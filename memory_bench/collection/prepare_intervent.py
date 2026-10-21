"""Choose, preflight and fix raw intervention recordings before instance sampling."""

import argparse
from copy import deepcopy
import json
import traceback

import h5py

from memory_bench.session import Session
from memory_bench.utils.recordings import FrameReader, inspect_source
from memory_bench.utils.scene_state import read_json, robot_metadata, merge_scene_assets, write_json_atomic
from memory_bench.utils.segment_manifest import compatible_interfaces, load_intervent_assignments


def scan_sources(session):
    """Inventory I recordings by activity, scene, room and basic recording schema."""
    inventory_path = session.paths.resolve(f"reports/{session.session_id}/source_inventory.json")
    if inventory_path.exists():
        report = read_json(inventory_path)
        if report["session_id"] != session.session_id:
            raise ValueError(f"Source recording inventory belongs to a different session: {inventory_path}")
        print(f"Reusing source recording inventory: {inventory_path}")
        return report

    report = {"session_id": session.session_id, "tasks": {}}
    for key, segment in session.segments.items():
        if segment["role"] != "intervent_task":
            continue
        root = session.paths.intervent_rawdata_root / f"task-{segment['source']['task_id']:04d}"
        candidates, rejected = [], []
        for path in sorted(root.glob("episode_*.hdf5")):
            try:
                with h5py.File(path, "r") as stream:
                    demos = [
                        int(name.removeprefix("demo_"))
                        for name, group in stream["data"].items()
                        if name.startswith("demo_") and "action" in group and len(group["action"]) > 0
                    ]
                if not demos:
                    raise ValueError("No demo group contains actions")
                candidates.append(
                    inspect_source(path, segment, session.scene_model, segment.get("extra_objects", []), max(demos))
                )
            except (ValueError, KeyError, OSError) as error:
                rejected.append({"hdf5": str(path), "reason": str(error)})
        expected = session.intervent_selection["candidate_count_per_task"]
        report["tasks"][key] = {
            "available": len(candidates) + len(rejected),
            "compatible_metadata": len(candidates),
            "expected_candidates": expected,
            "candidate_shortfall": max(0, expected - len(candidates) - len(rejected)),
            "candidates": candidates,
            "rejected": rejected,
        }
    write_json_atomic(inventory_path, report)
    print(f"Source recording inventory saved: {inventory_path}")
    return report


def audit_boundaries(source, demo=0):
    import torch as th
    import omnigibson as og
    from omnigibson.utils.python_utils import recursively_convert_to_torch
    from bddl.condition_evaluation import evaluate_state

    reader = FrameReader(source, demo)
    metadata = reader.metadata
    reader.context.prepare(metadata)
    env = reader.context.env
    robot = env.scene.robots[0]
    validation = {"entry": True, "exit": True, "empty_hands": True, "stable": True}
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
    return snapshots, validation


def prepare_intervent(session, key, source, demo, *, allowed_objects, protected):
    source = session.paths.resolve(source).resolve()
    info = inspect_source(source, session.segments[key], session.scene_model,
                          session.segments[key].get("extra_objects", []), demo)
    if set(info["used"]) != set(allowed_objects) or set(info["used"]) & set(protected):
        raise ValueError("Intervention source assets conflict with the selected group")
    snapshots, validation = audit_boundaries(source, demo)
    return {"hdf5": str(source), "demo": demo, "validation": validation}, snapshots[0]


def prepare_intervents(session, inventory):
    root = session.paths.resolve(f"sessions/{session.session_id}")
    output_path = root / "intervent_assignments.json"
    if output_path.exists():
        return load_intervent_assignments(session)
    # Preserve the previous pipeline's actual source selection during migration.
    previous = root / "intervent_segments.json"
    if previous.exists():
        frozen = read_json(previous)
        instances = {}
        for instance_id, refs in frozen["instances"].items():
            instances[instance_id] = {}
            for key, path in refs.items():
                manifest = read_json(session.paths.resolve(path))
                instances[instance_id][key] = {
                    **{name: manifest["source"][name] for name in ("hdf5", "demo")},
                    "validation": manifest["validation"],
                }
        template_path = root / "scene_template.json"
        if template_path.exists():
            template = read_json(template_path)
            inputs = template["metadata"]["task"]["memory_bench_inputs"]
            if session.paths.resolve(inputs["intervents"]).resolve() != previous.resolve():
                raise ValueError("Existing template does not match the I allocation being migrated")
        write_json_atomic(output_path, {
            "session_id": session.session_id, "instances": instances,
        })
        if template_path.exists():
            inputs["intervents"] = session.paths.relative(output_path)
            write_json_atomic(template_path, template)
        return load_intervent_assignments(session)
    if inventory["session_id"] != session.session_id:
        raise ValueError("Source inventory belongs to another session")
    expected = {key for key, definition in session.segments.items() if definition["role"] == "intervent_task"}
    if set(inventory["tasks"]) != expected:
        raise ValueError("Source inventory must contain every configured I exactly once")
    assignments_path = root / "case_assignments.json"
    assignments = read_json(assignments_path)
    count = len(assignments["instances"])
    protected = {name for names in assignments["asset_requirements"].values() for name in names}
    candidates_by_key, occupied = {}, set(protected)
    for key, task in inventory["tasks"].items():
        groups = {}
        for candidate in task["candidates"]:
            groups.setdefault(tuple(sorted(candidate["used"])), []).append(candidate)
        if not groups:
            raise ValueError(f"No compatible source recordings for {key}")
        names, candidates = max(groups.items(), key=lambda item: len(item[1]))
        if set(names) & occupied:
            raise ValueError(f"Intervention assets overlap protected or other I assets: {key}")
        occupied.update(names)
        candidates_by_key[key] = (names, candidates)
    results, rejected = {}, []
    interface = None
    for key, (names, candidates) in candidates_by_key.items():
        results[key] = []
        seen = set()
        first_entry = None
        for candidate in candidates:
            if len(results[key]) == count:
                break
            source = session.paths.resolve(candidate["hdf5"]).resolve()
            identity = (str(source), candidate["demo"])
            if identity in seen:
                continue
            seen.add(identity)
            try:
                print(f"Checking {key}: {source.name}/demo_{candidate['demo']}", flush=True)
                reference, entry = prepare_intervent(
                    session, key, source, candidate["demo"], allowed_objects=names, protected=protected,
                )
                if interface is not None and not compatible_interfaces(interface, candidate["interface"]):
                    raise ValueError("Intervention sources have incompatible robot interfaces")
                if first_entry is not None:
                    merge_scene_assets(first_entry, entry, names)
                else:
                    first_entry = entry
                interface = candidate["interface"]
                results[key].append(reference)
            except (ValueError, KeyError) as error:
                rejected.append({"segment_id": key, "hdf5": str(source), "reason": str(error)})
                print(f"Rejected {key}: {source.name}: {error}", flush=True)
        if len(results[key]) != count:
            write_json_atomic(
                session.paths.output_root / "reports" / session.session_id / f"{key}_selection_failure.json",
                {"selected": results[key], "required": count, "rejected": rejected},
            )
            raise ValueError(
                f"Only {len(results[key])}/{count} compatible sources for {key}; no instance was silently dropped"
            )
    instances = {
        str(item["instance_id"]): {key: paths[index] for key, paths in results.items()}
        for index, item in enumerate(assignments["instances"])
    }
    write_json_atomic(output_path, {
        "session_id": session.session_id,
        "instances": instances,
    })
    return instances


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    root = session.paths.resolve(f"sessions/{session.session_id}")
    if (root / "intervent_assignments.json").exists() or (root / "intervent_segments.json").exists():
        print(json.dumps(prepare_intervents(session, {}), indent=2))
        return
    inventory = scan_sources(session)
    import omnigibson as og
    from omnigibson.macros import gm

    gm.HEADLESS = True
    gm.USE_GPU_DYNAMICS = False
    gm.ENABLE_OBJECT_STATES = True
    gm.ENABLE_TRANSITION_RULES = False
    try:
        print(json.dumps(prepare_intervents(session, inventory), indent=2))
    except Exception:
        traceback.print_exc()
        og.app._app.post_quit(1)
        raise
    finally:
        og.shutdown()


if __name__ == "__main__":
    main()
