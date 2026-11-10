import math
import time
from collections.abc import Callable, Mapping

import numpy as np
import torch as th

from memory_bench.utils.scene.state import ROBOT_POSITION_TOLERANCE


def _yaw(quaternion):
    from omnigibson.utils import transform_utils as T

    return float(T.quat2euler(th.as_tensor(quaternion, dtype=th.float32))[2])


def _angle_difference(target, current):
    return (target - current + math.pi) % (2 * math.pi) - math.pi


def _orientation_error(target, actual):
    target = th.as_tensor(target, dtype=th.float32).detach().cpu()
    actual = th.as_tensor(actual, dtype=th.float32).detach().cpu()
    direct = float(th.max(th.abs(target - actual)))
    flipped = float(th.max(th.abs(target + actual)))
    return min(direct, flipped)


def _gripper_command(robot, arm, joint_positions):
    """Map a desired finger position to JoyLo's smooth one-value gripper input."""
    indices = robot.gripper_control_idx[arm]
    position = joint_positions[indices].mean()
    low = robot.control_limits["position"][0][indices].mean()
    high = robot.control_limits["position"][1][indices].mean()
    if float(high - low) <= 1e-8:
        raise ValueError(f"R1Pro {arm} gripper has invalid position limits")
    return th.clamp(2.0 * (position - low) / (high - low) - 1.0, -1.0, 1.0)


def _base_action(robot, vx=0.0, vy=0.0, wz=0.0):
    action = th.zeros(robot.action_dim, dtype=th.float32)
    current = robot.get_joint_positions()
    action[robot.base_action_idx] = th.tensor([vx, vy, wz], dtype=th.float32)
    action[robot.trunk_action_idx] = current[robot.trunk_control_idx]
    for arm in robot.arm_names:
        action[robot.arm_action_idx[arm]] = current[robot.arm_control_idx[arm]]
        action[robot.gripper_action_idx[arm]] = _gripper_command(robot, arm, current)
    return action


def _grid_segment_clear(grid, traversability, start, end):
    """Check a swept base-center segment on the footprint-inflated grid, including cell corners."""
    start, end = np.asarray(start), np.asarray(end)
    count = max(1, math.ceil(float(np.max(np.abs(end - start))) / traversability.map_resolution * 2))
    points = np.linspace(start, end, count + 1)
    cells = points[:, ::-1] / traversability.map_resolution + traversability.map_size / 2.0
    if np.any(cells < 0) or np.any(cells >= np.asarray(grid.shape)):
        return False
    cells = np.floor(cells).astype(int)
    if not np.all(grid[cells[:, 0], cells[:, 1]]):
        return False
    # Diagonal transitions must not slip between two blocked side cells.
    before, after = cells[:-1], cells[1:]
    return bool(np.all(grid[before[:, 0], after[:, 1]]) and np.all(grid[after[:, 0], before[:, 1]]))


def _shortcut_route(points, grid, traversability):
    """Remove grid stair steps only when the whole replacement segment has clearance."""
    route = [points[0]]
    index = 0
    while index < len(points) - 1:
        for following in range(len(points) - 1, index, -1):
            if _grid_segment_clear(grid, traversability, points[index], points[following]):
                break
        else:
            raise RuntimeError("Navigation route contains a blocked segment")
        route.append(points[following])
        index = following
    return np.asarray(route)


def drive_to_pose(
    env,
    robot,
    target_pose: Mapping,
    step: Callable,
    *,
    linear_speed=0.25,
    angular_speed=0.45,
    max_steps_per_waypoint=600,
    collision_check=True,
    frame_count: Callable[[], int] | None = None,
    position_tolerance=ROBOT_POSITION_TOLERANCE,
    yaw_tolerance=0.05,
    linear_velocity_tolerance=0.03,
    angular_velocity_tolerance=0.05,
    world_guard: Callable[[], None] | None = None,
    lookahead_distance=0.25,
    linear_acceleration=0.4,
    angular_acceleration=1.0,
):
    """Plan over the current traversability map and execute the route in closed loop.

    The function only issues base velocity actions. It never sets a robot pose directly and
    never opens doors. `step` should submit an action to the live environment so the actions
    are recorded by its HDF5 collector. Speed limits use the same controller input units as
    teleoperation: the JoyLo R1Pro controller scales linear commands by 0.75 and yaw commands by 1.0.
    """
    from omnigibson.controllers.controller_view import ControllerView
    from omnigibson.utils.motion_planning_utils import astar, detect_robot_collision_in_sim

    if min(linear_speed, angular_speed, lookahead_distance, linear_acceleration, angular_acceleration) <= 0:
        raise ValueError("Navigation speeds, lookahead, and acceleration limits must be positive")

    planning_start = time.perf_counter()
    position = th.as_tensor(target_pose["position"], dtype=th.float32)
    orientation = th.as_tensor(target_pose["orientation"], dtype=th.float32)
    target_yaw = _yaw(orientation)
    current_position, current_orientation = robot.get_position_orientation()
    travel_yaw = _yaw(current_orientation)

    floors = robot.scene.trav_map.floor_heights
    if isinstance(floors, th.Tensor):
        floors = floors.detach().cpu().numpy()
    floor = int(np.argmin(np.abs(np.asarray(floors) - float(position[2]))))

    from memory_bench.utils.navigation.navigation_map import build_occupancy_map

    # Plan for the stowed robot; physical contacts remain checked on every recorded action.
    traversability = env.scene.trav_map
    grid, footprint = build_occupancy_map(env.scene, robot, floor)
    start = current_position[:2].detach().cpu().numpy()
    goal = position[:2].cpu().numpy()
    # The base only translates horizontally; reserve tolerance for the existing height offset.
    planar_tolerance = math.sqrt(max(0.0, position_tolerance ** 2 - float(position[2] - current_position[2]) ** 2))
    within_goal = float(np.linalg.norm(goal - start)) <= planar_tolerance
    source_cell = tuple(traversability.world_to_map(current_position[:2]).tolist())
    target_cell = tuple(traversability.world_to_map(position[:2]).tolist())
    if any(cell[axis] < 0 or cell[axis] >= grid.shape[axis] for cell in (source_cell, target_cell) for axis in (0, 1)):
        raise RuntimeError("Navigation endpoint is outside the traversability map")
    if grid[source_cell] == 0 or (not within_goal and grid[target_cell] == 0):
        raise RuntimeError(
            "Navigation endpoint is blocked on the base planning map: "
            f"source_free={bool(grid[source_cell])}, target_free={bool(grid[target_cell])}"
        )

    if within_goal or _grid_segment_clear(grid, traversability, start, goal):
        points = np.vstack((start, goal))
    else:
        path_cells = astar(grid, source_cell, target_cell, allow_corner_cutting=False)
        if path_cells is None:
            raise RuntimeError(f"No traversable route to target pose {goal.tolist()}")
        # Retain endpoint cell centers so exact poses cannot cut blocked neighbors.
        path = traversability.map_to_world(path_cells.to(dtype=th.float64) + 0.5)
        points = np.vstack((start, path.detach().cpu().numpy(), goal))
    points = points[np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-8]]
    route = points if within_goal else _shortcut_route(points, grid, traversability)
    planning_seconds = time.perf_counter() - planning_start
    print(
        f"Planned navigation route with {len(route)} waypoints at {traversability.map_resolution}m map resolution",
        flush=True,
    )

    start_actions = frame_count() if frame_count is not None else None

    def checked_step(action):
        step(action)
        if world_guard is not None:
            world_guard()
        if collision_check and detect_robot_collision_in_sim(robot):
            raise RuntimeError("Robot collision detected while following a bridge route")

    # Only real corners remain; straight-run samples never introduce stops.
    distances = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(route, axis=0), axis=1))]
    dt = 1.0 / env.config["env"]["action_frequency"]
    # Convert measured velocities to the controller's input units.
    base_scale = ControllerView.preprocess_command(robot.controllers["base"][0], th.ones(3)).cpu().numpy()
    linear_scale, angular_scale = float(base_scale[0]), float(base_scale[2])
    speed = max(0.0, float(robot.get_linear_velocity()[:2].norm())) / linear_scale
    angular_velocity = float(robot.get_angular_velocity()[2]) / angular_scale
    previous_command_yaw = travel_yaw
    waypoint_index = min(1, len(route) - 1)
    final_align = within_goal
    turning = True
    hold_action = _base_action(robot)
    execution_start = time.perf_counter()
    steps = 0
    executed_length = 0.0
    previous_xy = start
    peak_linear_velocity = 0.0
    peak_angular_velocity = 0.0
    last_progress_step = 0
    checkpoint_distance = float(np.linalg.norm(route[waypoint_index] - start))
    checkpoint_yaw_error = math.pi
    # Stop inside the acceptance radius so turning drift cannot repeatedly switch phases.
    approach_tolerance = min(0.02, planar_tolerance * 2 / 3)
    step_budget = max_steps_per_waypoint * max(1, len(route), math.ceil(distances[-1]))
    for step_index in range(step_budget):
        current_position, current_orientation = robot.get_position_orientation()
        xy = current_position[:2].detach().cpu().numpy()
        yaw = _yaw(current_orientation)
        goal_distance = float(np.linalg.norm(goal - xy))
        measured_velocity = robot.get_linear_velocity().detach().cpu().numpy()
        measured_rotation = robot.get_angular_velocity().detach().cpu().numpy()
        actual_speed = float(np.linalg.norm(measured_velocity))
        actual_rotation = float(np.linalg.norm(measured_rotation))
        peak_linear_velocity = max(peak_linear_velocity, actual_speed)
        peak_angular_velocity = max(peak_angular_velocity, actual_rotation)
        executed_length += float(np.linalg.norm(xy - previous_xy))
        previous_xy = xy

        delta = route[waypoint_index] - xy
        distance = float(np.linalg.norm(delta))
        gentle_corner = False
        if waypoint_index < len(route) - 1:
            next_delta = route[waypoint_index + 1] - xy
            next_yaw = math.atan2(next_delta[1], next_delta[0])
            gentle_corner = abs(_angle_difference(next_yaw, yaw)) <= 0.2
        if (
            not final_align and distance <= approach_tolerance
            and (gentle_corner or (speed < 1e-8 and actual_speed <= linear_velocity_tolerance))
        ):
            if waypoint_index == len(route) - 1:
                final_align = True
            else:
                waypoint_index += 1
                delta = route[waypoint_index] - xy
                distance = float(np.linalg.norm(delta))
            turning = not gentle_corner
            if not final_align:
                checkpoint_distance = distance
                checkpoint_yaw_error = math.pi
                last_progress_step = step_index
        elif final_align and goal_distance > planar_tolerance:
            # Re-approach facing the goal if a real disturbance moves the base away.
            # There is no lateral or reverse correction, including at the endpoint.
            final_align = False
            turning = True

        desired_yaw = target_yaw if final_align else math.atan2(delta[1], delta[0])
        yaw_error = _angle_difference(desired_yaw, yaw)
        if (
            final_align and goal_distance <= planar_tolerance and abs(yaw_error) <= yaw_tolerance
            and actual_speed <= linear_velocity_tolerance and actual_rotation <= angular_velocity_tolerance
            and speed < 1e-8 and abs(angular_velocity) < 1e-8
        ):
            break

        if not turning and abs(yaw_error) > 0.2:
            turning = True
        if (
            turning and not final_align and abs(yaw_error) <= yaw_tolerance
            and actual_rotation <= angular_velocity_tolerance and abs(angular_velocity) < 1e-8
        ):
            turning = False

        desired_speed = 0.0
        desired_rotation = float(np.clip(1.5 * yaw_error, -angular_speed, angular_speed))
        if turning:
            if speed > 1e-8 or actual_speed > linear_velocity_tolerance or abs(yaw_error) <= yaw_tolerance:
                desired_rotation = 0.0
        else:
            desired_speed = min(linear_speed, math.sqrt(2 * linear_acceleration * distance))
            if distance <= lookahead_distance:
                desired_speed = min(desired_speed, distance * 1.2)
            if distance <= approach_tolerance:
                desired_speed = 0.0
                desired_rotation = 0.0
            desired_speed *= max(0.0, math.cos(yaw_error))

        # Keep independent best errors; revisiting a phase does not erase stalled progress.
        progress_yaw_error = (
            abs(_angle_difference(target_yaw, yaw)) if waypoint_index == len(route) - 1 else abs(yaw_error)
        )
        distance_progress = checkpoint_distance - distance >= approach_tolerance / 4
        yaw_progress = turning and checkpoint_yaw_error - progress_yaw_error >= yaw_tolerance / 4
        if distance_progress:
            checkpoint_distance = distance
        if yaw_progress:
            checkpoint_yaw_error = progress_yaw_error
        if distance_progress or yaw_progress:
            last_progress_step = step_index
        elif step_index - last_progress_step >= max_steps_per_waypoint:
            raise RuntimeError("Navigation stopped making progress toward the target pose")

        # Limit world-frame input changes without introducing a sideways command.
        heading_change = _angle_difference(yaw, previous_command_yaw)
        along = speed * math.cos(heading_change)
        lateral = speed * math.sin(heading_change)
        change_budget = math.sqrt(max(0.0, (linear_acceleration * dt) ** 2 - lateral ** 2))
        speed = float(np.clip(desired_speed, max(0.0, along - change_budget), max(0.0, along + change_budget)))
        angular_velocity += float(np.clip(
            desired_rotation - angular_velocity, -angular_acceleration * dt, angular_acceleration * dt,
        ))

        action = hold_action.clone()
        # Match teleoperation: let the base controller apply its configured scaling.
        action[robot.base_action_idx] = th.tensor([speed, 0.0, angular_velocity], dtype=action.dtype)
        checked_step(action)
        previous_command_yaw = yaw
        steps += 1
    else:
        raise RuntimeError("R1Pro failed to follow and settle at the navigation target")

    # Check the actual final pose before reporting success.
    final_position, final_orientation = robot.get_position_orientation()
    final_position = th.as_tensor(final_position).cpu().numpy()
    position_error = float(np.linalg.norm(final_position[:3] - position[:3].cpu().numpy()))
    yaw_error = abs(_angle_difference(target_yaw, _yaw(final_orientation)))
    orientation_error = _orientation_error(orientation, final_orientation)
    if position_error > position_tolerance or yaw_error > yaw_tolerance or orientation_error > yaw_tolerance:
        raise RuntimeError(
            f"R1Pro missed target pose: position error={position_error:.3f}m, "
            f"yaw error={yaw_error:.3f}rad, orientation error={orientation_error:.3f}"
        )

    result = {
        "planned_waypoints": [waypoint.tolist() for waypoint in route],
        "map_resolution": float(traversability.map_resolution),
        "travel_yaw": travel_yaw,
        "forward_only": True,
        "footprint": footprint,
        "position_error": position_error,
        "yaw_error": yaw_error,
        "orientation_error": orientation_error,
        "route_length": float(distances[-1]),
        "executed_length": executed_length,
        "steps": steps,
        "duration_seconds": steps * dt,
        "planning_seconds": planning_seconds,
        "execution_seconds": time.perf_counter() - execution_start,
        "peak_linear_velocity": peak_linear_velocity,
        "peak_angular_velocity": peak_angular_velocity,
    }
    if start_actions is not None:
        result["frames"] = [start_actions, frame_count()]
    return result


def move_joints(
    robot,
    step: Callable,
    target_positions,
    *,
    max_joint_delta=0.025,
    joint_tolerance=0.03,
    max_steps_per_waypoint=60,
    control_grippers=False,
    joint_velocity_tolerance=0.05,
    linear_velocity_tolerance=0.03,
    angular_velocity_tolerance=0.05,
    world_guard: Callable[[], None] | None = None,
):
    """Move through checked joint targets and settle at the final target."""
    from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim

    current = robot.get_joint_positions().clone()
    target = th.as_tensor(target_positions, dtype=current.dtype, device=current.device)
    indices = th.cat([robot.trunk_control_idx, *[robot.arm_control_idx[arm] for arm in robot.arm_names]])
    moving_indices = th.cat([indices, *[robot.gripper_control_idx[arm] for arm in robot.arm_names]])
    if control_grippers:
        indices = moving_indices
    max_delta = float(th.max(th.abs(target[indices] - current[indices])))
    n_steps = max(1, math.ceil(max_delta / max_joint_delta))

    for index, fraction in enumerate(th.linspace(1.0 / n_steps, 1.0, n_steps, device=current.device)):
        waypoint = current + fraction * (target - current)
        action = th.zeros(robot.action_dim, dtype=th.float32)
        action[robot.base_action_idx] = 0.0
        action[robot.trunk_action_idx] = waypoint[robot.trunk_control_idx]
        for arm in robot.arm_names:
            action[robot.arm_action_idx[arm]] = waypoint[robot.arm_control_idx[arm]]
            gripper_target = waypoint if control_grippers else current
            action[robot.gripper_action_idx[arm]] = _gripper_command(robot, arm, gripper_target)
        for _ in range(max_steps_per_waypoint):
            step(action)
            if world_guard is not None:
                world_guard()
            if detect_robot_collision_in_sim(robot):
                raise RuntimeError("Robot collision detected while stowing or extending the arms")
            error = th.max(th.abs(robot.get_joint_positions()[indices] - waypoint[indices]))
            settled = index < n_steps - 1 or (
                float(th.max(th.abs(robot.get_joint_velocities()[moving_indices]))) <= joint_velocity_tolerance
                and float(robot.get_linear_velocity().norm()) <= linear_velocity_tolerance
                and float(robot.get_angular_velocity().norm()) <= angular_velocity_tolerance
            )
            if float(error) <= joint_tolerance and settled:
                break
        else:
            raise RuntimeError("R1Pro did not reach and settle at a stow or ready joint target")
