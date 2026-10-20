"""Select distinct source recordings after physical boundary and declared-state audits."""

import argparse
import hashlib
import json
from copy import deepcopy

import h5py

from memory_bench.processing.make_segments import make_segment
from memory_bench.processing.normalize_recording import normalize_recording
from memory_bench.session import Session
from memory_bench.utils.recordings import FrameReader, inspect_source, recording_metadata
from memory_bench.utils.scene_state import (
    file_hash,
    get_state_tolerances,
    read_json,
    robot_metadata,
    scene_objects,
    state_matches,
    write_json,
    write_json_atomic,
)
from memory_bench.utils.segment_manifest import load_segments


def validate_prepared_segment(session, key, path):
    """Reuse only frozen manifests whose raw, normalized and boundary files still match."""
    manifest = load_segments(session, {key: session.paths.relative(path)})[key]
    states = manifest["states"]
    for name in ("entry", "exit"):
        path = session.paths.resolve(states["directory"]) / states[name]
        if file_hash(path) != states["sha256"][name]:
            raise ValueError(f"Prepared boundary changed after freezing: {path}")
    return manifest


def audit_boundaries(source, normalized, demo=0):
    import torch as th
    import omnigibson as og
    from omnigibson.envs.data_wrapper import (
        _add_recorded_non_kin_states_to_scene_file,
        _align_scene_object_states_with_recorded_schema,
    )
    from omnigibson.macros import gm
    from omnigibson.utils.python_utils import recursively_convert_to_torch
    from bddl.condition_evaluation import evaluate_state

    metadata = recording_metadata(source, demo)
    config = deepcopy(metadata["config"])
    scene = deepcopy(metadata["scene"])
    _add_recorded_non_kin_states_to_scene_file(scene, [scene])
    config["scene"].update(scene_file=scene, load_room_types=None, load_room_instances=None)
    config["objects"] = []
    config["task"].update(online_object_sampling=False, use_presampled_robot_pose=False)
    config["env"]["automatic_reset"] = False
    if og.sim is not None:
        config.setdefault("render", {}).update(viewer_width=og.sim.viewer_width, viewer_height=og.sim.viewer_height)
    config["env"]["external_sensors"] = None
    for robot_config in config["robots"]:
        robot_config["obs_modalities"] = []
    with gm.unlocked():
        gm.ENABLE_TRANSITION_RULES = False
        gm.VISUAL_ONLY = False
    env = og.Environment(configs=config)
    _align_scene_object_states_with_recorded_schema(env.scene, scene)
    robot = env.scene.robots[0]
    validation = {"entry": True, "exit": True, "empty_hands": True, "stable": True}
    reader = FrameReader(normalized)
    snapshots = []
    try:
        for index, frame in enumerate((0, len(reader.group["action"]))):
            state = reader.state(frame)
            env.scene.load_state(recursively_convert_to_torch(deepcopy(state)), serialized=False)
            physical_robot = robot_metadata(robot)
            # Propagate spatial predicates in normal physics; this is an audit, not visual-only replay.
            og.sim.step()
            empty = all(robot._ag_obj_in_hand[arm] is None for arm in robot.arm_names)
            stable = (
                th.linalg.vector_norm(robot.get_linear_velocity()).item() <= 0.03
                and th.linalg.vector_norm(robot.get_angular_velocity()).item() <= 0.05
                and robot.get_joint_velocities().abs().max().item() <= 0.05
            )
            validation["empty_hands"] &= empty
            validation["stable"] &= stable
            if index:
                validation["exit"] = any(all(option) for option in env.task.get_goal_option_satisfaction(0))
            else:
                validation["entry"] = evaluate_state(
                    env.task.activity_initial_conditions,
                    lambda predicate, *entities: env.task._evaluate_predicate(0, predicate, *entities),
                )[0]
            snapshot = deepcopy(metadata["scene"])
            snapshot["state"] = state
            snapshot["robot"] = physical_robot
            snapshots.append(snapshot)
        if not all(validation.values()):
            raise ValueError(f"Source boundary checks failed: {validation}")
    finally:
        reader.close()
        og.clear()
    return snapshots, validation


def prepare_intervent(session, key, source, demo, directory, resume=False):
    """Prepare one I source; explicit resume reuses only a completed, source-matched normalization."""
    source = session.paths.resolve(source).resolve()
    directory = session.paths.resolve(directory)
    if directory.exists() and not resume:
        raise FileExistsError(f"Use a fresh output root or explicitly remove failed candidate output: {directory}")
    source_identity = {"hdf5": str(source), "demo": demo, "sha256": file_hash(source)}
    source_path = directory / "source.json"
    if directory.exists():
        if not source_path.is_file() or read_json(source_path) != source_identity:
            raise ValueError(f"Cannot resume preparation with a missing or different source identity: {source_path}")
    else:
        write_json(source_path, source_identity)
    if (directory / "segment.json").exists():
        manifest = validate_prepared_segment(session, key, directory / "segment.json")
        frozen = manifest["source"]
        if session.paths.resolve(frozen["hdf5"]).resolve() != source or frozen["demo"] != demo:
            raise ValueError(f"Prepared manifest refers to a different source/demo: {directory}")
        return directory / "segment.json"
    assets = read_json(session.paths.resolve(session.assets["manifest"]))
    inspected = inspect_source(
        source,
        session.segments[key],
        session.scene_model,
        session.segments[key].get("extra_objects", []),
        demo,
    )
    if not set(inspected["used"]).issubset(assets["intervent_objects"][key]):
        raise ValueError("Source uses assets outside the frozen session")
    normalized = directory / "normalized.hdf5"
    if normalized.exists():
        with h5py.File(normalized, "r") as stream:
            data = stream.get("data")
            if data is None or not data.attrs.get("normalization_complete", False):
                raise ValueError(f"Normalization is incomplete; use a fresh directory and preserve {normalized}")
            group = data["demo_0"]
            if len(group["action"]) != inspected["frames"][1] or len(group["state_json"]) != len(group["action"]) + 1:
                raise ValueError(f"Normalized frame counts differ from the source or terminal state: {normalized}")
    else:
        normalize_recording(session, source, normalized, demo)
    snapshots, validation = audit_boundaries(source, normalized, demo)
    for name, snapshot in zip(("entry.json", "exit.json"), snapshots):
        path = directory / name
        if path.exists():
            value = json.loads(json.dumps(snapshot, default=lambda value: value.tolist()))
            if read_json(path) != value:
                raise ValueError(f"Audited boundary differs from the saved snapshot: {path}")
        else:
            write_json(path, snapshot)
    reader = FrameReader(normalized)
    changed = set()
    tolerance = get_state_tolerances()
    try:
        baseline = scene_objects(snapshots[0])
        for frame in range(len(reader.group["action"]) + 1):
            current = scene_objects(reader.state(frame))
            for obj in inspected["used"]:
                if current[obj] != baseline[obj] and not state_matches(current[obj], baseline[obj], tolerance):
                    changed.add(obj)
    finally:
        reader.close()
    reference = {
        "hdf5": str(source),
        "demo": demo,
        "frames": inspected["frames"],
        "normalized_hdf5": session.paths.relative(normalized),
        "normalized_demo": 0,
    }
    states = {"directory": session.paths.relative(directory), "entry": "entry.json", "exit": "exit.json"}
    objects = {
        "used": inspected["used"],
        "read": inspected["used"],
        "write": sorted(changed),
        "memory_bindings": {},
    }
    output = directory / "segment.json"
    make_segment(session, key, 0, reference, states, objects, validation, output)
    return output


def prepare_intervents(session, inventory, resume=False):
    if inventory["session_id"] != session.session_id:
        raise ValueError("Source inventory belongs to another session")
    expected = {key for key, definition in session.segments.items() if definition["role"] == "intervent_task"}
    if set(inventory["tasks"]) != expected:
        raise ValueError("Source inventory must contain every configured I exactly once")
    count = session.instance_count
    results, rejected = {}, []
    for key, task in inventory["tasks"].items():
        results[key] = []
        seen = set()
        for candidate in task["candidates"]:
            if len(results[key]) == count:
                break
            source = session.paths.resolve(candidate["hdf5"])
            identity = (str(source.resolve()), candidate["demo"])
            if identity in seen:
                continue
            seen.add(identity)
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:12]
            name = f"{source.stem}_demo_{candidate['demo']}_{digest}"
            directory = session.paths.output_root / "segments" / session.session_id / key / name
            try:
                output = prepare_intervent(session, key, source, candidate["demo"], directory, resume=resume)
                results[key].append(session.paths.relative(output))
            except (ValueError, KeyError) as error:
                rejected.append({"segment_id": key, "hdf5": str(source), "reason": str(error)})
        if len(results[key]) != count:
            write_json_atomic(
                session.paths.output_root / "reports" / session.session_id / f"{key}_selection_failure.json",
                {"selected": results[key], "required": count, "rejected": rejected},
            )
            raise ValueError(
                f"Only {len(results[key])}/{count} compatible sources for {key}; no instance was silently dropped"
            )
    for path, value in (
        (session.paths.output_root / "sessions" / session.session_id / "intervent_segments.json", results),
        (session.paths.output_root / "reports" / session.session_id / "intervent_selection.json",
         {"selected": results, "rejected": rejected}),
    ):
        if resume and path.exists():
            if read_json(path) != value:
                raise ValueError(f"Frozen I selection changed; use a separate output root: {path}")
        else:
            write_json(path, value)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("inventory")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    result = prepare_intervents(session, read_json(session.paths.resolve(args.inventory)), args.resume)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
