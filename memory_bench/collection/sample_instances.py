"""Check assigned instances in simulation and save one shared template plus final TROs."""

import argparse
import json
from pathlib import Path
import traceback

import numpy as np

from memory_bench.collection.prepare_session import (
    _remap_tro,
    _template_bindings,
    _write_prepared_json,
    assemble_scene_assets,
    validate_assignments,
)
from memory_bench.session import Session
from memory_bench.utils.instance_scene import build_instance_tro
from memory_bench.utils.paths import _scene_json_dir, _task_instance_root
from memory_bench.utils.scene_state import (
    file_hash,
    merge_scene_assets,
    read_json,
    robot_metadata,
    scene_objects,
)


def _template_path(session, activity):
    return _scene_json_dir(session, _task_instance_root(session)) / (
        f"{session.task.scene_model}_task_{activity}_0_0_template.json"
    )


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
    result = {key: pose[key] for key in ("position", "orientation")}
    if "joint_positions" in pose:
        result["joint_positions"] = pose["joint_positions"]
    return result


def _required_task_objects(session, instance):
    bindings = instance["bindings"]
    activities_by_segment = {
        segment_id: segment["activity"]
        for segment_id, segment in session.segments.items()
        if segment["role"] in {"key_task", "query_task"}
    }
    sources = {}
    for segment_id, activity in activities_by_segment.items():
        template_path = _template_path(session, activity)
        if not template_path.is_file():
            raise FileNotFoundError(
                f"Missing full task template {template_path}; run the OmniGibson task sampler and "
                f"sample_robot_pose.py for activity {activity} before preparing instances"
            )
        sources[segment_id] = read_json(template_path)

    names = {}
    for segment_id, activity in activities_by_segment.items():
        if segment_id not in bindings:
            raise ValueError(f"Frozen case bindings are missing segment {segment_id}")
        for bddl_name, object_name in bindings[segment_id].items():
            if bddl_name.startswith("agent.n."):
                continue
            names.setdefault(object_name, []).append(segment_id)
    return names, sources, activities_by_segment


def _enrich_stable_scene(session, instance, stable_snapshot, asset_sources):
    """Add only task assets absent from the stable scene, preserving every shared state."""
    names, sources, activities_by_segment = _required_task_objects(session, instance)
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


def _set_activity(env, activity, bindings):
    env.scene.write_task_metadata(key="inst_to_name", data=bindings)
    env.task.update_activity(env, activity_name=activity, activity_definition_id=0)
    success, feedback = env.task.initialize_activity(env)
    if not success:
        raise ValueError(f"Could not bind {activity} to the frozen scene: {feedback}")
    if env.task.feedback and any(item is not None for item in env.task.feedback):
        raise ValueError(f"Cached initializer rejected {activity}: {env.task.feedback}")


def _initial_conditions(task):
    evaluate = lambda predicate_name, *entities: task._evaluate_predicate(0, predicate_name, *entities)
    satisfied, results = task.compiled_task.check_initial_conditions(evaluate)
    if not satisfied:
        raise ValueError(f"First-Key initial BDDL predicates are not satisfied: {results['unsatisfied']}")
    return {"satisfied": results["satisfied"], "unsatisfied": results["unsatisfied"]}


def _load_tro_state(env, tro):
    from omnigibson.utils.python_utils import recursively_convert_to_torch

    robot = env.scene.robots[0]
    robot.reset()
    robot_pose = _robot_pose(tro.get("robot_poses"), "First-Key TRO")
    robot.set_position_orientation(robot_pose["position"], robot_pose["orientation"], frame="scene")
    env.scene.write_task_metadata(key="robot_poses", data=tro["robot_poses"])
    object_scope = env.task.object_scopes[0]
    state = recursively_convert_to_torch(tro)
    for instance_name, object_state in state.items():
        if instance_name in {"robot_poses", "inst_to_name"}:
            continue
        if instance_name not in object_scope or object_scope[instance_name] is None:
            raise ValueError(f"First-Key TRO state has no bound scene object for {instance_name}")
        object_scope[instance_name].load_state(object_state, serialized=False)
    env.task.update_bddl_scope_metadata(env, 0)
    return robot_pose


def _settle(env):
    import omnigibson as og

    for _ in range(120):
        og.sim.step_physics()


def _check_start_boundary(robot):
    import torch as th
    from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim

    if detect_robot_collision_in_sim(robot):
        raise ValueError("First-Key initial boundary has a robot collision")
    if any(obj is not None for obj in robot._ag_obj_in_hand.values()):
        raise ValueError("First-Key initial boundary requires both hands empty")

    joint_indices = th.as_tensor(robot_metadata(robot)["controlled_joint_indices"], dtype=th.long)
    joint_speed = float(th.max(th.abs(robot.get_joint_velocities()[joint_indices])))
    linear_speed = float(robot.get_linear_velocity().norm())
    angular_speed = float(robot.get_angular_velocity().norm())
    limits = {"joint_velocity": 0.05, "linear_velocity": 0.03, "angular_velocity": 0.05}
    measured = {
        "joint_velocity": joint_speed,
        "linear_velocity": linear_speed,
        "angular_velocity": angular_speed,
    }
    for name, value in measured.items():
        limit = limits[name]
        if value > limit:
            raise ValueError(f"First-Key initial boundary is unstable: {name}={value:.4f} exceeds {limit:.4f}")
    return {"empty_hands": True, "collision_free": True, "stable": True, "measured_speeds": measured, "limits": limits}


def sample_instances(
    session: Session,
    assignments_path: Path | None = None,
    output_root: Path | None = None,
    inventory_path: Path | None = None,
) -> tuple[Path, Path]:
    """Read frozen assignments, check instances, and publish each completed TRO once."""
    session.paths.activate()
    session_root = session.paths.resolve(f"sessions/{session.session_id}")
    assignments_path = session_root / "case_assignments.json" if assignments_path is None else Path(assignments_path)
    assignments = read_json(assignments_path)
    if assignments.get("status") != "variants_assigned":
        raise ValueError("Run prepare_session assign-variants with a fresh output_root before sampling instances")
    validate_assignments(session, assignments)
    output_root = session_root if output_root is None else Path(output_root)
    template_path = output_root / "scene_template.json"
    tro_root = output_root / "tro"
    inventory_path = (
        session.paths.resolve(f"reports/{session.session_id}/source_inventory.json")
        if inventory_path is None
        else Path(inventory_path)
    )
    inventory = read_json(inventory_path)
    if inventory["session_id"] != session.session_id:
        raise ValueError("Source inventory belongs to another session")
    preparation_inputs = {
        "assignments": str(assignments_path.resolve()),
        "assignments_sha256": file_hash(assignments_path),
        "inventory": str(inventory_path.resolve()),
        "inventory_sha256": file_hash(inventory_path),
        "collection_start": "first_key_robot_state",
    }
    template = None
    if template_path.exists():
        template = read_json(template_path)
        metadata = template["metadata"]["task"]
        if metadata["memory_bench_inputs"] != preparation_inputs:
            raise ValueError("Shared template inputs changed; use a separate output_root")
        for path, expected in metadata["memory_bench_sources"].items():
            if file_hash(path) != expected:
                raise ValueError(f"Intervention source changed after preparation: {path}")
        _write_prepared_json(session.paths.resolve(session.assets["manifest"]), metadata["memory_bench_assets"])
    completed = {}
    template_sha256 = file_hash(template_path) if template is not None else None
    expected_files = {tro_root / f"instance_{entry['instance_id']:04d}.json" for entry in assignments["instances"]}
    if set(tro_root.glob("instance_*.json")) - expected_files:
        raise ValueError("TRO directory contains instances outside the frozen assignments")
    for entry in assignments["instances"]:
        path = tro_root / f"instance_{entry['instance_id']:04d}.json"
        if not path.exists():
            continue
        if template is None:
            raise ValueError("Completed TROs require their shared scene template")
        record = read_json(path)
        if record["scene_template_sha256"] != template_sha256:
            raise ValueError("Shared scene template changed after instance preparation")
        if record["assignments_sha256"] != preparation_inputs["assignments_sha256"]:
            raise ValueError("Variant assignments changed after instance preparation")
        if record["session_id"] != session.session_id:
            raise ValueError(f"Completed TRO belongs to another session: {path}")
        for field in ("instance_id", "case_id", "bindings", "source_task_instance_id"):
            if record[field] != entry[field]:
                raise ValueError(f"Completed TRO {field} does not match the assignment: {path}")
        completed[entry["instance_id"]] = record
    if len(completed) == len(assignments["instances"]):
        print(f"Reusing completed instances: {tro_root}")
        return template_path, tro_root

    first_key_id = session.key_ids[0]
    first_activity = session.segments[first_key_id]["activity"]
    json_dir = _scene_json_dir(session, _task_instance_root(session))
    stable = read_json(json_dir / f"{session.task.scene_model}_stable.json")
    enriched, templates = _enrich_stable_scene(
        session, assignments["instances"][0], stable, assignments["asset_sources"]
    )
    assets = None
    source_hashes = {}
    if template is None:
        enriched, assets = assemble_scene_assets(session, assignments, enriched, inventory)
        source_hashes = {source["hdf5"]: file_hash(source["hdf5"]) for source in assets["intervent_sources"].values()}
        task_metadata = enriched.setdefault("metadata", {}).setdefault("task", {})
        task_metadata["inst_to_name"] = assignments["instances"][0]["bindings"][first_key_id]
    original_first_key_bindings = _template_bindings(session, json_dir, first_activity)
    template_start_pose = _robot_pose(templates[first_key_id]["metadata"]["task"]["robot_poses"], first_key_id)

    import omnigibson as og
    import torch as th
    from gello.utils.og_teleop_utils import generate_robot_config
    from omnigibson.macros import gm

    gm.HEADLESS = True
    gm.USE_GPU_DYNAMICS = False
    gm.ENABLE_OBJECT_STATES = True
    gm.ENABLE_TRANSITION_RULES = False
    robot_cfg = generate_robot_config(robot_type="r1pro", robot_name="robot")
    robot_cfg["obs_modalities"] = []
    robot_cfg["position"] = [-50.0, -50.0, -50.0]
    config = {
        "env": {"action_frequency": 30, "physics_frequency": 120},
        "scene": {
            "type": "InteractiveTraversableScene",
            "scene_file": template if template is not None else enriched,
            "scene_model": session.task.scene_model,
            "include_robots": False,
            "seg_map_resolution": 0.1,
            "trav_map_with_objects": False,
        },
        "robots": [robot_cfg],
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
        if template is None:
            first_entry = template_start_pose
            robot.set_position_orientation(first_entry["position"], first_entry["orientation"], frame="scene")
            _settle(env)
            template = env.scene.save(as_dict=True)
            template["robot"] = robot_metadata(robot)
            template = json.loads(json.dumps(template, default=lambda value: value.tolist()))
            metadata = template.setdefault("metadata", {}).setdefault("task", {})
            metadata.update(
                memory_bench_inputs=preparation_inputs,
                memory_bench_sources=source_hashes,
                memory_bench_assets=assets,
            )
            _write_prepared_json(template_path, template)
            _write_prepared_json(session.paths.resolve(session.assets["manifest"]), assets)
        else:
            env.scene.restore(template, update_initial_file=True)
        template_sha256 = file_hash(template_path)
        for key, value in template["metadata"]["task"].items():
            env.scene.write_task_metadata(key=key, data=value)
        # This common state is restored for every instance and on an interrupted rerun.
        from omnigibson.utils.python_utils import recursively_convert_to_torch

        env.scene.load_state(recursively_convert_to_torch(template["state"]), serialized=False)
        initial_scene_state = env.scene.dump_state(serialized=False)
        for entry in assignments["instances"]:
            instance_id = entry["instance_id"]
            if instance_id in completed:
                print(f"Reusing completed instance {instance_id}.")
                continue
            seed = int(session.instance_sampling["seed"]) + instance_id
            th.manual_seed(seed)
            np.random.seed(seed)
            env.scene.load_state(initial_scene_state, serialized=False)
            _set_activity(env, first_activity, entry["bindings"][first_key_id])
            tro = _remap_tro(
                read_json(entry["source_tro"]), original_first_key_bindings, entry["bindings"][first_key_id]
            )
            first_pose = _load_tro_state(env, tro)
            _settle(env)
            robot.set_position_orientation(first_pose["position"], first_pose["orientation"], frame="scene")
            _settle(env)
            # Check the common robot start and first-Key world before publishing the TRO.
            predicate_report = _initial_conditions(env.task)
            start_boundary = _check_start_boundary(robot)
            room = env.scene._seg_map.get_room_instance_by_point(first_pose["position"][:2])
            if room != session.memory_room:
                raise ValueError(f"Shared robot start is in {room!r}, expected {session.memory_room!r}")
            snapshot = env.scene.save(as_dict=True)
            snapshot["robot"] = robot_metadata(robot)
            snapshot = json.loads(json.dumps(snapshot, default=lambda value: value.tolist()))
            metadata = snapshot.setdefault("metadata", {}).setdefault("task", {})
            metadata.update(memory_bench_case=entry["case_id"], memory_bench_bindings=entry["bindings"])
            record = {
                "session_id": session.session_id,
                "instance_id": instance_id,
                "source_task_instance_id": entry["source_task_instance_id"],
                "case_id": entry["case_id"],
                "bindings": entry["bindings"],
                "scene_template": session.paths.relative(template_path),
                "scene_template_sha256": template_sha256,
                "assignments": session.paths.relative(assignments_path),
                "assignments_sha256": preparation_inputs["assignments_sha256"],
                "status": "simulator_checked_pending_physical_calibration",
                "seed": seed,
                "validation": {
                    "first_key_initial_predicates": predicate_report,
                    "first_key_live_boundary": True,
                    "empty_hands": start_boundary["empty_hands"],
                    "stable_state": start_boundary,
                    "shared_initial_robot_state": True,
                },
            }
            final_tro = build_instance_tro(template, snapshot, record)
            destination = tro_root / f"instance_{instance_id:04d}.json"
            _write_prepared_json(destination, final_tro)
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
    parser.add_argument("--assignments", type=Path, help="Frozen variant assignment JSON")
    parser.add_argument("--inventory", type=Path, help="Intervention source inventory JSON")
    parser.add_argument("--output-root", type=Path, help="Directory for the shared template and final TROs")
    args = parser.parse_args()
    sample_instances(Session.load(args.config), args.assignments, args.output_root, args.inventory)


if __name__ == "__main__":
    main()
