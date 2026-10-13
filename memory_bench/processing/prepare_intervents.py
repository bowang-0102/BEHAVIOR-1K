"""Select distinct source recordings after physical boundary and declared-state audits."""

import argparse
import json
from copy import deepcopy

from memory_bench.processing.make_segments import make_segment
from memory_bench.processing.normalize_recording import normalize_recording
from memory_bench.processing.sources import FrameReader, inspect_source, recording_metadata
from memory_bench.session import Session
from memory_bench.utils.state import read_json, robot_metadata, scene_objects, state_matches, write_json


def audit_boundaries(session, source, normalized, demo=0):
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
    gm.ENABLE_TRANSITION_RULES = False
    gm.VISUAL_ONLY = False
    env = og.Environment(configs=config)
    _align_scene_object_states_with_recorded_schema(env.scene, scene)
    robot = env.scene.robots[0]
    thresholds = session.config["validation"]["robot_boundary"]
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
                th.linalg.vector_norm(robot.get_linear_velocity()).item() <= thresholds["linear_velocity"]
                and th.linalg.vector_norm(robot.get_angular_velocity()).item() <= thresholds["angular_velocity"]
                and robot.get_joint_velocities().abs().max().item() <= thresholds["joint_velocity"]
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


def prepare_intervents(session, inventory):
    count = session.config["instance_sampling"]["count"]
    results, rejected = {}, []
    assets = read_json(session.paths.resolve(session.config["assets"]["manifest"]))
    for key, task in inventory["tasks"].items():
        results[key] = []
        for candidate in task["candidates"]:
            if len(results[key]) == count:
                break
            source = session.paths.resolve(candidate["hdf5"])
            name = source.stem
            directory = session.paths.output_root / "segments" / session.session_id / key / name
            if directory.exists():
                raise FileExistsError(
                    f"Use a fresh output root or explicitly remove failed candidate output: {directory}"
                )
            try:
                inspected = inspect_source(
                    source,
                    session.segments[key],
                    session.config["scene_model"],
                    session.segments[key].get("extra_objects", []),
                    candidate["demo"],
                )
                if not set(inspected["used"]).issubset(assets["intervent_objects"][key]):
                    raise ValueError("Source uses assets outside the frozen session")
                normalized = normalize_recording(session, source, directory / "normalized.hdf5", candidate["demo"])
                snapshots, validation = audit_boundaries(session, source, normalized, candidate["demo"])
                write_json(directory / "entry.json", snapshots[0])
                write_json(directory / "exit.json", snapshots[1])
                reader = FrameReader(normalized)
                changed = set()
                tolerance = session.config["validation"]["state_tolerances"]
                try:
                    baseline = scene_objects(snapshots[0])
                    for frame in range(len(reader.group["action"]) + 1):
                        current = scene_objects(reader.state(frame))
                        for obj in inspected["used"]:
                            if not state_matches(current[obj], baseline[obj], tolerance):
                                changed.add(obj)
                finally:
                    reader.close()
                reference = {
                    "hdf5": str(source),
                    "demo": candidate["demo"],
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
                results[key].append(session.paths.relative(output))
            except (ValueError, KeyError) as error:
                rejected.append({"segment_id": key, "hdf5": str(source), "reason": str(error)})
        if len(results[key]) != count:
            write_json(
                session.paths.output_root / "reports" / session.session_id / f"{key}_selection_failure.json",
                {"selected": results[key], "required": count, "rejected": rejected},
            )
            raise ValueError(
                f"Only {len(results[key])}/{count} compatible sources for {key}; no instance was silently dropped"
            )
    write_json(session.paths.output_root / "sessions" / session.session_id / "intervent_segments.json", results)
    write_json(
        session.paths.output_root / "reports" / session.session_id / "intervent_selection.json",
        {"selected": results, "rejected": rejected},
    )
    return results


def match_sources(session, instances, joint_segments, intervening_segments, order_indices):
    """Use each valid source once; order allocation is supplied independently of memory cases."""
    count = session.config["instance_sampling"]["count"]
    if len(instances) != count or len(joint_segments) != count or len(order_indices) != count:
        raise ValueError("Pairing inputs must contain exactly the configured instance count")
    if any(len(paths) != count for paths in intervening_segments.values()):
        raise ValueError("Each I must provide one distinct source per instance")
    pairings = []
    for index, instance in enumerate(instances):
        order = session.config["composition"]["task_orders"][order_indices[index]]
        session.validate_order(order)
        pairings.append(
            {
                "episode_id": f"episode_{index:08d}",
                "instance": instance["manifest"],
                "scene": instance["scene"],
                "task_order": order,
                "segments": {
                    **joint_segments[index],
                    **{key: paths[index] for key, paths in intervening_segments.items()},
                },
            }
        )
    return pairings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("inventory")
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    print(json.dumps(prepare_intervents(session, read_json(session.paths.resolve(args.inventory))), indent=2))


if __name__ == "__main__":
    main()
