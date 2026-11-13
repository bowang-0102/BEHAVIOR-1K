from copy import deepcopy

from memory_bench.utils.scene.light_utils import sync_light_visibility_from_toggles
from memory_bench.utils.scene.state import (
    assert_nonrobot_world_preserved,
    merge_scene_assets,
    robot_metadata,
    scene_objects,
    scene_snapshot,
)


def restore_robot_start(server, initial_scene, previous_world):
    """Restore only the robot and audit the world during unrecorded settling steps."""
    import omnigibson as og
    from omnigibson.utils.python_utils import recursively_convert_to_torch

    from memory_bench.utils.navigation.navigation import _base_action

    wrapper = server.env
    robot = server.robot
    require_empty_hands(robot)
    initial_state = deepcopy(scene_objects(initial_scene)[robot.name])
    robot.load_state(recursively_convert_to_torch(initial_state), serialized=False)
    # Zero velocities and wake the restored articulation before stepping physics.
    robot.keep_still()
    og.sim.physics_sim_view.update_articulations_kinematic()
    og.sim.sync_physx_to_fabric()
    og.sim._refresh_state_caches(dt=0.0)

    # The previous file is already closed. Settling steps validate the transfer;
    # the next file receives its own initial state only when the operator starts.
    hold_action = _base_action(robot)
    for _ in range(60):
        wrapper.step(hold_action)
        assert_nonrobot_world_preserved(previous_world, scene_snapshot(wrapper.scene), robot.name)
        require_collision_free(robot)
        require_empty_hands(robot)
        if is_stable(robot):
            break
    else:
        raise RuntimeError("Robot did not settle after restoring its initial state")
    require_entry_pose(robot, initial_scene["robot"])
    server.sync_session_control_state()


def restore_instance_start(server, entry):
    """Load the sampled base pose and establish a start using the current reset joints."""
    import omnigibson as og

    # Prepared worlds omit the robot; keep the live teleop articulation when restoring.
    initial = merge_scene_assets(entry, server.env.scene.save(as_dict=True), (server.robot.name,))
    server.env.scene.restore(initial)
    sync_light_visibility_from_toggles(server.env.scene)
    # Empty-handed entries do not serialize a pending assisted-grasp release window.
    for arm in server.robot.arm_names:
        if server.robot._ag_release_counter[arm] is not None:
            server.robot._ag_obj_in_hand[arm] = None
            server.robot._ag_release_counter[arm] = None
    server.env._optimize_sim_for_data_collection(viewport_camera_path=og.sim.viewer_camera.active_camera_path)
    server.robot.reset()
    server.robot.set_position_orientation(entry["robot"]["position"], entry["robot"]["orientation"])
    server.robot.keep_still()
    start = scene_snapshot(server.env.scene)
    restore_robot_start(server, start, entry)


def ready_command_is_synced(server, command, joint_tolerance=0.02, *, resume_existing=False):
    """Sync arms and neutral inputs; a resume retains pending trunk/gripper goals."""
    import torch as th

    robot = server.robot
    joint_positions = robot.get_joint_positions()
    if float(th.max(th.abs(command["base"]))) > 1e-4:
        return False
    if float(th.max(th.abs(command["trunk"]))) > 1e-4:
        return False

    generated_targets = server.session_neutral_targets(command)
    trunk_error = float(th.max(th.abs(generated_targets["trunk"] - joint_positions[robot.trunk_control_idx])))
    if not resume_existing and trunk_error > joint_tolerance:
        return False
    for arm in robot.arm_names:
        arm_error = float(th.max(th.abs(generated_targets[arm] - joint_positions[robot.arm_control_idx[arm]])))
        if arm_error > joint_tolerance:
            return False
        if abs(float(command[f"{arm}_gripper"][0]) - server._grasp_action[arm]) > 1e-6:
            return False
        # Pausing freezes physics before these goals necessarily finish. Resuming
        # continues the same goals; waiting for their physical completion would
        # deadlock while the ready handshake prevents simulation stepping.
        if not resume_existing:
            gripper_error = float(
                th.max(th.abs(generated_targets[f"{arm}_gripper"] - joint_positions[robot.gripper_control_idx[arm]]))
            )
            if gripper_error > joint_tolerance:
                return False
    return resume_existing or all(obj is None for obj in robot._ag_obj_in_hand.values())


def require_entry_pose(robot, target, position_tolerance=0.02, yaw_tolerance=0.05, joint_tolerance=0.02):
    """Check both the base pose and controlled joints against the frozen start."""
    import torch as th

    from memory_bench.utils.navigation.navigation import _angle_difference, _orientation_error, _yaw

    position, orientation = robot.get_position_orientation()
    position = th.as_tensor(position).cpu().numpy()
    target_position = th.as_tensor(target["position"]).cpu().numpy()
    position_error = float(((position[:3] - target_position[:3]) ** 2).sum() ** 0.5)
    yaw_error = abs(_angle_difference(_yaw(target["orientation"]), _yaw(orientation)))
    orientation_error = _orientation_error(target["orientation"], orientation)
    if position_error > position_tolerance or yaw_error > yaw_tolerance or orientation_error > yaw_tolerance:
        raise RuntimeError(
            f"Robot is outside the entry target tolerance: position={position_error:.3f}m, "
            f"yaw={yaw_error:.3f}rad, orientation={orientation_error:.3f}"
        )
    actual_joint_positions = robot.get_joint_positions()
    target_joint_positions = th.as_tensor(
        target["joint_positions"],
        dtype=actual_joint_positions.dtype,
        device=actual_joint_positions.device,
    ).clone()
    controlled_indices = robot_metadata(robot)["controlled_joint_indices"]
    joint_error = float(
        th.max(th.abs(actual_joint_positions[controlled_indices] - target_joint_positions[controlled_indices]))
    )
    if joint_error > joint_tolerance:
        raise RuntimeError(f"Robot is outside the entry joint target tolerance: error={joint_error:.3f}rad")


def require_empty_hands(robot):
    if any(obj is not None for obj in robot._ag_obj_in_hand.values()):
        raise RuntimeError("object still held")


def is_stable(robot, joint_velocity=0.05, linear_velocity=0.03, angular_velocity=0.05):
    import torch as th

    joint_indices = th.cat(
        [
            robot.trunk_control_idx,
            *[robot.arm_control_idx[arm] for arm in robot.arm_names],
            *[robot.gripper_control_idx[arm] for arm in robot.arm_names],
        ]
    )
    joint_speed = float(th.max(th.abs(robot.get_joint_velocities()[joint_indices])))
    linear_speed = float(robot.get_linear_velocity().norm())
    angular_speed = float(robot.get_angular_velocity().norm())
    return joint_speed <= joint_velocity and linear_speed <= linear_velocity and angular_speed <= angular_velocity


def require_stable(robot):
    if not is_stable(robot):
        raise RuntimeError("robot still moving")


def require_collision_free(robot):
    from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim

    if detect_robot_collision_in_sim(robot):
        raise RuntimeError("robot in collision")
