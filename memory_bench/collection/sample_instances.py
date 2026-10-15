"""Build full-scene snapshots and frozen robot entries from cached Key TROs."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path

import numpy as np

from memory_bench.collection.prepare_task import _scene_json_dir, _task_instance_root
from memory_bench.session import Session
from memory_bench.utils.state import (
    merge_scene_assets,
    read_json,
    robot_metadata,
    scene_objects,
    write_json,
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


def _open_doorways(scene, configuration):
    from omnigibson.object_states import Open

    setup = configuration.get("scene_preparation", {}).get("doorways", {})
    if not setup.get("open_before_snapshot", True):
        return []
    categories = set(setup.get("categories", ["door", "interior_door", "doorway"]))
    opened = []
    for obj in scene.objects:
        if obj.category not in categories or Open not in obj.states:
            continue
        if not obj.states[Open].get_value():
            if not obj.states[Open].set_value(True):
                raise ValueError(f"Could not open configured doorway object {obj.name}")
            opened.append(obj.name)
    return opened


def _initial_conditions(task):
    evaluate = lambda predicate_name, *entities: task._evaluate_predicate(0, predicate_name, *entities)
    satisfied, results = task.compiled_task.check_initial_conditions(evaluate)
    if not satisfied:
        raise ValueError(f"First-Key initial BDDL predicates are not satisfied: {results['unsatisfied']}")
    return {"satisfied": results["satisfied"], "unsatisfied": results["unsatisfied"]}


def _nearest_floor(scene, height):
    floor_heights = scene.trav_map.floor_heights
    if hasattr(floor_heights, "detach"):
        floor_heights = floor_heights.detach().cpu().numpy()
    return int(np.argmin(np.abs(np.asarray(floor_heights) - height)))


def _sample_query_start(scene, robot, room_id, seed, reference_pose, max_attempts=1000):
    import torch as th

    th.manual_seed(seed)
    floor = _nearest_floor(scene, reference_pose[2])
    rng = np.random.default_rng(seed)
    for _ in range(max_attempts):
        _, position = scene.get_random_point(
            floor=floor,
            robot=robot,
        )
        position = np.asarray(position, dtype=float)
        if scene._seg_map.get_room_instance_by_point(position[:2]) != room_id:
            continue
        yaw = float(rng.uniform(-np.pi, np.pi))
        return {
            "position": position.tolist(),
            "orientation": [0.0, 0.0, float(np.sin(yaw / 2)), float(np.cos(yaw / 2))],
        }
    raise ValueError(f"Could not sample an answer-independent free pose in {room_id} after {max_attempts} attempts")


@contextmanager
def _with_current_occupancy(scene, robot, floor):
    from memory_bench.utils.navigation_map import current_floor_map

    traversability = scene.trav_map
    original_map = traversability.floor_map[floor]
    traversability.floor_map[floor] = current_floor_map(scene, robot, floor)
    try:
        yield
    finally:
        traversability.floor_map[floor] = original_map


def _require_route(scene, robot, source, target, label):
    import torch as th

    floor = _nearest_floor(scene, target["position"][2])
    with _with_current_occupancy(scene, robot, floor):
        path, _ = scene.get_shortest_path(
            floor=floor,
            source_world=th.as_tensor(source["position"][:2], dtype=th.float32),
            target_world=th.as_tensor(target["position"][:2], dtype=th.float32),
            entire_path=True,
            robot=robot,
        )
    if path is None:
        raise ValueError(f"No traversable route between frozen entry poses for {label}")
    return {"reachable": True, "waypoints": len(path)}


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


def _check_start_boundary(robot, thresholds):
    import torch as th

    if any(obj is not None for obj in robot._ag_obj_in_hand.values()):
        raise ValueError("First-Key initial boundary requires both hands empty")

    joint_indices = th.as_tensor(robot_metadata(robot)["controlled_joint_indices"], dtype=th.long)
    joint_speed = float(th.max(th.abs(robot.get_joint_velocities()[joint_indices])))
    linear_speed = float(robot.get_linear_velocity().norm())
    angular_speed = float(robot.get_angular_velocity().norm())
    limits = thresholds.get("robot_boundary", {})
    measured = {
        "joint_velocity": joint_speed,
        "linear_velocity": linear_speed,
        "angular_velocity": angular_speed,
    }
    for name, value in measured.items():
        limit = float(
            limits.get(name, {"joint_velocity": 0.05, "linear_velocity": 0.03, "angular_velocity": 0.05}[name])
        )
        if value > limit:
            raise ValueError(f"First-Key initial boundary is unstable: {name}={value:.4f} exceeds {limit:.4f}")
    return {"empty_hands": True, "stable": True, "measured_speeds": measured, "limits": limits}


def sample_instances(
    session: Session,
    assignments_path: Path | None = None,
    output_root: Path | None = None,
    query_pose_attempts: int = 1000,
) -> tuple[Path, Path]:
    """Sample full initial scenes and non-answer-conditioned Key/Query entry poses.

    This stage uses cached first-Key TROs for task state. It does not independently
    sample Query object states; Query's state follows the Key sequence during collection.
    """
    import omnigibson as og
    import torch as th
    from gello.utils.og_teleop_utils import generate_robot_config
    from omnigibson.macros import gm

    session.paths.activate()
    session_root = session.paths.resolve(f"sessions/{session.session_id}")
    assignments_path = session_root / "case_assignments.json" if assignments_path is None else Path(assignments_path)
    assignments = read_json(assignments_path)
    if not assignments.get("status", "").startswith("cases_frozen"):
        raise ValueError("assign-variants must complete before sample_instances")
    output_root = session_root / "sampled" if output_root is None else Path(output_root)
    scene_root = output_root / "scene_snapshots"
    pose_manifest_path = output_root / "poses.json"
    if pose_manifest_path.exists() or scene_root.exists() and any(scene_root.iterdir()):
        raise FileExistsError(f"Refusing to replace sampled instance output under {output_root}")
    scene_root.mkdir(parents=True, exist_ok=True)

    first_key_id = session.key_ids[0]
    first_activity = session.segments[first_key_id]["activity"]
    first_instance = assignments["instances"][0]
    stable_path = (
        _task_instance_root(session)
        / "scenes"
        / session.task.scene_model
        / "json"
        / f"{session.task.scene_model}_stable.json"
    )
    if not stable_path.is_file():
        raise FileNotFoundError(f"Stable full-room scene is missing: {stable_path}; run prepare_task.py init")
    stable_snapshot = read_json(stable_path)

    # Enrich only task-only assets absent from the stable scene. Existing scene state
    # is never replaced by a task template's copy.
    enriched, templates = _enrich_stable_scene(session, first_instance, stable_snapshot, assignments["asset_sources"])
    task_metadata = enriched.setdefault("metadata", {}).setdefault("task", {})
    task_metadata["inst_to_name"] = first_instance["bindings"][first_key_id]
    preparation_input = output_root / "sampling_scene.json"
    preparation_input.parent.mkdir(parents=True, exist_ok=True)
    if preparation_input.exists():
        if json.loads(preparation_input.read_text()) != enriched:
            raise ValueError(f"Existing sampling input differs from the current task templates: {preparation_input}")
    else:
        write_json(preparation_input, enriched)

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
            "scene_file": str(preparation_input.resolve()),
            "scene_model": session.task.scene_model,
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
    all_pose_records = {}
    try:
        task_template_poses = {}
        for key_id in session.key_ids:
            activity = session.segments[key_id]["activity"]
            pose_mapping = templates[key_id].get("metadata", {}).get("task", {}).get("robot_poses")
            if pose_mapping is None:
                raise ValueError(
                    f"Full task template has no sampled robot entry: {_template_path(session, activity)}; "
                    "run sample_robot_pose.py"
                )
            task_template_poses[key_id] = _robot_pose(pose_mapping, activity)

        opened_doorways = _open_doorways(env.scene, session.config)
        _settle(env)
        robot = env.scene.robots[0]
        query_reference = task_template_poses[first_key_id]["position"]
        robot.set_position_orientation(
            task_template_poses[first_key_id]["position"],
            task_template_poses[first_key_id]["orientation"],
            frame="scene",
        )
        query_z = float(query_reference[2])
        query_floor = _nearest_floor(env.scene, query_z)
        query_seed_base = int(
            session.config["query_start"].get("seed", session.config["instance_sampling"]["seed"] + 7919)
        )
        query_starts = {}
        used_query_positions = set()
        for instance in assignments["instances"]:
            instance_id = int(instance["instance_id"])
            for retry in range(100):
                query_seed = query_seed_base + instance_id * 1_000_003 + retry
                with _with_current_occupancy(env.scene, robot, query_floor):
                    query_pose = _sample_query_start(
                        env.scene,
                        robot,
                        session.config["query_start"]["room_id"],
                        query_seed,
                        query_reference,
                        max_attempts=query_pose_attempts,
                    )
                position_key = tuple(round(float(value), 3) for value in query_pose["position"])
                if position_key not in used_query_positions:
                    used_query_positions.add(position_key)
                    query_pose["joint_positions"] = robot.get_joint_positions().detach().cpu().tolist()
                    query_starts[instance_id] = (query_pose, query_seed)
                    break
            else:
                raise ValueError(f"Could not sample a unique Query start pose for instance {instance_id}")
        initial_scene_state = env.scene.dump_state(serialized=False)
        for instance in assignments["instances"]:
            instance_id = int(instance["instance_id"])
            case_id = instance["case_id"]
            if instance["bindings"].get(first_key_id) is None:
                raise ValueError(f"Instance {instance_id} is missing bindings for {first_key_id}")
            seed = int(session.config["instance_sampling"]["seed"]) + instance_id
            th.manual_seed(seed)
            np.random.seed(seed)
            env.scene.load_state(initial_scene_state, serialized=False)
            env.scene.write_task_metadata(key="memory_bench_case", data=case_id)
            env.scene.write_task_metadata(key="memory_bench_bindings", data=instance["bindings"])
            _set_activity(env, first_activity, instance["bindings"][first_key_id])
            tro = read_json(instance["tro"])
            first_pose = _load_tro_state(env, tro)
            _settle(env)

            predicate_report = _initial_conditions(env.task)
            start_boundary = _check_start_boundary(robot=env.scene.robots[0], thresholds=session.config["validation"])

            robot = env.scene.robots[0]
            key_entries = {}
            for key_id in session.key_ids:
                pose = dict(first_pose if key_id == first_key_id else task_template_poses[key_id])
                pose["joint_positions"] = robot.get_joint_positions().detach().cpu().tolist()
                pose_room = env.scene._seg_map.get_room_instance_by_point(pose["position"][:2])
                if pose_room != session.segments[key_id]["room_id"]:
                    raise ValueError(
                        f"Cached robot pose for {key_id} is in {pose_room!r}, "
                        f"expected {session.segments[key_id]['room_id']!r}"
                    )
                key_entries[key_id] = pose

            query_start, query_seed = query_starts[instance_id]

            route_reports = {}
            route_source = key_entries[first_key_id]
            for key_id in session.key_ids[1:]:
                route_reports[f"{first_key_id}->{key_id}"] = _require_route(
                    env.scene, robot, route_source, key_entries[key_id], f"{first_key_id} to {key_id}"
                )
                route_source = key_entries[key_id]
            route_reports[f"{session.key_ids[-1]}->{session.query_id}"] = _require_route(
                env.scene, robot, route_source, query_start, "last Key to Query"
            )

            # Keep the in-scene sample at the first Key entry so the full snapshot
            # has a navigable robot base pose and its loaded R1Pro posture.
            robot.set_position_orientation(first_pose["position"], first_pose["orientation"], frame="scene")
            _settle(env)
            scene_snapshot = env.scene.save(as_dict=True)
            task_metadata = scene_snapshot.setdefault("metadata", {}).setdefault("task", {})
            task_metadata["memory_bench_case"] = case_id
            task_metadata["memory_bench_bindings"] = instance["bindings"]
            task_metadata["memory_bench_doorways_opened"] = opened_doorways
            scene_snapshot["robot"] = robot_metadata(robot)

            destination = scene_root / f"instance_{instance_id:04d}" / "scene.json"
            write_json(destination, scene_snapshot)
            all_pose_records[str(instance_id)] = {
                "case_id": case_id,
                "query_start": query_start,
                "query_start_seed": query_seed,
                "key_entries": key_entries,
                "seed": seed,
                "validation": {
                    "first_key_initial_predicates": predicate_report,
                    "first_key_live_boundary": True,
                    "empty_hands": start_boundary["empty_hands"],
                    "stable_state": start_boundary,
                    "query_start_sampled_before_case_tro": True,
                    "navigation_path_preflight": route_reports,
                    "doorways_opened": opened_doorways,
                    "status": "simulator_checked_pending_physical_calibration",
                },
            }
            print(f"Sampled initial full scene and entries for {instance_id}/{len(assignments['instances'])}")

        write_json(
            pose_manifest_path,
            {
                "session_id": session.session_id,
                "status": "simulator_sampled_pending_physical_calibration",
                "room_free_query_sampler": session.config["query_start"],
                "query_sampling_independent_of_case": True,
                "query_start_shared_across_instances": False,
                "query_start_unique_positions": len(used_query_positions) == len(assignments["instances"]),
                "query_start_seed_base": query_seed_base,
                "physical_calibration": {"status": "pending"},
                "instances": all_pose_records,
            },
        )
    finally:
        env.close()
        og.shutdown()
    print(f"Sampled scene snapshots: {scene_root}")
    print(f"Sampled entry poses: {pose_manifest_path}")
    return scene_root, pose_manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Session YAML path")
    parser.add_argument("--assignments", type=Path, help="Frozen case assignment JSON")
    parser.add_argument("--output-root", type=Path, help="Output directory for scene snapshots and poses")
    parser.add_argument("--query-pose-attempts", type=int, default=1000)
    args = parser.parse_args()
    session = Session.load(args.config)
    sample_instances(session, args.assignments, args.output_root, args.query_pose_attempts)


if __name__ == "__main__":
    main()
