import math
import time
from collections.abc import Callable, Mapping

import numpy as np
import torch as th


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
    from omnigibson.controllers.controller_view import ControllerView

    action = th.zeros(robot.action_dim, dtype=th.float32)
    current = robot.get_joint_positions()
    action[robot.base_action_idx] = ControllerView.reverse_preprocess_command(
        robot.controllers["base"][0], th.tensor([vx, vy, wz], dtype=th.float32),
    )
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


def _sample_waypoints(points, max_spacing=1.0):
    """Merge straight runs while retaining supplied turns and subdividing long segments."""
    points = np.asarray(points, dtype=float)
    if len(points) < 2:
        return points.copy()
    directions = np.diff(points, axis=0)
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    corners = [points[0]]
    for index in range(1, len(points) - 1):
        if not np.allclose(directions[index - 1], directions[index], atol=1e-4, rtol=0):
            corners.append(points[index])
    corners.append(points[-1])
    sampled = [corners[0]]
    for start, end in zip(corners, corners[1:]):
        count = max(1, math.ceil(float(np.linalg.norm(end - start)) / max_spacing))
        for fraction in np.arange(1, count) / count:
            sampled.append(start + (end - start) * fraction)
        sampled.append(end)
    return np.asarray(sampled)


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
    position_tolerance=0.02,
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
    are recorded by its HDF5 collector.
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

    from memory_bench.utils.navigation_map import (
        braking_motion_clear,
        forward_navigation_map,
        forward_route_clear,
        rotation_clear,
    )

    # Reserve clearance for the stowed robot in the current scene's occupancy grid.
    traversability = env.scene.trav_map
    grid, occupancy, footprint = forward_navigation_map(env.scene, robot, floor, position_tolerance)
    source_cell = tuple(traversability.world_to_map(current_position[:2]).tolist())
    target_cell = tuple(traversability.world_to_map(position[:2]).tolist())
    if any(cell[axis] < 0 or cell[axis] >= grid.shape[axis] for cell in (source_cell, target_cell) for axis in (0, 1)):
        raise RuntimeError("Navigation endpoint is outside the traversability map")
    if grid[source_cell] == 0 or grid[target_cell] == 0:
        raise RuntimeError(
            "Bridge endpoint lacks clearance for the stowed robot and tracking margin: "
            f"source_free={bool(grid[source_cell])}, target_free={bool(grid[target_cell])}"
        )

    start = current_position[:2].detach().cpu().numpy()
    goal = position[:2].cpu().numpy()
    if _grid_segment_clear(grid, traversability, start, goal):
        points = np.vstack((start, goal))
    else:
        path_cells = astar(grid, source_cell, target_cell, allow_corner_cutting=False)
        if path_cells is None:
            raise RuntimeError(f"No traversable route to target pose {position[:2].tolist()}")

        # Keep endpoint cell centers too: connecting exact poses directly to the next
        # cell can cross a blocked neighbor even when the A* cells are all free.
        path = traversability.map_to_world(path_cells.to(dtype=th.float64) + 0.5)
        points = np.vstack((start, path.detach().cpu().numpy(), goal))
    points = points[np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-8]]
    points = _shortcut_route(points, grid, traversability)
    route = _sample_waypoints(points, max_spacing=float("inf"))
    outline = np.asarray(footprint["outline_xy"])
    if not forward_route_clear(occupancy, traversability, outline, route, travel_yaw):
        raise RuntimeError("Candidate route lacks clearance for forward travel or a corner turn")
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
    speed = max(0.0, float(robot.get_linear_velocity()[:2].norm()))
    angular_velocity = float(robot.get_angular_velocity()[2])
    previous_command_yaw = travel_yaw
    waypoint_index = min(1, len(route) - 1)
    final_align = len(route) == 1
    turning = True
    turn_checked = False
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
            not final_align and distance <= position_tolerance / 2
            and (gentle_corner or (speed < 1e-8 and actual_speed <= linear_velocity_tolerance))
        ):
            if waypoint_index == len(route) - 1:
                final_align = True
            else:
                waypoint_index += 1
                delta = route[waypoint_index] - xy
                distance = float(np.linalg.norm(delta))
            turning = not gentle_corner
            turn_checked = False
            checkpoint_distance = distance
            checkpoint_yaw_error = math.pi
            last_progress_step = step_index
        elif final_align and goal_distance > position_tolerance:
            # Re-approach facing the goal if a real disturbance moves the base away.
            # There is no lateral or reverse correction, including at the endpoint.
            final_align = False
            turning = True
            turn_checked = False
            checkpoint_distance = distance
            checkpoint_yaw_error = math.pi
            last_progress_step = step_index

        desired_yaw = target_yaw if final_align else math.atan2(delta[1], delta[0])
        yaw_error = _angle_difference(desired_yaw, yaw)
        if (
            final_align and goal_distance <= position_tolerance and abs(yaw_error) <= yaw_tolerance
            and actual_speed <= linear_velocity_tolerance and actual_rotation <= angular_velocity_tolerance
            and speed < 1e-8 and abs(angular_velocity) < 1e-8
        ):
            break

        if not turning and abs(yaw_error) > 0.2:
            turning = True
            turn_checked = False
        if (
            turning and not final_align and abs(yaw_error) <= yaw_tolerance / 2
            and actual_rotation <= angular_velocity_tolerance and abs(angular_velocity) < 1e-8
        ):
            turning = False

        desired_speed = 0.0
        desired_rotation = float(np.clip(1.5 * yaw_error, -angular_speed, angular_speed))
        if turning:
            if speed > 1e-8 or actual_speed > linear_velocity_tolerance:
                desired_rotation = 0.0
            else:
                if not turn_checked:
                    if not rotation_clear(occupancy, traversability, outline, xy, yaw, desired_yaw):
                        raise RuntimeError(f"Insufficient clearance to turn at {xy.tolist()}")
                    turn_checked = True
                if abs(yaw_error) <= yaw_tolerance / 2:
                    desired_rotation = 0.0
        else:
            desired_speed = min(linear_speed, math.sqrt(2 * linear_acceleration * distance))
            if distance <= lookahead_distance:
                desired_speed = min(desired_speed, distance * 1.2)
            if distance <= position_tolerance / 2:
                desired_speed = 0.0
                desired_rotation = 0.0
            desired_speed *= max(0.0, math.cos(yaw_error))

        if (
            checkpoint_distance - distance >= position_tolerance / 4
            or (turning and checkpoint_yaw_error - abs(yaw_error) >= yaw_tolerance / 4)
        ):
            checkpoint_distance = distance
            checkpoint_yaw_error = abs(yaw_error)
            last_progress_step = step_index
        elif step_index - last_progress_step >= max_steps_per_waypoint:
            raise RuntimeError("Navigation stopped making progress toward the target pose")

        # A lower command does not instantly remove physical momentum.
        measured_speed = float(np.linalg.norm(measured_velocity[:2]))
        measured_heading = math.atan2(measured_velocity[1], measured_velocity[0])
        if not braking_motion_clear(
            occupancy, traversability, outline, xy, yaw, measured_speed, float(measured_rotation[2]),
            dt, linear_acceleration, angular_acceleration, velocity_yaw=measured_heading,
        ):
            raise RuntimeError("No collision-free braking motion at the robot's measured velocity")

        # Limit world-frame acceleration without introducing a sideways command.
        heading_change = _angle_difference(yaw, previous_command_yaw)
        along = speed * math.cos(heading_change)
        lateral = speed * math.sin(heading_change)
        change_budget = math.sqrt(max(0.0, (linear_acceleration * dt) ** 2 - lateral ** 2))
        for scale in (1.0, 0.5, 0.0):
            candidate_speed = float(np.clip(
                desired_speed * scale, max(0.0, along - change_budget), max(0.0, along + change_budget),
            ))
            candidate_rotation = angular_velocity + float(np.clip(
                desired_rotation * scale - angular_velocity, -angular_acceleration * dt, angular_acceleration * dt,
            ))
            if braking_motion_clear(
                occupancy, traversability, outline, xy, yaw, candidate_speed, candidate_rotation,
                dt, linear_acceleration, angular_acceleration,
            ):
                speed, angular_velocity = candidate_speed, candidate_rotation
                break
        else:
            raise RuntimeError("No collision-free braking motion along the navigation route")

        action = hold_action.clone()
        # The live controller may normalize inputs; the navigation commands stay in physical units.
        action[robot.base_action_idx] = ControllerView.reverse_preprocess_command(
            robot.controllers["base"][0], th.tensor([speed, 0.0, angular_velocity], dtype=action.dtype),
        )
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
