"""Register sampler inputs and freeze Memory Bench instance bindings."""

import argparse
from copy import deepcopy
import json
import re
import shutil
from pathlib import Path

from memory_bench.session import Session
from memory_bench.utils.paths import TASK_INSTANCES_DATASET


def _task_instance_root(session: Session) -> Path:
    return session.paths.data_root / TASK_INSTANCES_DATASET


def _scene_json_dir(session: Session, task_instance_root: Path) -> Path:
    return task_instance_root / "scenes" / session.task.scene_model / "json"


def _task_activities(session: Session) -> list[str]:
    return [*session.task.key_activities, session.task.query_activity]


def _validate_session_task(session: Session) -> None:
    task = session.task
    segment_activities = {
        segment["activity"] for segment in session.segments.values() if segment["role"] in {"key_task", "query_task"}
    }
    expected = {*task.key_activities, task.query_activity}
    if segment_activities != expected:
        raise ValueError(f"Session task activities {sorted(segment_activities)} do not match {sorted(expected)}")
    if len(session.key_ids) != len(task.key_activities):
        raise ValueError("The session Key count does not match its task definition")


def init(session: Session) -> None:
    """Register every Key and Query activity with OmniGibson's task sampler."""
    _validate_session_task(session)
    session.paths.activate()
    task_instance_root = _task_instance_root(session)
    metadata_dir = task_instance_root / "metadata"
    custom_lists_path = metadata_dir / "task_custom_lists.json"
    custom_lists = json.loads(custom_lists_path.read_text()) if custom_lists_path.exists() else {}

    for activity in _task_activities(session):
        activity_spec = {
            "room_types": list(session.task.room_types),
            session.task.scene_model: {"whitelist": session.task.whitelist_for(activity), "blacklist": {}},
        }
        if activity in custom_lists and custom_lists[activity] != activity_spec:
            raise ValueError(f"Existing sampler registration differs for {activity}")
        custom_lists[activity] = activity_spec

    metadata_dir.mkdir(parents=True, exist_ok=True)
    custom_lists_path.write_text(json.dumps(custom_lists, indent=4))

    stable_name = f"{session.task.scene_model}_stable.json"
    official_scene = (
        session.paths.data_root
        / "2026-challenge-task-instances"
        / "scenes"
        / session.task.scene_model
        / "json"
        / stable_name
    )

    target_dir = _scene_json_dir(session, task_instance_root)
    target_dir.mkdir(parents=True, exist_ok=True)
    stable_scene = target_dir / stable_name
    if stable_scene.exists():
        if stable_scene.read_bytes() != official_scene.read_bytes():
            raise ValueError(f"Refusing to replace a different stable scene: {stable_scene}")
    else:
        shutil.copy2(official_scene, stable_scene)

    session_root = session.paths.resolve(f"sessions/{session.session_id}")
    session_root.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "session_id": session.session_id,
        "config": session.config,
        "resolved_paths": {
            "data_root": str(session.paths.data_root),
            "intervent_rawdata_root": str(session.paths.intervent_rawdata_root),
            "output_root": str(session.paths.output_root),
            "task_instances_root": str(task_instance_root),
        },
    }
    session_path = session_root / "session.json"
    encoded = json.dumps(snapshot, indent=2)
    if session_path.exists():
        if session_path.read_text() != encoded:
            raise ValueError(f"Session configuration is already frozen at {session_path}")
    else:
        session_path.write_text(encoded)

    print(f"Registered {len(_task_activities(session))} activities in {custom_lists_path}")
    print(f"Copied stable scene to {stable_scene}")
    print(f"Saved session snapshot to {session_path}")


def _template_bindings(session: Session, json_dir: Path, activity: str) -> dict[str, str]:
    template_path = json_dir / f"{session.task.scene_model}_task_{activity}_0_0_template.json"
    template = json.loads(template_path.read_text())
    return template["metadata"]["task"]["inst_to_name"]


def _bindings_for_case(session: Session, json_dir: Path, case_id: str) -> dict[str, dict[str, str]]:
    task = session.task
    bindings = {}
    for segment_id, segment in session.segments.items():
        if segment["role"] not in {"key_task", "query_task"}:
            continue
        activity = segment["activity"]
        base = _template_bindings(session, json_dir, activity)
        bindings[segment_id] = task.bind_variant(base, case_id, activity)
    return bindings


def _remap_tro(tro_state: dict, template_bindings: dict[str, str], new_bindings: dict[str, str]) -> dict:
    tro_state = deepcopy(tro_state)
    original_bindings = tro_state.pop("inst_to_name", template_bindings)
    original_state_by_object = {
        original_bindings[instance]: tro_state[instance] for instance in original_bindings if instance in tro_state
    }
    for instance, object_name in new_bindings.items():
        if object_name in original_state_by_object:
            tro_state[instance] = original_state_by_object[object_name]
        else:
            tro_state.pop(instance, None)
    tro_state["inst_to_name"] = new_bindings
    return tro_state


def _pose_fields(pose: dict, label: str) -> dict:
    required = {"position", "orientation"}
    missing = required - set(pose)
    if missing:
        raise ValueError(f"{label} is missing pose fields: {sorted(missing)}")
    fields = {key: pose[key] for key in ("position", "orientation")}
    if "joint_positions" in pose:
        fields["joint_positions"] = pose["joint_positions"]
    return fields


def freeze_cases(session: Session, seed: int | None = None) -> None:
    """Balance cases over generated first-Key TROs and freeze all segment bindings."""
    _validate_session_task(session)
    seed = (
        session.config.get("case_sampling", {}).get("seed", session.config["instance_sampling"]["seed"])
        if seed is None
        else seed
    )
    count = session.config["instance_sampling"]["count"]
    task_instance_root = _task_instance_root(session)
    json_dir = _scene_json_dir(session, task_instance_root)
    first_key_id = session.key_ids[0]
    first_key_activity = session.segments[first_key_id]["activity"]
    prefix = f"{session.task.scene_model}_task_{first_key_activity}"
    instance_dir = json_dir / f"{prefix}_instances"
    pattern = re.compile(rf"{re.escape(prefix)}_0_(\d+)_template-tro_state\.json")
    tro_files = {
        int(match.group(1)): path
        for path in instance_dir.glob("*-tro_state.json")
        if (match := pattern.fullmatch(path.name))
    }
    if len(tro_files) < count:
        raise ValueError(f"Need {count} sampled {first_key_activity} instances; found {len(tro_files)}")

    session_root = session.paths.resolve(f"sessions/{session.session_id}")
    assignment_path = session_root / "case_assignments.json"
    instances_path = session_root / "instances.json"
    if assignment_path.exists() or instances_path.exists():
        raise ValueError(f"Case assignments are already frozen for {session.session_id}")

    assignments = session.task.sample_cases(count, seed)
    template_bindings = _template_bindings(session, json_dir, first_key_activity)
    prepared = []
    tro_output_dir = session_root / "tro"
    tro_output_dir.mkdir(parents=True, exist_ok=True)
    for instance_id, (source_id, tro_path) in enumerate(sorted(tro_files.items())[:count], start=1):
        case_id = assignments[instance_id]
        segment_bindings = _bindings_for_case(session, json_dir, case_id)
        first_key_bindings = segment_bindings[first_key_id]
        tro_state = json.loads(tro_path.read_text())
        remapped = _remap_tro(tro_state, template_bindings, first_key_bindings)
        frozen_tro_path = tro_output_dir / f"instance_{instance_id:04d}.json"
        prepared.append((instance_id, source_id, case_id, segment_bindings, frozen_tro_path, remapped))

    # Freeze remapped copies inside the session. The shared task sampler cache stays untouched.
    for _, _, _, _, tro_path, remapped in prepared:
        if tro_path.exists():
            raise FileExistsError(f"Refusing to replace a frozen session TRO: {tro_path}")
        tro_path.write_text(json.dumps(remapped, indent=4))

    manifest = {
        "session_id": session.session_id,
        "count": count,
        "seed": seed,
        "case_weights": session.task.case_weights,
        "cases": list(sorted(session.task.variants)),
        "status": "cases_frozen; scene_snapshots_and_start_poses_pending",
        "instances": [
            {
                "instance_id": instance_id,
                "source_task_instance_id": source_id,
                "case_id": case_id,
                "tro": str(tro_path.resolve()),
                "bindings": segment_bindings,
            }
            for instance_id, source_id, case_id, segment_bindings, tro_path, _ in prepared
        ],
    }
    session_root.mkdir(parents=True, exist_ok=True)
    assignment_path.write_text(json.dumps(manifest, indent=2))
    instances_path.write_text(
        json.dumps(
            {
                "session_id": session.session_id,
                "status": manifest["status"],
                "instances": [
                    {
                        "instance_id": item["instance_id"],
                        "case_id": item["case_id"],
                        "manifest": f"instances/{session.session_id}/instance_{item['instance_id']:04d}/instance.json",
                        "tro": item["tro"],
                    }
                    for item in manifest["instances"]
                ],
            },
            indent=2,
        )
    )
    print(f"Froze {count} case assignments in {assignment_path}")
    print("Query-start and Key-entry poses, plus full scene snapshots, are still required before collection.")


def finalize_instances(
    session: Session,
    pose_manifest_path: Path,
    scene_root: Path,
    inventory_path: Path,
    representative_calibration_path: Path | None = None,
    physical_calibration_path: Path | None = None,
) -> None:
    """Write collection-ready instance manifests from simulator snapshots and sampled poses."""
    from memory_bench.processing.sources import recording_metadata, task_assets
    from memory_bench.utils.state import file_hash, merge_scene_assets, read_json, scene_objects, write_json

    session_root = session.paths.resolve(f"sessions/{session.session_id}")
    assignments = read_json(session_root / "case_assignments.json")
    poses = read_json(pose_manifest_path)
    pose_instances = poses.get("instances", poses)
    inventory = read_json(inventory_path)
    count = session.config["instance_sampling"]["count"]
    if poses.get("query_sampling_independent_of_case") is not True:
        raise ValueError("Query starts must be sampled before case-specific Key TROs are applied")
    query_positions = [
        tuple(round(float(value), 3) for value in pose_instances[str(instance_id)]["query_start"]["position"])
        for instance_id in range(1, count + 1)
    ]
    if len(set(query_positions)) != count:
        raise ValueError("Frozen Query start positions must be unique across instances")
    if len(assignments["instances"]) != count:
        raise ValueError(f"Frozen assignments contain {len(assignments['instances'])} instances; expected {count}")

    representative = (
        read_json(representative_calibration_path)
        if representative_calibration_path is not None
        else poses.get("representative_calibration", {})
    )
    physical_calibration = (
        read_json(physical_calibration_path)
        if physical_calibration_path is not None
        else poses.get("physical_calibration", {})
    )
    boundary_ready = (
        representative.get("status") == "passed"
        and representative.get("live_predicates") is True
        and representative.get("navigation") is True
        and physical_calibration.get("status") == "passed"
    )
    instance_status = "ready" if boundary_ready else "pending_physical_calibration"

    intervent_objects = {}
    intervent_sources = {}
    for segment_id, segment in session.segments.items():
        if segment["role"] != "intervent_task":
            continue
        candidates = inventory["tasks"][segment_id]["candidates"]
        signatures = {}
        for candidate in candidates:
            signature = tuple(sorted(candidate["used"]))
            signatures.setdefault(signature, []).append(candidate)
        if not signatures:
            raise ValueError(f"No compatible source recordings for {segment_id}")
        signature, matching = max(signatures.items(), key=lambda item: len(item[1]))
        if len(matching) < count:
            raise ValueError(
                f"{segment_id} has only {len(matching)} source recordings with one stable asset set; {count} are required"
            )
        intervent_objects[segment_id] = list(signature)
        intervent_sources[segment_id] = matching[0]

    protected = sorted(
        {
            name
            for requirement in session.config["assets"]["requirements"].values()
            for name in requirement["candidates"]
        }
    )
    intervention_sets = {segment_id: set(names) for segment_id, names in intervent_objects.items()}
    for segment_id, names in intervention_sets.items():
        overlap = set(protected) & names
        if overlap:
            raise ValueError(
                f"Intervening assets for {segment_id} overlap protected Key/Query assets: {sorted(overlap)}"
            )
    intervention_ids = list(intervention_sets)
    for index, segment_id in enumerate(intervention_ids):
        for other_id in intervention_ids[index + 1 :]:
            overlap = intervention_sets[segment_id] & intervention_sets[other_id]
            if overlap:
                raise ValueError(f"Intervening assets overlap between {segment_id} and {other_id}: {sorted(overlap)}")

    source_scenes = {}
    for segment_id, source in intervent_sources.items():
        metadata = recording_metadata(source["hdf5"], source["demo"])
        names, _ = task_assets(
            metadata["scene"],
            metadata["interface"]["robot_name"],
            session.segments[segment_id].get("extra_objects", []),
        )
        if set(names) != intervention_sets[segment_id]:
            raise ValueError(f"Source inventory changed while preparing {segment_id}")
        source_scenes[segment_id] = metadata["scene"]

    task_instance_root = _task_instance_root(session)
    stable_path = (
        task_instance_root / "scenes" / session.task.scene_model / "json" / f"{session.task.scene_model}_stable.json"
    )
    stable_init = read_json(stable_path)["objects_info"]["init_info"]
    asset_manifest_path = session.paths.resolve(session.config["assets"]["manifest"])
    if asset_manifest_path.exists():
        raise FileExistsError(f"Asset manifest is already frozen: {asset_manifest_path}")

    index_entries = []
    asset_records = None
    frozen_poses = {}
    scene_sources = {}
    for entry in assignments["instances"]:
        instance_id = entry["instance_id"]
        key = str(instance_id)
        if key not in pose_instances:
            raise ValueError(f"Pose manifest lacks instance {instance_id}")
        pose = pose_instances[key]
        if pose.get("case_id", entry["case_id"]) != entry["case_id"]:
            raise ValueError(f"Pose manifest case ID differs from frozen assignment for instance {instance_id}")
        validation = pose.get("validation", {})
        if validation.get("first_key_live_boundary") is not True:
            raise ValueError(f"Instance {instance_id} lacks a successful live first-Key predicate check")
        if validation.get("empty_hands") is not True:
            raise ValueError(f"Instance {instance_id} lacks an empty-hands start-boundary check")
        if validation.get("stable_state", {}).get("stable") is not True:
            raise ValueError(f"Instance {instance_id} lacks a stable robot start-boundary check")
        if validation.get("query_start_sampled_before_case_tro") is not True:
            raise ValueError(f"Instance {instance_id} Query start is not proven case-independent")
        routes = validation.get("navigation_path_preflight", {})
        if not routes or any(route.get("reachable") is not True for route in routes.values()):
            raise ValueError(f"Instance {instance_id} lacks successful navigation preflight for every entry")
        missing_keys = set(session.key_ids) - set(pose.get("key_entries", {}))
        if missing_keys:
            raise ValueError(f"Instance {instance_id} lacks Key entry poses: {sorted(missing_keys)}")
        if "seed" not in pose:
            raise ValueError(f"Instance {instance_id} lacks a frozen random seed")
        if "query_start_seed" not in pose:
            raise ValueError(f"Instance {instance_id} lacks an independent Query start seed")
        frozen_poses[instance_id] = {
            "query_start": _pose_fields(pose["query_start"], f"Instance {instance_id} Query start"),
            "key_entries": {
                segment_id: _pose_fields(pose["key_entries"][segment_id], f"Instance {instance_id} {segment_id} entry")
                for segment_id in session.key_ids
            },
            "seed": pose["seed"],
            "query_start_seed": pose["query_start_seed"],
            "validation": validation,
        }
        scene_source = scene_root / f"instance_{instance_id:04d}" / "scene.json"
        if not scene_source.is_file():
            raise FileNotFoundError(f"Full scene snapshot is missing: {scene_source}")
        scene_sources[instance_id] = scene_source

    for requirement in session.config["assets"]["requirements"].values():
        for name in requirement["candidates"]:
            source = requirement["source"]
            is_scene_asset = name in stable_init
            if source == "scene" and not is_scene_asset:
                raise ValueError(f"Asset {name} is declared scene-sourced but is absent from the stable scene")
            if source == "insert" and is_scene_asset:
                raise ValueError(f"Asset {name} is declared inserted but already exists in the stable scene")

    for entry in assignments["instances"]:
        instance_id = entry["instance_id"]
        pose = frozen_poses[instance_id]
        scene = read_json(scene_sources[instance_id])
        for segment_id, source_scene in source_scenes.items():
            scene = merge_scene_assets(scene, source_scene, intervent_objects[segment_id])
        present_objects = scene_objects(scene)
        missing_assets = set(protected) - set(present_objects)
        if missing_assets:
            raise ValueError(f"Instance {instance_id} is missing protected assets: {sorted(missing_assets)}")

        instance_dir = session.paths.resolve(f"instances/{session.session_id}/instance_{instance_id:04d}")
        instance_dir.mkdir(parents=True, exist_ok=True)
        scene_path = instance_dir / "scene.json"
        instance_path = instance_dir / "instance.json"
        if scene_path.exists() or instance_path.exists():
            raise FileExistsError(f"Refusing to replace a frozen instance at {instance_dir}")
        write_json(scene_path, scene)
        scene_sha256 = file_hash(scene_path)
        tro_sha256 = file_hash(entry["tro"])
        manifest = {
            "status": instance_status,
            "instance_id": instance_id,
            "case_id": entry["case_id"],
            "bindings": entry["bindings"],
            "query_start": pose["query_start"],
            "key_entries": pose["key_entries"],
            "seed": pose["seed"],
            "query_start_seed": pose["query_start_seed"],
            "tro": entry["tro"],
            "scene": session.paths.relative(scene_path),
            "scene_sha256": scene_sha256,
            "tro_sha256": tro_sha256,
            "validation": pose["validation"],
        }
        write_json(instance_path, manifest)
        if asset_records is None:
            init_info = scene["objects_info"]["init_info"]
            asset_records = {
                name: {
                    "source": "scene" if name in stable_init else "insert",
                    "category": init_info[name]["args"]["category"],
                    "model": init_info[name]["args"]["model"],
                    "initializer": init_info[name],
                }
                for name in protected
            }
        index_entries.append(
            {
                "instance_id": instance_id,
                "status": instance_status,
                "case_id": entry["case_id"],
                "manifest": session.paths.relative(instance_path),
                "scene": session.paths.relative(scene_path),
                "tro": entry["tro"],
                "scene_sha256": scene_sha256,
                "tro_sha256": tro_sha256,
            }
        )

    write_json(
        asset_manifest_path,
        {
            "session_id": session.session_id,
            "status": "frozen",
            "assets": asset_records,
            "protected": protected,
            "intervent_objects": intervent_objects,
            "intervent_sources": intervent_sources,
            "source_inventory": str(inventory_path),
        },
    )
    instances_path = session_root / "instances.json"
    instances_path.write_text(
        json.dumps(
            {
                "session_id": session.session_id,
                "status": instance_status,
                "representative_calibration": representative,
                "physical_calibration": physical_calibration,
                "instances": index_entries,
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Session YAML path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Register sampler inputs and copy the stable scene")
    freeze_parser = commands.add_parser("freeze-cases", help="Assign cases to generated first-Key TROs")
    freeze_parser.add_argument("--seed", type=int)
    finalize_parser = commands.add_parser("finalize", help="Write collection-ready instance manifests")
    finalize_parser.add_argument("--pose-manifest", type=Path, required=True)
    finalize_parser.add_argument("--scene-root", type=Path, required=True)
    finalize_parser.add_argument("--inventory", type=Path, required=True)
    finalize_parser.add_argument(
        "--representative-calibration",
        type=Path,
        help="JSON evidence with status=passed, live_predicates=true, and navigation=true",
    )
    finalize_parser.add_argument(
        "--physical-calibration",
        type=Path,
        help="JSON evidence with status=passed after a physical boundary/navigation check",
    )
    args = parser.parse_args()

    session = Session.load(args.config)
    # initialize the session by registering sampler inputs and copying the stable scene
    if args.command == "init":
        init(session)
    # assign cases to generated first-Key TROs and freeze all segment bindings
    elif args.command == "freeze-cases":
        freeze_cases(session, args.seed)
    else:
        finalize_instances(
            session,
            args.pose_manifest.resolve(),
            args.scene_root.resolve(),
            args.inventory.resolve(),
            None if args.representative_calibration is None else args.representative_calibration.resolve(),
            None if args.physical_calibration is None else args.physical_calibration.resolve(),
        )


if __name__ == "__main__":
    main()
