"""Generate one executable R1Pro navigation bridge and its segment manifest."""

import json
import os
import traceback
from copy import deepcopy
from pathlib import Path

import torch as th
import h5py

from memory_bench.utils.scene_state import (
    get_state_tolerances,
    require_robot_match,
    require_objects_match,
    read_json,
    file_hash,
    robot_metadata,
    scene_objects,
    scene_systems,
    state_matches,
    write_json,
)


def _load_terminal_attempt(session, path, previous_exit, next_entry, bridge_id):
    """Accept only a preserved prefix whose sole failure was the final velocity check."""
    path = session.paths.resolve(path).resolve()
    report_hash = file_hash(path)
    report = read_json(path)
    failure = report["traceback"].strip()
    if (
        report["accepted"] is not False
        or failure.count("Traceback (most recent call last):") != 1
        or failure.splitlines()[-1] != "RuntimeError: R1Pro was moving too quickly at bridge exit"
        or report.get("artifact_preservation_error") is not None
        or report["robot_contacts"].get("collision_detected") is not False
        or report["robot_contacts"].get("contact_pairs")
    ):
        raise ValueError("Resume requires a sole terminal-stability failure with preserved collision-free evidence")
    archive = path.with_suffix("")
    paths = {key: session.paths.resolve(report["artifacts"][key]).resolve() for key in ("hdf5", "entry", "exit")}
    expected_names = {"hdf5": f"{bridge_id}.hdf5", "entry": f"{bridge_id}_entry.json", "exit": f"{bridge_id}_exit.json"}
    if any(value.parent != archive or value.name != expected_names[key] for key, value in paths.items()):
        raise ValueError("Resume artifacts do not belong to the selected archived Bridge attempt")
    hashes = {key: file_hash(value) for key, value in paths.items()}
    entry, checkpoint = read_json(paths["entry"]), read_json(paths["exit"])
    tolerance = get_state_tolerances()
    names = set(scene_objects(previous_exit))
    if set(scene_objects(entry)) != names or set(scene_objects(checkpoint)) != names:
        raise ValueError("Resume artifact world topology differs from the expected Bridge")
    require_objects_match(entry, previous_exit, names, tolerance, "Archived original Bridge entry")
    require_robot_match(
        entry, previous_exit, previous_exit["robot"]["name"], tolerance, "Archived original Bridge entry"
    )
    _assert_world_preserved(entry, checkpoint)
    require_robot_match(checkpoint, next_entry, previous_exit["robot"]["name"], tolerance, "Archived Bridge endpoint")
    continuity = deepcopy(tolerance)
    continuity["ignored_fields"] = ["is_asleep"]
    continuity["numeric"].update(lin_vel=1e-6, ang_vel=1e-6, joint_vel=1e-6)
    if not state_matches(checkpoint["state"], report["observed_scene"]["state"], continuity):
        raise ValueError("Archived exit and reported observed state differ, including velocities")
    from memory_bench.utils.recordings import recording_metadata

    metadata = recording_metadata(paths["hdf5"])
    interface = metadata["interface"]
    args = previous_exit["objects_info"]["init_info"][previous_exit["robot"]["name"]]["args"]
    if (
        interface["robot_name"] != previous_exit["robot"]["name"]
        or interface["robot_type"] != args["model"].lower()
        or interface["controller_config"] != args["controller_config"]
        or interface["action_normalize"] != args["action_normalize"]
        or interface["action_frequency"] != session.action_frequency
        or interface["fps"] != session.action_frequency
        or interface["physics_frequency"] != session.action_frequency * 4
    ):
        raise ValueError("Archived Bridge robot/controller/frequency differs from the current session")
    if set(scene_objects(metadata["scene"])) != names or any(
        set(state.get("non_kin", {})) != set(scene_objects(entry)[name].get("non_kin", {}))
        for name, state in scene_objects(metadata["scene"]).items()
    ):
        raise ValueError("Archived raw scene topology/non-kinematic schema differs from its original entry")
    with h5py.File(paths["hdf5"], "r") as stream:
        group = stream["data/demo_0"]
        count = len(group["action"])
        if (
            count <= 0
            or count != report["recorded_steps"]
            or int(group.attrs["num_samples"]) != count
            or int(stream["data"].attrs["n_steps"]) != count
            or int(stream["data"].attrs["n_episodes"]) != 1
        ):
            raise ValueError("Archived Bridge action counts differ from its failure report")
        if json.loads(group.attrs["transitions"]) != {}:
            raise ValueError("Terminal continuation does not support topology transitions")
        prefix = {key: group[key][:] for key in ("action", "state", "state_size", "reward", "terminated", "truncated")}
        if (
            any(len(values) != count for values in prefix.values())
            or prefix["action"].shape[1] != interface["action_dim"]
        ):
            raise ValueError("Archived Bridge prefix datasets are not aligned")
        if any(size <= 0 or size > prefix["state"].shape[1] for size in prefix["state_size"]):
            raise ValueError("Archived Bridge serialized state sizes are invalid")
    provenance = {
        "attempt": session.paths.relative(path),
        "attempt_sha256": report_hash,
        "session_id": session.session_id,
        "bridge_id": bridge_id,
        "artifacts": {key: session.paths.relative(value) for key, value in paths.items()},
        "artifact_sha256": hashes,
        "hash_scope": "computed_when_loading_resume_inputs_not_original_run",
        "prefix_actions": count,
        "prefix_navigation": "completed_before_reported_terminal_velocity_failure",
        "navigation_details": "not_saved_by_original_attempt",
    }
    if file_hash(path) != report_hash or any(file_hash(value) != hashes[key] for key, value in paths.items()):
        raise ValueError("Terminal-resume evidence changed while loading")
    return entry, checkpoint, prefix, provenance, continuity


def _seed_terminal_history(wrapper, prefix, terminal):
    """Restore collector action/post-state alignment, including the previously dropped state[N]."""
    count = len(prefix["action"])
    initial = th.as_tensor(prefix["state"][0, : int(prefix["state_size"][0])])
    history = [{"state": initial, "state_size": len(initial)}]
    for index in range(count):
        state = (
            terminal
            if index + 1 == count
            else th.as_tensor(prefix["state"][index + 1, : int(prefix["state_size"][index + 1])])
        )
        history.append(
            {
                "state": state,
                "state_size": len(state),
                "action": th.as_tensor(prefix["action"][index]),
                **{key: prefix[key][index].item() for key in ("reward", "terminated", "truncated")},
            }
        )
    wrapper.current_traj_history = history
    wrapper.max_state_size = max(wrapper.max_state_size, *(len(item["state"]) for item in history))
    wrapper.step_count = count


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


def _scene_snapshot(scene, encoder, metadata=None):
    from omnigibson.object_states.tensorized_state import TensorizedState

    TensorizedState.maybe_refresh_caches()
    if metadata is None:
        snapshot = json.loads(json.dumps(scene.save(as_dict=True), cls=encoder))
    else:
        # State remains complete; scene identity and initializers come from the entry save.
        snapshot = {**metadata, "state": json.loads(json.dumps(scene.dump_state(serialized=False), cls=encoder))}
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


def _write_bridge_metadata(wrapper, robot):
    data = wrapper.hdf5_file["data"]
    frequency = wrapper.env.env_config["action_frequency"]
    data.attrs["fps"] = wrapper.fps
    data.attrs["control_frequency"] = frequency
    data.attrs["action_frequency"] = frequency
    data.attrs["robot_model"] = robot.model
    data.attrs["action_dim"] = robot.action_dim


def synthesize_bridge(
    session,
    scene_file: dict,
    previous_exit: dict,
    next_entry: dict,
    output_path: Path,
    *,
    clear_on_failure=True,
    env=None,
    resume_attempt=None,
) -> dict:
    """Create a bridge from a full-scene exit state to a target entry pose.

    Actions are recorded with OmniGibson's HDF5CollectionWrapper. The entry/exit snapshots
    are full scene saves, so the final state after the last action is available even when a
    consumer uses an HDF5 version that omits the last serialized frame.
    """
    session.paths.activate()
    os.environ.setdefault("OMNIGIBSON_HEADLESS", "1")
    if resume_attempt is not None and session.mode != "intervent_debug":
        raise ValueError("Terminal-stability continuation is only available for explicit I/Bridge debugging")

    import omnigibson as og
    from omnigibson.envs import Environment, HDF5CollectionWrapper
    from omnigibson.macros import gm
    from omnigibson.utils.config_utils import TorchEncoder
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
    resume = None
    if resume_attempt is not None:
        resume = _load_terminal_attempt(session, resume_attempt, previous_exit, next_entry, output_path.stem)
    try:
        if env is None:
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
        _write_bridge_metadata(wrapper, robot)
        print("Bridge: checking the inherited entry state", flush=True)
        start_snapshot = _scene_snapshot(env.scene, TorchEncoder)
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
        if resume is not None:
            archived_entry, checkpoint, prefix, resume_provenance, continuity = resume
            require_objects_match(
                start_snapshot, archived_entry, set(scene_objects(archived_entry)), tolerance, "Resume original entry"
            )
            decoded, consumed = og.sim.deserialize(th.as_tensor(prefix["state"][0, : int(prefix["state_size"][0])]))
            if consumed != int(prefix["state_size"][0]) or not state_matches(
                decoded[0], archived_entry["state"], continuity
            ):
                raise ValueError("Archived raw state[0] differs from the saved original entry")
            start_snapshot = archived_entry
        write_json(entry_path, start_snapshot)

        def world_guard():
            nonlocal latest_snapshot
            latest_snapshot = _scene_snapshot(env.scene, TorchEncoder, metadata=start_snapshot)
            _assert_world_preserved(start_snapshot, latest_snapshot)

        # Stow without moving the base, then follow the planned path with base velocity actions.
        rest = robot.reset_joint_pos
        pose = _pose_from_entry(next_entry)
        if resume is not None:
            print("Bridge: restoring saved terminal checkpoint for physical stability continuation", flush=True)
            env.scene.load_state(recursively_convert_to_torch(deepcopy(checkpoint["state"])), serialized=False)
            og.sim.physics_sim_view.update_articulations_kinematic()
            og.sim.sync_physx_to_fabric()
            og.sim._refresh_state_caches(dt=0.0)
            restored = _scene_snapshot(env.scene, TorchEncoder, metadata=start_snapshot)
            if not state_matches(restored["state"], checkpoint["state"], continuity):
                raise ValueError("Restored terminal checkpoint is discontinuous, including velocities")
            _assert_world_preserved(start_snapshot, restored)
            require_robot_match(restored, next_entry, robot.name, tolerance, "Resume checkpoint endpoint")
            if detect_robot_collision_in_sim(robot) or any(obj is not None for obj in robot._ag_obj_in_hand.values()):
                raise RuntimeError("Resume checkpoint must be collision-free and empty-handed")
            terminal = og.sim.dump_state(serialized=True)
            _seed_terminal_history(wrapper, prefix, terminal)
            latest_snapshot = restored
            nav_report = {"resume": resume_provenance}
        else:
            print("Bridge: stowing arms", flush=True)
            move_joints(robot, wrapper.step, rest, world_guard=world_guard)
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

        target_joints = _joint_positions_from_entry(next_entry)
        control_grippers = target_joints is not None
        if target_joints is None:
            target_joints = rest
        print("Bridge: restoring the next task's joint pose", flush=True)
        move_joints(
            robot,
            wrapper.step,
            target_joints,
            joint_tolerance=0.02,
            control_grippers=control_grippers,
            world_guard=world_guard,
        )
        if resume is not None:
            tail_end = len(wrapper.current_traj_history) - 1
            if tail_end <= resume_provenance["prefix_actions"]:
                raise RuntimeError("Terminal continuation must record real hold actions")
            nav_report["resume"]["continuation_frames"] = [resume_provenance["prefix_actions"], tail_end]
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
        stable = (
            float(th.max(th.abs(velocities[moving_indices]))) <= 0.05
            and float(robot.get_linear_velocity().norm()) <= 0.03
            and float(robot.get_angular_velocity().norm()) <= 0.05
        )
        if not stable:
            raise RuntimeError("R1Pro was moving too quickly at bridge exit")
        if resume is not None:
            if file_hash(session.paths.resolve(resume_provenance["attempt"])) != resume_provenance[
                "attempt_sha256"
            ] or any(
                file_hash(session.paths.resolve(path)) != resume_provenance["artifact_sha256"][key]
                for key, path in resume_provenance["artifacts"].items()
            ):
                raise ValueError("Archived resume evidence changed during continuation")

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
        reports = session.paths.resolve(f"reports/{session.session_id}/bridge_attempts")
        reports.mkdir(parents=True, exist_ok=True)
        attempt = len(list(reports.glob(f"{output_path.stem}_*.json")))
        while (reports / f"{output_path.stem}_{attempt:04d}.json").exists() or (
            reports / f"{output_path.stem}_{attempt:04d}"
        ).exists():
            attempt += 1
        recorded_steps = max(0, len(wrapper.current_traj_history) - 1) if wrapper is not None else 0
        artifacts = {}
        preservation_error = None
        if not clear_on_failure:
            try:
                if wrapper is not None and wrapper.hdf5_file.id.valid:
                    wrapper.save_data()
                wrapper = None
                saved_paths = {"hdf5": output_path, "entry": entry_path, "exit": exit_path}
                saved_paths = {key: path for key, path in saved_paths.items() if path.exists()}
                if saved_paths:
                    archive = reports / f"{output_path.stem}_{attempt:04d}"
                    archive.mkdir()
                    for key, path in saved_paths.items():
                        destination = archive / path.name
                        path.rename(destination)
                        artifacts[key] = session.paths.relative(destination)
            except Exception as archive_error:
                preservation_error = {"error": repr(archive_error), "traceback": traceback.format_exc()}
                for key, path in (("hdf5", output_path), ("entry", entry_path), ("exit", exit_path)):
                    if path.exists():
                        artifacts[key] = session.paths.relative(path)
        write_json(
            reports / f"{output_path.stem}_{attempt:04d}.json",
            {
                "accepted": False,
                "traceback": failure_traceback,
                "recorded_steps": recorded_steps,
                "observed_scene": latest_snapshot,
                "observed_scene_metadata_source": artifacts.get("entry", session.paths.relative(entry_path))
                if latest_snapshot
                else None,
                "observed_scene_metadata_kind": "bridge_entry_scene_save" if latest_snapshot else None,
                "robot_contacts": contacts,
                "artifacts": artifacts,
                "artifact_preservation_error": preservation_error,
                "resume": resume[3] if resume is not None else None,
            },
        )
        raise
    finally:
        try:
            if wrapper is not None and wrapper.hdf5_file.id.valid and (succeeded or clear_on_failure):
                wrapper.save_data()
        finally:
            if succeeded or clear_on_failure:
                og.clear()
            if not succeeded and clear_on_failure:
                output_path.unlink(missing_ok=True)
                entry_path.unlink(missing_ok=True)
                exit_path.unlink(missing_ok=True)
