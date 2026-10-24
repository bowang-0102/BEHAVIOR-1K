import argparse
from copy import deepcopy
import os
from pathlib import Path
import traceback

import numpy as np

from memory_bench.collection.prepare_session import assemble_scene_assets
from memory_bench.session import Session
from memory_bench.utils.recordings import FrameReader, RecordingContext
from memory_bench.utils.instance_scene import build_instance_tro, prepared_scene
from memory_bench.utils.scene_state import (
    get_state_tolerances,
    merge_scene_assets,
    read_json,
    require_objects_match,
    scene_objects,
    write_json_atomic,
)
from memory_bench.utils.segment_manifest import load_intervent_assignments
from memory_bench.utils.task_segments import describe_intervent


def _robot_pose(poses, label):
    if not isinstance(poses, dict):
        raise ValueError(f"{label} has no robot pose mapping")
    normalized = {str(key).lower(): value for key, value in poses.items()}
    available = normalized.get("robot") or normalized.get("r1pro")
    if not available:
        raise ValueError(f"{label} has no generic or R1Pro robot pose")
    pose = available[0]
    missing = {"position", "orientation"} - set(pose)
    if missing:
        raise ValueError(f"{label} pose is missing {sorted(missing)}")
    return {key: pose[key] for key in ("position", "orientation")}


def _enrich_stable_scene(session, instance, stable_snapshot, asset_sources):
    """Add only task assets absent from the stable scene, preserving every shared state."""
    bindings = instance["bindings"]
    activities_by_segment = {
        segment_id: segment["activity"]
        for segment_id, segment in session.segments.items()
        if segment["role"] in {"key_task", "query_task"}
    }
    sources = {}
    for segment_id, activity in activities_by_segment.items():
        template_path = session.paths.scene_json_dir / (
            f"{session.task.scene_model}_task_{activity}_0_0_template.json"
        )
        if not template_path.is_file():
            raise FileNotFoundError(
                f"Missing full task template {template_path}; run the OmniGibson task sampler and "
                f"sample_robot_pose.py for activity {activity} before preparing instances"
            )
        sources[segment_id] = read_json(template_path)

    names = {}
    for segment_id, activity in activities_by_segment.items():
        if segment_id not in bindings:
            raise ValueError(f"Frozen variant bindings are missing segment {segment_id}")
        for bddl_name, object_name in bindings[segment_id].items():
            if bddl_name.startswith("agent.n."):
                continue
            names.setdefault(object_name, []).append(segment_id)

    present = set(scene_objects(stable_snapshot))
    merged = stable_snapshot
    missing = sorted(set(names) - present)
    for object_name in missing:
        source_scene = None
        source_ids = [asset_sources[object_name]] if object_name in asset_sources else names[object_name]
        for segment_id in source_ids:
            candidate = sources[segment_id]
            if object_name in candidate.get("objects_info", {}).get("init_info", {}) and object_name in scene_objects(
                candidate
            ):
                source_scene = candidate
                break
        if source_scene is None:
            activity_sources = [f"{segment}: {activities_by_segment[segment]}" for segment in names[object_name]]
            raise ValueError(
                f"Task-only asset {object_name!r} is absent from the stable scene and its Key/Query templates "
                f"({', '.join(activity_sources)}); add an explicit object initializer/state template before sampling"
            )
        merged = merge_scene_assets(merged, source_scene, (object_name,))
    return merged, sources


def sample_instances(session: Session) -> tuple[Path, Path]:
    """Read frozen assignments, check instances, and publish each completed TRO once."""
    session_root = session.paths.session_dir
    assignments_path = session_root / "variant_assignments.json"
    assignments = read_json(assignments_path)
    if assignments.get("status") != "variants_assigned":
        raise ValueError("Run prepare_session assign-variants with a fresh output_root before sampling instances")
    if assignments["session_id"] != session.session_id:
        raise ValueError("Variant assignments belong to another session")
    template_path = session_root / "scene_template.json"
    tro_root = session_root / "tro"
    assets_path = session_root / "assets.json"
    if template_path.exists() or tro_root.exists() or assets_path.exists():
        raise FileExistsError(f"Prepared instances already exist; use a new output_root: {session_root}")
    intervents_path = session_root / "intervent_assignments.json"
    intervents = load_intervent_assignments(session)
    preparation_inputs = {
        "assignments": str(assignments_path.relative_to(session.paths.output_root)),
        "intervents": str(intervents_path.relative_to(session.paths.output_root)),
    }
    import omnigibson as og
    import torch as th
    from omnigibson.macros import gm

    gm.HEADLESS = True
    gm.USE_GPU_DYNAMICS = False
    gm.ENABLE_OBJECT_STATES = True
    gm.ENABLE_TRANSITION_RULES = False

    intervent_entries = {instance_id: {} for instance_id in intervents}
    with RecordingContext() as context:
        for key in next(iter(intervents.values())):
            for instance_id, sources in intervents.items():
                source = sources[key]
                segment = describe_intervent(session, key, source, int(instance_id))
                with FrameReader(session.paths.resolve(source["hdf5"]), source["demo"], context=context) as reader:
                    segment["entry"] = reader.snapshot(0)
                intervent_entries[instance_id][key] = segment

    first_key_id = session.key_ids[0]
    first_activity = session.segments[first_key_id]["activity"]
    json_dir = session.paths.scene_json_dir
    stable = read_json(json_dir / f"{session.task.scene_model}_stable.json")
    enriched, templates = _enrich_stable_scene(
        session, assignments["instances"][0], stable, assignments["asset_sources"]
    )
    first_id = str(assignments["instances"][0]["instance_id"])
    first_intervents = intervent_entries[first_id]
    enriched, assets = assemble_scene_assets(session, assignments, enriched, first_intervents)
    task_metadata = enriched.setdefault("metadata", {}).setdefault("task", {})
    task_metadata["inst_to_name"] = assignments["instances"][0]["bindings"][first_key_id]
    original_first_key_bindings = templates[first_key_id]["metadata"]["task"]["inst_to_name"]
    template_start_pose = _robot_pose(templates[first_key_id]["metadata"]["task"]["robot_poses"], first_key_id)

    config = {
        "env": {"action_frequency": 30, "physics_frequency": 120},
        "scene": {
            "type": "InteractiveTraversableScene",
            "scene_file": deepcopy(enriched),
            "scene_model": session.task.scene_model,
            "include_robots": False,
            "seg_map_resolution": 0.1,
            "trav_map_with_objects": False,
        },
        "robots": [{"type": "r1pro", "name": "robot", "obs_modalities": [], "position": [-50.0, -50.0, -50.0]}],
        "task": {
            "type": "BehaviorTask",
            "activity_name": first_activity,
            "activity_definition_id": 0,
            "activity_instance_id": 0,
            "online_object_sampling": False,
            "use_presampled_robot_pose": False,
            "include_obs": False,
        },
    }
    env = og.Environment(configs=config)
    try:
        robot = env.scene.robots[0]
        robot.set_position_orientation(template_start_pose["position"], template_start_pose["orientation"], frame="scene")
        for _ in range(120):
            og.sim.step_physics()
        template = prepared_scene(env.scene.save(as_dict=True), robot.name)
        write_json_atomic(assets_path, assets)
        metadata = template.setdefault("metadata", {}).setdefault("task", {})
        metadata.update(
            memory_bench_inputs=preparation_inputs,
            memory_bench_assets={"path": str(assets_path.relative_to(session.paths.output_root))},
        )
        write_json_atomic(template_path, template)
        for key, value in template["metadata"]["task"].items():
            env.scene.write_task_metadata(key=key, data=value)
        # Restore this common state before preparing each instance.
        from omnigibson.utils.python_utils import recursively_convert_to_torch

        runtime_template = merge_scene_assets(template, env.scene.save(as_dict=True), (robot.name,))
        env.scene.load_state(recursively_convert_to_torch(runtime_template["state"]), serialized=False)
        initial_scene_state = env.scene.dump_state(serialized=False)
        for entry in assignments["instances"]:
            instance_id = entry["instance_id"]
            seed = int(session.instance_sampling["seed"]) + instance_id
            th.manual_seed(seed)
            np.random.seed(seed)
            env.scene.load_state(initial_scene_state, serialized=False)
            bindings = entry["bindings"][first_key_id]
            env.scene.write_task_metadata(key="inst_to_name", data=bindings)
            env.task.update_activity(env, activity_name=first_activity, activity_definition_id=0)
            success, feedback = env.task.initialize_activity(env)
            if not success:
                raise ValueError(f"Could not bind {first_activity} to the frozen scene: {feedback}")
            tro = read_json(session.paths.resolve(entry["source_tro"]))
            first_pose = _robot_pose(tro.get("robot_poses"), "First-Key TRO")
            robot.set_position_orientation(first_pose["position"], first_pose["orientation"], frame="scene")
            env.scene.write_task_metadata(key="robot_poses", data=tro["robot_poses"])
            # TRO keys use the sampler's bindings; load each physical object's state
            # through its current variant binding without rewriting the TRO.
            original_bindings = tro.get("inst_to_name", original_first_key_bindings)
            states_by_object = {
                original_bindings[instance]: recursively_convert_to_torch(state)
                for instance, state in tro.items() if instance in original_bindings
            }
            for instance, name in bindings.items():
                if name in states_by_object:
                    env.task.object_scopes[0][instance].load_state(states_by_object[name], serialized=False)
            env.task.update_bddl_scope_metadata(env, 0)
            instance_intervents = {}
            for key, segment in intervent_entries[str(instance_id)].items():
                entry_scene = segment["entry"]
                names = segment["objects"]["used"]
                # Check shared initializers and load only this I's objects, never its robot or background.
                merge_scene_assets(template, entry_scene, names)
                for name in names:
                    state = recursively_convert_to_torch(deepcopy(scene_objects(entry_scene)[name]))
                    env.scene.object_registry("name", name).load_state(state, serialized=False)
                instance_intervents[key] = (entry_scene, names)
            for _ in range(120):
                og.sim.step_physics()
            robot.set_position_orientation(first_pose["position"], first_pose["orientation"], frame="scene")
            for _ in range(120):
                og.sim.step_physics()
            # Check the first-Key world and sampled base location before publishing the TRO.
            evaluate = lambda predicate_name, *entities: env.task._evaluate_predicate(0, predicate_name, *entities)
            satisfied, predicate_report = env.task.compiled_task.check_initial_conditions(evaluate)
            if not satisfied:
                raise ValueError(f"First-Key initial BDDL predicates are not satisfied: {predicate_report['unsatisfied']}")
            room = env.scene._seg_map.get_room_instance_by_point(first_pose["position"][:2])
            if room != session.memory_room:
                raise ValueError(f"Shared robot start is in {room!r}, expected {session.memory_room!r}")
            snapshot = prepared_scene(env.scene.save(as_dict=True), robot.name)
            for key, (entry_scene, names) in instance_intervents.items():
                require_objects_match(snapshot, entry_scene, names, get_state_tolerances(), f"{key} sampled entry")
            position, orientation = robot.get_position_orientation()
            record = {
                "session_id": session.session_id,
                "instance_id": instance_id,
                "scene_template": str(template_path.relative_to(session.paths.output_root)),
                "robot": {"position": position.tolist(), "orientation": orientation.tolist()},
                "validation": {
                    "first_key_initial_predicates": predicate_report,
                },
            }
            final_tro = build_instance_tro(template, snapshot, record)
            destination = tro_root / f"instance_{instance_id:04d}.json"
            write_json_atomic(destination, final_tro)
            print(f"Prepared instance {instance_id} of {len(assignments['instances'])}: {destination}")
    except Exception:
        traceback.print_exc()
        # Kit's fast shutdown must not turn a failed preparation into a successful process exit.
        og.app._app.post_quit(1)
        raise
    finally:
        env.close()
        og.shutdown()
    return template_path, tro_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Session YAML path")
    args = parser.parse_args()
    session = Session.load(args.config)
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)
    sample_instances(session)


if __name__ == "__main__":
    main()
