import math
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
    cells = (points[:, ::-1] / traversability.map_resolution + traversability.map_size / 2.0).astype(int)
    if np.any(cells < 0) or np.any(cells >= np.asarray(grid.shape)):
        return False
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
    from omnigibson.utils.motion_planning_utils import astar, detect_robot_collision_in_sim

    if min(linear_speed, angular_speed, lookahead_distance, linear_acceleration, angular_acceleration) <= 0:
        raise ValueError("Navigation speeds, lookahead, and acceleration limits must be positive")

    position = th.as_tensor(target_pose["position"], dtype=th.float32)
    orientation = th.as_tensor(target_pose["orientation"], dtype=th.float32)
    target_yaw = _yaw(orientation)
    current_position, current_orientation = robot.get_position_orientation()
    travel_yaw = _yaw(current_orientation)

    floors = robot.scene.trav_map.floor_heights
    if isinstance(floors, th.Tensor):
        floors = floors.detach().cpu().numpy()
    floor = int(np.argmin(np.abs(np.asarray(floors) - float(position[2]))))

    from memory_bench.utils.navigation_map import fixed_heading_floor_map

    # Reserve clearance for the stowed robot in the current scene's occupancy grid.
    traversability = env.scene.trav_map
    grid, footprint = fixed_heading_floor_map(env.scene, robot, floor, position_tolerance)
    source_cell = tuple(traversability.world_to_map(current_position[:2]).tolist())
    target_cell = tuple(traversability.world_to_map(position[:2]).tolist())
    if grid[source_cell] == 0 or grid[target_cell] == 0:
        raise RuntimeError(
            "Bridge endpoint lacks clearance for the stowed robot and tracking margin: "
            f"source_free={bool(grid[source_cell])}, target_free={bool(grid[target_cell])}"
        )

    # Plan a grid route with A*; blocked endpoints and unreachable targets fail immediately.
    path_cells = astar(grid, source_cell, target_cell, allow_corner_cutting=False)
    if path_cells is None:
        raise RuntimeError(f"No traversable route to target pose {position[:2].tolist()}")

    # Convert to world coordinates and merge straight runs while preserving turns.
    path = traversability.map_to_world(path_cells)
    points = path.detach().cpu().numpy() if isinstance(path, th.Tensor) else np.asarray(path)
    goal = position[:2].cpu().numpy()
    points = np.vstack((current_position[:2].detach().cpu().numpy(), points[1:-1], goal))
    points = points[np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-8]]
    route = _sample_waypoints(points)
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

    # Follow a moving lookahead target; intermediate points never trigger a stop or yaw phase.
    grid = grid.cpu().numpy()
    lengths = np.linalg.norm(np.diff(route, axis=0), axis=1)
    distances = np.r_[0.0, np.cumsum(lengths)]
    directions = np.diff(route, axis=0) / lengths[:, None]
    dt = 1.0 / env.config["env"]["action_frequency"]
    velocity = robot.get_linear_velocity()[:2].detach().cpu().numpy().astype(float)
    angular_velocity = float(robot.get_angular_velocity()[2])
    progress = 0.0
    segment_index = 0
    aligning = False
    for _ in range(max_steps_per_waypoint * max(1, len(route))):
        current_position, current_orientation = robot.get_position_orientation()
        xy = current_position[:2].detach().cpu().numpy()
        yaw = _yaw(current_orientation)
        goal_distance = float(np.linalg.norm(goal - xy))
        actual_speed = float(robot.get_linear_velocity().norm())
        actual_rotation = float(robot.get_angular_velocity().norm())
        if goal_distance <= position_tolerance / 2 and actual_speed <= linear_velocity_tolerance:
            aligning = True
        yaw_error = _angle_difference(target_yaw if aligning else travel_yaw, yaw)
        if (
            aligning and goal_distance <= position_tolerance and abs(yaw_error) <= yaw_tolerance
            and actual_speed <= linear_velocity_tolerance and actual_rotation <= angular_velocity_tolerance
        ):
            break

        desired = np.zeros(2)
        if not aligning and len(route) > 1:
            # Advance locally along the polyline, without jumping across nearby loops or walls.
            best_error = float("inf")
            for index in range(segment_index, min(segment_index + 2, len(lengths))):
                offset = float(np.clip(np.dot(xy - route[index], directions[index]), 0, lengths[index]))
                projected = route[index] + directions[index] * offset
                error = float(np.linalg.norm(projected - xy))
                if error < best_error and _grid_segment_clear(grid, traversability, xy, projected):
                    best_error = error
                    progress = max(progress, distances[index] + offset)
                    segment_index = index

            # Shorten the lookahead if its chord would cut through an obstacle at a turn.
            lookahead = min(lookahead_distance, distances[-1] - progress)
            while True:
                target_distance = min(progress + lookahead, distances[-1])
                target = np.asarray([np.interp(target_distance, distances, route[:, axis]) for axis in (0, 1)])
                if _grid_segment_clear(grid, traversability, xy, target):
                    break
                lookahead *= 0.5
                if lookahead < traversability.map_resolution / 2:
                    raise RuntimeError("No collision-free lookahead along the navigation route")
            delta = target - xy
            remaining = max(goal_distance, distances[-1] - progress)
            speed = min(linear_speed, math.sqrt(2 * linear_acceleration * remaining))
            # Brake before sharp turns, rather than before every sampled point.
            for index in range(max(0, segment_index - 1), len(directions) - 1):
                corner_distance = distances[index + 1] - progress
                if corner_distance < -position_tolerance:
                    continue
                if corner_distance > lookahead_distance + linear_speed ** 2 / (2 * linear_acceleration):
                    break
                cosine = float(np.clip(np.dot(directions[index], directions[index + 1]), -1, 1))
                corner_speed = linear_speed * max(0.1, (1 + cosine) / 2)
                braking_distance = max(0, corner_distance - lookahead_distance)
                speed = min(speed, math.sqrt(corner_speed ** 2 + 2 * linear_acceleration * braking_distance))
            if distances[-1] - progress <= lookahead_distance:
                speed = min(speed, goal_distance * 1.2)
            norm = float(np.linalg.norm(delta))
            if norm > 1e-8:
                desired = delta / norm * speed

        # Limit the entire world-frame velocity vector, including changes of travel direction.
        # Check the next step plus braking distance before issuing a command near obstacles.
        for scale in (1.0, 0.5, 0.25, 0.0):
            change = desired * scale - velocity
            change *= min(1.0, linear_acceleration * dt / max(float(np.linalg.norm(change)), 1e-8))
            candidate = velocity + change
            stopping_time = float(np.linalg.norm(candidate)) / linear_acceleration
            stopping_point = xy + candidate * (dt + stopping_time / 2)
            if _grid_segment_clear(grid, traversability, xy, stopping_point):
                velocity = candidate
                break
        else:
            raise RuntimeError("No collision-free braking motion along the navigation route")
        desired_rotation = float(np.clip(1.5 * yaw_error, -angular_speed, angular_speed))
        if aligning and abs(yaw_error) <= yaw_tolerance:
            desired_rotation = 0.0
        angular_change = np.clip(
            desired_rotation - angular_velocity, -angular_acceleration * dt, angular_acceleration * dt,
        )
        angular_velocity += float(angular_change)
        vx = math.cos(yaw) * velocity[0] + math.sin(yaw) * velocity[1]
        vy = -math.sin(yaw) * velocity[0] + math.cos(yaw) * velocity[1]
        checked_step(_base_action(robot, vx=vx, vy=vy, wz=angular_velocity))
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
        "footprint": footprint,
        "position_error": position_error,
        "yaw_error": yaw_error,
        "orientation_error": orientation_error,
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
