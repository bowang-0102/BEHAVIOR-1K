"""Generate one executable R1Pro navigation bridge and its segment manifest."""

import json
import os
from pathlib import Path

import torch as th

from memory_bench.utils.state import (
    require_robot_match,
    robot_metadata,
    scene_objects,
    scene_systems,
    state_matches,
    write_json,
)


def _robot_snapshot(snapshot):
    objects = scene_objects(snapshot)
    init_info = snapshot["objects_info"]["init_info"]
    robot_names = [
        name
        for name, info in init_info.items()
        if info["class_name"].lower() in {"robot", "r1", "r1pro"}
        or str(info.get("args", {}).get("model", "")).lower() == "r1pro"
        or info.get("args", {}).get("name") == "robot"
    ]
    if len(robot_names) != 1:
        raise ValueError(f"Expected one robot in scene snapshot, found {robot_names}")
    return objects[robot_names[0]]


def _pose_from_entry(entry):
    if "position" in entry and "orientation" in entry:
        return {"position": entry["position"], "orientation": entry["orientation"]}

    if "robot" in entry and isinstance(entry["robot"], dict):
        robot = entry["robot"]
        if "position" in robot and "orientation" in robot:
            return {"position": robot["position"], "orientation": robot["orientation"]}

    raise ValueError("Bridge target is missing actual robot boundary metadata or an explicit pose")


def _joint_positions_from_entry(entry):
    if "joint_positions" in entry:
        return entry["joint_positions"]
    if "robot" in entry and isinstance(entry["robot"], dict) and "joint_positions" in entry["robot"]:
        return entry["robot"]["joint_positions"]
    if "position" in entry and "orientation" in entry:
        return None
    if "robot" in entry and isinstance(entry["robot"], dict):
        return None
    if "state" in entry or "objects_info" in entry:
        raise ValueError("Bridge target scene snapshot is missing actual robot joint metadata")
    return None


def _nonrobot_snapshot(snapshot):
    objects = scene_objects(snapshot)
    robot_state = _robot_snapshot(snapshot)
    return (
        {name: state for name, state in objects.items() if state is not robot_state},
        scene_systems(snapshot),
    )


def _assert_world_preserved(before, after, session):
    before_objects, before_systems = _nonrobot_snapshot(before)
    after_objects, after_systems = _nonrobot_snapshot(after)
    if set(before_objects) != set(after_objects) or set(before_systems) != set(after_systems):
        raise RuntimeError("Bridge changed the nonrobot scene topology")

    config = session.config["validation"]["state_tolerances"]
    tolerance = {"ignored_fields": config["ignored_fields"], "numeric": config["numeric"]}
    changed = [
        name
        for name in before_objects
        if not state_matches(after_objects[name], before_objects[name], tolerance, f"objects.{name}")
    ]
    if changed or not state_matches(after_systems, before_systems, tolerance, "systems"):
        raise RuntimeError(f"Bridge changed nonrobot world state: {changed or ['particle systems']}")


def _build_config(session, action_frequency, scene_file):
    from joylo.gello.utils.og_teleop_utils import generate_basic_environment_config, generate_robot_config

    scene_model = session.config["scene_model"]
    config = generate_basic_environment_config("r1pro", "robot")
    config["env"].update(
        action_frequency=action_frequency,
        rendering_frequency=action_frequency,
        physics_frequency=action_frequency * 4,
        automatic_reset=False,
        flatten_action_space=False,
        flatten_obs_space=False,
        external_sensors=None,
    )
    config["scene"] = {
        "type": "InteractiveTraversableScene",
        "scene_model": scene_model,
        "scene_file": scene_file,
        "load_room_types": None,
        "load_room_instances": None,
        "include_robots": False,
        "trav_map_with_objects": False,
    }
    config["robots"] = [generate_robot_config("r1pro", "robot")]
    config["objects"] = []
    config["task"] = {"type": "DummyTask"}
    return config


def _scene_snapshot(scene, encoder):
    snapshot = json.loads(json.dumps(scene.save(as_dict=True), cls=encoder))
    if len(scene.robots) != 1:
        raise RuntimeError(f"Bridge boundaries require one R1Pro, found {len(scene.robots)} robots")
    snapshot["robot"] = robot_metadata(scene.robots[0])
    return snapshot


def _write_bridge_metadata(wrapper, robot):
    data = wrapper.hdf5_file["data"]
    frequency = wrapper.env.env_config["action_frequency"]
    data.attrs["fps"] = wrapper.fps
    data.attrs["control_frequency"] = frequency
    data.attrs["action_frequency"] = frequency
    data.attrs["robot_model"] = robot.model
    data.attrs["action_dim"] = robot.action_dim


def synthesize_bridge(session, scene_file: dict, previous_exit: dict, next_entry: dict, output_path: Path) -> dict:
    """Create a bridge from a full-scene exit state to a target entry pose.

    Actions are recorded with OmniGibson's HDF5CollectionWrapper. The entry/exit snapshots
    are full scene saves, so the final state after the last action is available even when a
    consumer uses an HDF5 version that omits the last serialized frame.
    """
    session.paths.activate()
    os.environ.setdefault("OMNIGIBSON_HEADLESS", "1")

    import omnigibson as og
    from omnigibson.envs import Environment, HDF5CollectionWrapper
    from omnigibson.utils.config_utils import TorchEncoder

    from memory_bench.navigation import drive_to_pose, move_joints

    output_path = Path(output_path).resolve()
    entry_path = output_path.with_name(f"{output_path.stem}_entry.json")
    exit_path = output_path.with_name(f"{output_path.stem}_exit.json")
    if output_path.exists() or entry_path.exists() or exit_path.exists():
        raise FileExistsError(f"Bridge output already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if set(scene_objects(scene_file)) != set(scene_objects(previous_exit)):
        raise ValueError("scene_file and previous_exit must contain the same full-scene object set")
    if set(scene_systems(scene_file)) != set(scene_systems(previous_exit)):
        raise ValueError("scene_file and previous_exit must contain the same systems")
    if not isinstance(previous_exit.get("robot"), dict):
        raise ValueError("Bridge start is missing actual robot boundary metadata")
    if not isinstance(next_entry.get("robot"), dict):
        raise ValueError("Bridge target is missing actual robot boundary metadata")

    action_frequency = int(session.config.get("bridge", {}).get("action_frequency", 30))
    env = None
    wrapper = None
    succeeded = False
    try:
        env = Environment(configs=_build_config(session, action_frequency, previous_exit))
        env.scene.restore(previous_exit, update_initial_file=True)
        robot = env.robots[0][0]
        if robot.model != "r1pro":
            raise ValueError(f"Bridge synthesis requires R1Pro, got {robot.model}")
        if any(obj is not None for obj in robot._ag_obj_in_hand.values()):
            raise ValueError("Bridge entry requires an empty-handed robot")

        from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim

        wrapper = HDF5CollectionWrapper(
            env=env,
            output_path=str(output_path),
            overwrite=False,
            only_successes=False,
            flush_every_n_traj=1,
            keep_checkpoint_rollback_data=False,
        )
        wrapper.reset()
        _write_bridge_metadata(wrapper, robot)
        start_snapshot = _scene_snapshot(env.scene, TorchEncoder)
        state_tolerances = session.config["validation"]["state_tolerances"]
        tolerance = {
            "ignored_fields": state_tolerances["ignored_fields"],
            "numeric": state_tolerances["numeric"],
        }
        _assert_world_preserved(previous_exit, start_snapshot, session)
        require_robot_match(start_snapshot, previous_exit, robot.name, tolerance, "Bridge entry")
        if any(obj is not None for obj in robot._ag_obj_in_hand.values()):
            raise RuntimeError("Bridge entry requires an empty-handed robot")
        if detect_robot_collision_in_sim(robot):
            raise RuntimeError("Robot collision detected at bridge entry")
        entry_velocity = robot.get_joint_velocities()
        entry_joint_indices = th.tensor(
            start_snapshot["robot"]["controlled_joint_indices"],
            dtype=th.long,
            device=entry_velocity.device,
        )
        boundary = session.config["validation"].get("robot_boundary", {})
        if (
            float(th.max(th.abs(entry_velocity[entry_joint_indices]))) > float(boundary.get("joint_velocity", 0.05))
            or float(robot.get_linear_velocity().norm()) > float(boundary.get("linear_velocity", 0.03))
            or float(robot.get_angular_velocity().norm()) > float(boundary.get("angular_velocity", 0.05))
        ):
            raise RuntimeError("Bridge entry robot is not stable")
        write_json(entry_path, start_snapshot)

        def world_guard():
            _assert_world_preserved(start_snapshot, _scene_snapshot(env.scene, TorchEncoder), session)

        # Stow without moving the base, then follow the planned path with base velocity actions.
        rest = robot.reset_joint_pos
        move_joints(robot, wrapper.step, rest, world_guard=world_guard)
        pose = _pose_from_entry(next_entry)
        limits = session.config["validation"].get("robot_boundary", {})
        state_tolerance = session.config["validation"]["state_tolerances"]["numeric"]
        nav_report = drive_to_pose(
            env,
            robot,
            pose,
            wrapper.step,
            linear_speed=float(session.config["bridge"].get("max_base_linear_velocity", 0.2)),
            angular_speed=float(session.config["bridge"].get("max_base_angular_velocity", 0.5)),
            frame_count=lambda: len(wrapper.current_traj_history) - 1,
            position_tolerance=float(state_tolerance["pos"]),
            yaw_tolerance=float(limits.get("yaw_tolerance", state_tolerance["ori"])),
            linear_velocity_tolerance=float(limits.get("linear_velocity", 0.03)),
            angular_velocity_tolerance=float(limits.get("angular_velocity", 0.05)),
            world_guard=world_guard,
        )

        target_joints = _joint_positions_from_entry(next_entry)
        control_grippers = target_joints is not None
        if target_joints is None:
            target_joints = rest
        move_joints(
            robot,
            wrapper.step,
            target_joints,
            joint_tolerance=float(state_tolerance["joint_pos"]),
            control_grippers=control_grippers,
            world_guard=world_guard,
        )
        if detect_robot_collision_in_sim(robot):
            raise RuntimeError("Robot collision detected at bridge exit")
        if any(obj is not None for obj in robot._ag_obj_in_hand.values()):
            raise RuntimeError("Robot is not empty-handed at bridge exit")

        end_snapshot = _scene_snapshot(env.scene, TorchEncoder)
        world_guard()
        require_robot_match(end_snapshot, next_entry, robot.name, tolerance, "Bridge endpoint")
        write_json(exit_path, end_snapshot)

        velocities = robot.get_joint_velocities()
        moving_indices = th.cat(
            [
                robot.trunk_control_idx,
                *[robot.arm_control_idx[arm] for arm in robot.arm_names],
                *[robot.gripper_control_idx[arm] for arm in robot.arm_names],
            ]
        )
        boundary = session.config["validation"].get("robot_boundary", {})
        stable = (
            float(th.max(th.abs(velocities[moving_indices]))) <= float(boundary.get("joint_velocity", 0.05))
            and float(robot.get_linear_velocity().norm()) <= float(boundary.get("linear_velocity", 0.03))
            and float(robot.get_angular_velocity().norm()) <= float(boundary.get("angular_velocity", 0.05))
        )
        if not stable:
            raise RuntimeError("R1Pro was moving too quickly at bridge exit")

        actions = len(wrapper.current_traj_history) - 1
        wrapper.save_data()
        wrapper = None

        from memory_bench.processing.recordings import recording_metadata

        interface = recording_metadata(output_path, demo=0)["interface"]

        bridge_id = output_path.stem
        manifest = {
            "identity": {"id": bridge_id, "instance_id": None},
            "task": {
                "role": "bridge_task",
                "activity": "bridge_navigation",
                "room_id": None,
                "instruction": "Navigate to the next task entry pose.",
                "depends_on": [],
            },
            "source": {
                "hdf5": session.paths.relative(output_path),
                "demo": 0,
                "frames": [0, actions],
            },
            "states": {
                "directory": session.paths.relative(output_path.parent),
                "entry": entry_path.name,
                "exit": exit_path.name,
            },
            "objects": {"used": [], "read": [], "write": [], "memory_bindings": {}},
            "interface": interface,
            "validation": {
                "entry": True,
                "exit": True,
                "empty_hands": True,
                "stable": stable,
                "collision_free": True,
                "nonrobot_state_preserved": True,
            },
            "motion": nav_report,
        }
        succeeded = True
        return manifest
    finally:
        try:
            if wrapper is not None and wrapper.hdf5_file.id.valid:
                wrapper.save_data()
        finally:
            og.clear()
            if not succeeded:
                output_path.unlink(missing_ok=True)
                entry_path.unlink(missing_ok=True)
                exit_path.unlink(missing_ok=True)
