import json
import os
import traceback
from copy import deepcopy
from pathlib import Path

import torch as th

from memory_bench.utils.scene_state import (
    get_state_tolerances,
    require_robot_match,
    robot_metadata,
    scene_objects,
    scene_systems,
    state_matches,
    write_json,
)


def _nonrobot_snapshot(snapshot):
    objects = scene_objects(snapshot)
    robot_name = snapshot["robot"]["name"]
    return (
        {name: state for name, state in objects.items() if name != robot_name},
        scene_systems(snapshot),
    )


def _assert_world_preserved(before, after):
    before_objects, before_systems = _nonrobot_snapshot(before)
    after_objects, after_systems = _nonrobot_snapshot(after)
    if set(before_objects) != set(after_objects) or set(before_systems) != set(after_systems):
        raise RuntimeError("Bridge changed the nonrobot scene topology")

    tolerance = get_state_tolerances()
    changed = [
        name
        for name in before_objects
        if not state_matches(after_objects[name], before_objects[name], tolerance, f"objects.{name}")
    ]
    if changed or not state_matches(after_systems, before_systems, tolerance, "systems"):
        raise RuntimeError(f"Bridge changed nonrobot world state: {changed or ['particle systems']}")


def _build_config(session, action_frequency, scene_file):
    import omnigibson as og
    from omnigibson.envs.data_wrapper import _add_recorded_non_kin_states_to_scene_file
    from omnigibson.macros import gm

    scene_model = session.scene_model
    robot_name = scene_file["robot"]["name"]
    robot_args = scene_file["objects_info"]["init_info"][robot_name]["args"]
    map_resolution = float(session.bridge.get("map_resolution", 0.02))
    scene_file = deepcopy(scene_file)
    scene_file["objects_info"]["init_info"][robot_name]["args"]["obs_modalities"] = []
    _add_recorded_non_kin_states_to_scene_file(scene_file, [scene_file])
    config = {
        "env": dict(
            action_frequency=action_frequency,
            rendering_frequency=action_frequency,
            physics_frequency=action_frequency * 4,
            automatic_reset=False,
            flatten_action_space=False,
            flatten_obs_space=False,
            external_sensors=None,
        )
    }
    config["scene"] = {
        "type": "InteractiveTraversableScene",
        "scene_model": scene_model,
        "scene_file": scene_file,
        "load_room_types": None,
        "load_room_instances": None,
        "include_robots": True,
        "trav_map_with_objects": False,
        "trav_map_resolution": map_resolution,
        # Keep the full grid path; drive_to_pose preserves turns when merging straight runs.
        "waypoint_resolution": map_resolution,
    }
    config["robots"] = [
        {
            "type": robot_args["model"],
            "name": robot_name,
            "controller_config": deepcopy(robot_args["controller_config"]),
            "action_normalize": robot_args["action_normalize"],
            "obs_modalities": [],
        }
    ]
    config["render"] = {
        "viewer_width": og.sim.viewer_width if og.sim is not None else gm.DEFAULT_VIEWER_WIDTH,
        "viewer_height": og.sim.viewer_height if og.sim is not None else gm.DEFAULT_VIEWER_HEIGHT,
    }
    config["objects"] = []
    config["task"] = {"type": "DummyTask"}
    return config


def _scene_snapshot(scene, metadata=None):
    from omnigibson.object_states.tensorized_state import TensorizedState
    from omnigibson.utils.config_utils import TorchEncoder

    TensorizedState.maybe_refresh_caches()
    if metadata is None:
        snapshot = json.loads(json.dumps(scene.save(as_dict=True), cls=TorchEncoder))
    else:
        # State remains complete; scene identity and initializers come from the entry save.
        snapshot = {**metadata, "state": json.loads(json.dumps(scene.dump_state(serialized=False), cls=TorchEncoder))}
    if len(scene.robots) != 1:
        raise RuntimeError(f"Bridge boundaries require one R1Pro, found {len(scene.robots)} robots")
    snapshot["robot"] = robot_metadata(scene.robots[0])
    return snapshot


def _robot_contact_diagnostics(robot):
    from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim
    from omnigibson.utils.usd_utils import RigidContactAPI

    ignored = []
    collision = detect_robot_collision_in_sim(robot, filter_objs=ignored)
    ignored_paths = set(RigidContactAPI._get_prim_paths(ignored))
    pairs = RigidContactAPI.get_contact_pairs(
        scene_idx=robot.scene.idx, query_set=[robot], with_set=None, current_only=False
    )
    return {
        "collision_detected": collision,
        "current_only": False,
        "ignored_prim_paths": sorted(ignored_paths),
        "contact_pairs": sorted((a, b) for a, b in pairs if b not in ignored_paths),
        "all_robot_contact_pairs": sorted(pairs),
        "link_owners": {
            link.prim_path: {"object": obj.name, "category": obj.category, "link": link.name}
            for obj in robot.scene.objects
            for link in obj.links.values()
        },
        "robot_links": {name: link.prim_path for name, link in robot.links.items()},
    }


def generate_bridge(
    session,
    scene_file: dict,
    previous_exit: dict,
    next_entry: dict,
    output_path: Path,
) -> dict:
    """Create a bridge from a full-scene exit state to a target entry pose.

    Actions are recorded with OmniGibson's HDF5CollectionWrapper. The entry/exit snapshots
    are full scene saves, so the final state after the last action is available even when a
    consumer uses an HDF5 version that omits the last serialized frame.
    """
    os.environ.setdefault("OMNIGIBSON_HEADLESS", "1")

    import omnigibson as og
    from omnigibson.envs import Environment, HDF5CollectionWrapper
    from omnigibson.macros import gm
    from omnigibson.utils.python_utils import recursively_convert_to_torch

    from memory_bench.navigation import drive_to_pose, move_joints

    with gm.unlocked():
        gm.VISUAL_ONLY = False
        gm.ENABLE_TRANSITION_RULES = False

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

    action_frequency = session.action_frequency
    wrapper = None
    succeeded = False
    latest_snapshot = None
    env = None

    try:
        # Restore the inherited world and prepare its recording environment.
        env = Environment(configs=_build_config(session, action_frequency, previous_exit))
        # Scene.restore converts lists to tensors in place; keep the JSON baseline intact.
        env.scene.restore(deepcopy(previous_exit), update_initial_file=True)
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
            # The world guard must observe sleeping and kinematic background too.
            enable_dump_filters=False,
        )
        wrapper.reset()

        # reset advances physics before recording. Seed the defined inherited entry
        # after that bookkeeping, before any Bridge action is issued.
        env.scene.load_state(recursively_convert_to_torch(deepcopy(previous_exit["state"])), serialized=False)
        og.sim.physics_sim_view.update_articulations_kinematic()
        og.sim.sync_physx_to_fabric()
        og.sim._refresh_state_caches(dt=0.0)
        initial_state = og.sim.dump_state(serialized=True)
        wrapper.current_traj_history = [{"state": initial_state, "state_size": len(initial_state)}]
        wrapper.max_state_size = max(wrapper.max_state_size, len(initial_state))

        data = wrapper.hdf5_file["data"]
        data.attrs["fps"] = wrapper.fps
        data.attrs["control_frequency"] = action_frequency
        data.attrs["action_frequency"] = action_frequency
        data.attrs["robot_model"] = robot.model
        data.attrs["action_dim"] = robot.action_dim

        print("Bridge: checking the inherited entry state", flush=True)
        start_snapshot = _scene_snapshot(env.scene)
        latest_snapshot = start_snapshot
        tolerance = get_state_tolerances()
        _assert_world_preserved(previous_exit, start_snapshot)
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
        if (
            float(th.max(th.abs(entry_velocity[entry_joint_indices]))) > 0.05
            or float(robot.get_linear_velocity().norm()) > 0.03
            or float(robot.get_angular_velocity().norm()) > 0.05
        ):
            raise RuntimeError("Bridge entry robot is not stable")
        write_json(entry_path, start_snapshot)

        # Check after each motion step that nonrobot objects and systems remain unchanged.
        def world_guard():
            nonlocal latest_snapshot
            latest_snapshot = _scene_snapshot(env.scene, metadata=start_snapshot)
            _assert_world_preserved(start_snapshot, latest_snapshot)

        rest = robot.reset_joint_pos
        pose = next_entry["robot"]

        # Stow the torso and arms while keeping the base stationary.
        print("Bridge: stowing arms", flush=True)
        move_joints(robot, wrapper.step, rest, world_guard=world_guard)

        # Drive the base to the next task's position and orientation.
        print(f"Bridge: navigating to {pose['position']}", flush=True)
        nav_report = drive_to_pose(
            env,
            robot,
            pose,
            wrapper.step,
            linear_speed=float(session.bridge.get("max_base_linear_velocity", 0.2)),
            angular_speed=float(session.bridge.get("max_base_angular_velocity", 0.5)),
            frame_count=lambda: len(wrapper.current_traj_history) - 1,
            world_guard=world_guard,
        )

        # Restore the next task's torso, arm, and gripper joint positions.
        print("Bridge: restoring the next task's joint pose", flush=True)
        move_joints(
            robot,
            wrapper.step,
            pose["joint_positions"],
            joint_tolerance=0.02,
            control_grippers=True,
            world_guard=world_guard,
        )

        # Require a safe, settled endpoint matching the next task's robot pose.
        if detect_robot_collision_in_sim(robot):
            raise RuntimeError("Robot collision detected at bridge exit")
        if any(obj is not None for obj in robot._ag_obj_in_hand.values()):
            raise RuntimeError("Robot is not empty-handed at bridge exit")

        end_snapshot = _scene_snapshot(env.scene)
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
        stable = (
            float(th.max(th.abs(velocities[moving_indices]))) <= 0.05
            and float(robot.get_linear_velocity().norm()) <= 0.03
            and float(robot.get_angular_velocity().norm()) <= 0.05
        )
        if not stable:
            raise RuntimeError("R1Pro was moving too quickly at bridge exit")

        # Save the accepted trajectory and return its recipe segment description.
        actions = len(wrapper.current_traj_history) - 1
        wrapper.save_data()
        wrapper = None

        from memory_bench.utils.recordings import recording_metadata

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
                "hdf5": str(output_path.relative_to(session.paths.output_root)),
                "demo": 0,
                "frames": [0, actions],
            },
            "states": {
                "directory": str(output_path.parent.relative_to(session.paths.output_root)),
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

    except Exception:
        # Full-scene teardown can take minutes; expose the cause before cleanup.
        failure_traceback = traceback.format_exc()
        traceback.print_exc()

        contacts = None
        if env is not None:
            try:
                contacts = _robot_contact_diagnostics(env.scene.robots[0])
            except Exception as diagnostic_error:
                contacts = {"diagnostic_error": repr(diagnostic_error), "traceback": traceback.format_exc()}

        reports = session.paths.reports_dir / "bridge_attempts"
        reports.mkdir(parents=True, exist_ok=True)
        attempt = len(list(reports.glob(f"{output_path.stem}_*.json")))
        while (reports / f"{output_path.stem}_{attempt:04d}.json").exists():
            attempt += 1
        recorded_steps = max(0, len(wrapper.current_traj_history) - 1) if wrapper is not None else 0
        write_json(
            reports / f"{output_path.stem}_{attempt:04d}.json",
            {
                "accepted": False,
                "traceback": failure_traceback,
                "recorded_steps": recorded_steps,
                "observed_scene": latest_snapshot,
                "robot_contacts": contacts,
            },
        )
        raise

    finally:
        # Release the environment; keep failure reports but remove incomplete Bridge files.
        try:
            if wrapper is not None:
                wrapper.hdf5_file.close()
        finally:
            og.clear()
            if not succeeded:
                output_path.unlink(missing_ok=True)
                entry_path.unlink(missing_ok=True)
                exit_path.unlink(missing_ok=True)
