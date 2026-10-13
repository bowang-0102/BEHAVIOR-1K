"""Closed-loop R1Pro motion used for collection handoffs and synthesized bridges."""

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


def _move_base(
    robot,
    step: Callable,
    x,
    y,
    target_yaw,
    *,
    linear_speed,
    angular_speed,
    max_steps,
    position_tolerance,
    yaw_tolerance,
):
    """Follow a waypoint with velocity actions in robot coordinates."""
    for _ in range(max_steps):
        position, orientation = robot.get_position_orientation()
        position = th.as_tensor(position).cpu().numpy()
        yaw = _yaw(orientation)
        dx, dy = x - position[0], y - position[1]
        distance = math.hypot(dx, dy)
        if distance <= position_tolerance:
            break

        heading = math.atan2(dy, dx)
        yaw_error = _angle_difference(heading, yaw)
        if abs(yaw_error) > yaw_tolerance:
            wz = float(np.clip(1.5 * yaw_error, -angular_speed, angular_speed))
            step(_base_action(robot, wz=wz))
            continue

        # HolonomicBaseJointController takes base-frame velocity commands.
        speed = min(linear_speed, max(0.02, distance * 0.8))
        vx_world = speed * math.cos(heading)
        vy_world = speed * math.sin(heading)
        vx = math.cos(yaw) * vx_world + math.sin(yaw) * vy_world
        vy = -math.sin(yaw) * vx_world + math.cos(yaw) * vy_world
        step(_base_action(robot, vx=vx, vy=vy))
    else:
        raise RuntimeError(f"R1Pro did not reach navigation waypoint {(x, y)}")

    for _ in range(max_steps):
        _, orientation = robot.get_position_orientation()
        yaw_error = _angle_difference(target_yaw, _yaw(orientation))
        if abs(yaw_error) <= yaw_tolerance:
            break
        wz = float(np.clip(1.5 * yaw_error, -angular_speed, angular_speed))
        step(_base_action(robot, wz=wz))
    else:
        raise RuntimeError("R1Pro did not reach target yaw")


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
):
    """Plan over the current traversability map and execute the route in closed loop.

    The function only issues base velocity actions. It never sets a robot pose directly and
    never opens doors. `step` should submit an action to the live environment so the actions
    are recorded by its HDF5 collector.
    """
    from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim

    position = th.as_tensor(target_pose["position"], dtype=th.float32)
    orientation = th.as_tensor(target_pose["orientation"], dtype=th.float32)
    target_yaw = _yaw(orientation)
    current_position, _ = robot.get_position_orientation()
    floors = robot.scene.trav_map.floor_heights
    if isinstance(floors, th.Tensor):
        floors = floors.detach().cpu().numpy()
    floor = int(np.argmin(np.abs(np.asarray(floors) - float(position[2]))))
    from memory_bench.utils.navigation_map import current_floor_map

    traversability = env.scene.trav_map
    original_map = traversability.floor_map[floor]
    traversability.floor_map[floor] = current_floor_map(env.scene, robot, floor)
    try:
        path, _ = env.scene.get_shortest_path(
            floor=floor,
            source_world=th.as_tensor(current_position[:2], dtype=th.float32),
            target_world=position[:2],
            entire_path=True,
            robot=robot,
        )
    finally:
        traversability.floor_map[floor] = original_map
    if path is None:
        raise RuntimeError(f"No traversable route to target pose {position[:2].tolist()}")

    points = path.detach().cpu().numpy() if isinstance(path, th.Tensor) else np.asarray(path)
    route = list(points)
    if len(route) == 0 or np.linalg.norm(route[-1] - position[:2].cpu().numpy()) > position_tolerance:
        route.append(position[:2].cpu().numpy())

    start_actions = frame_count() if frame_count is not None else None

    def checked_step(action):
        step(action)
        if world_guard is not None:
            world_guard()
        if collision_check and detect_robot_collision_in_sim(robot):
            raise RuntimeError("Robot collision detected while following a bridge route")

    for index, waypoint in enumerate(route):
        yaw = target_yaw
        if index + 1 < len(route):
            delta = route[index + 1] - waypoint
            if np.linalg.norm(delta) > 0.02:
                yaw = math.atan2(delta[1], delta[0])
        _move_base(
            robot,
            checked_step,
            float(waypoint[0]),
            float(waypoint[1]),
            yaw,
            linear_speed=linear_speed,
            angular_speed=angular_speed,
            max_steps=max_steps_per_waypoint,
            position_tolerance=position_tolerance,
            yaw_tolerance=yaw_tolerance,
        )

    # Align to the exact requested final orientation after reaching the final position.
    _move_base(
        robot,
        checked_step,
        float(position[0]),
        float(position[1]),
        target_yaw,
        linear_speed=linear_speed,
        angular_speed=angular_speed,
        max_steps=max_steps_per_waypoint,
        position_tolerance=position_tolerance,
        yaw_tolerance=yaw_tolerance,
    )

    for _ in range(max_steps_per_waypoint):
        linear_speed_now = float(robot.get_linear_velocity().norm())
        angular_speed_now = float(robot.get_angular_velocity().norm())
        if linear_speed_now <= linear_velocity_tolerance and angular_speed_now <= angular_velocity_tolerance:
            break
        checked_step(_base_action(robot))
    else:
        raise RuntimeError("R1Pro failed to stop at the navigation target")

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

    if start_actions is None:
        return {"position_error": position_error, "yaw_error": yaw_error, "orientation_error": orientation_error}
    return {
        "position_error": position_error,
        "yaw_error": yaw_error,
        "orientation_error": orientation_error,
        "frames": [start_actions, frame_count()],
    }


def move_joints(
    robot,
    step: Callable,
    target_positions,
    *,
    max_joint_delta=0.025,
    joint_tolerance=0.03,
    max_steps_per_waypoint=60,
    control_grippers=False,
    world_guard: Callable[[], None] | None = None,
):
    """Move the R1Pro trunk and arms through checked position-controller targets."""
    from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim

    current = robot.get_joint_positions().clone()
    target = th.as_tensor(target_positions, dtype=current.dtype, device=current.device)
    indices = th.cat([robot.trunk_control_idx, *[robot.arm_control_idx[arm] for arm in robot.arm_names]])
    if control_grippers:
        indices = th.cat([indices, *[robot.gripper_control_idx[arm] for arm in robot.arm_names]])
    max_delta = float(th.max(th.abs(target[indices] - current[indices])))
    n_steps = max(1, math.ceil(max_delta / max_joint_delta))

    for fraction in th.linspace(1.0 / n_steps, 1.0, n_steps, device=current.device):
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
            if float(error) <= joint_tolerance:
                break
        else:
            raise RuntimeError("R1Pro did not reach a stow or ready joint target")
