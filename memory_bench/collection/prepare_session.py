import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from memory_bench.session import Session
from memory_bench.utils.scene_state import read_json, write_json_atomic


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
    session_root = session.paths.session_dir
    session_path = session_root / "session.json"
    if session_path.exists():
        raise FileExistsError(f"Session already prepared; use a new output_root: {session_path}")
    metadata_dir = session.paths.task_instances_dir / "metadata"
    custom_lists_path = metadata_dir / "task_custom_lists.json"
    custom_lists = json.loads(custom_lists_path.read_text()) if custom_lists_path.exists() else {}

    activities = [*session.task.key_activities, session.task.query_activity]
    for activity in activities:
        activity_spec = {
            "room_types": list(session.task.room_types),
            session.task.scene_model: {"whitelist": session.task.whitelist_for(activity), "blacklist": {}},
        }
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

    target_dir = session.paths.scene_json_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stable_scene = target_dir / stable_name
    shutil.copy2(official_scene, stable_scene)

    session_root.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "session_id": session.session_id,
        "config": session.config,
    }
    session_path.write_text(json.dumps(snapshot, indent=2))

    print(f"Sampler registration ready ({len(activities)} activities): {custom_lists_path}")
    print(f"Stable scene ready: {stable_scene}")
    print(f"Session configuration saved: {session_path}")


def _shared_task_bindings(session: Session, json_dir: Path) -> tuple[dict, dict, dict]:
    """Share uniquely modeled inserted objects across activities, retaining their sampled names."""
    stable = json.loads((json_dir / f"{session.task.scene_model}_stable.json").read_text())
    stable_objects = stable["objects_info"]["init_info"]
    bindings, sources, objects_by_model = {}, {}, {}
    initializers = {}
    for segment_id in [*session.key_ids, session.query_id]:
        activity = session.segments[segment_id]["activity"]
        path = json_dir / f"{session.task.scene_model}_task_{activity}_0_0_template.json"
        template = json.loads(path.read_text())
        base = template["metadata"]["task"]["inst_to_name"]
        remap, seen_models = {}, set()
        for instance, name in base.items():
            if instance.startswith("agent.n.") or name in stable_objects:
                continue
            initializer = template["objects_info"]["init_info"].get(name)
            if initializer is None:  # Particle systems have no object initializer.
                continue
            args = initializer["args"]
            identity = (args["category"], args["model"])
            if identity in seen_models:
                raise ValueError(f"Ambiguous inserted model {identity} in {segment_id}; shared objects must be distinct")
            seen_models.add(identity)
            if identity not in objects_by_model:
                if name in sources:
                    raise ValueError(f"Different inserted models use the same sampled name: {name}")
                objects_by_model[identity] = name
                sources[name] = segment_id
                initializers[name] = args
            shared_name = objects_by_model[identity]
            for field in ("scale", "fixed_base"):
                if args.get(field) != initializers[shared_name].get(field):
                    raise ValueError(f"Conflicting shared object {identity} in {segment_id}: {field}")
            remap[name] = shared_name
        bindings[segment_id] = {instance: remap.get(name, name) for instance, name in base.items()}
        if len(set(bindings[segment_id].values())) != len(base):
            raise ValueError(f"Resolved task bindings contain duplicate object names in {segment_id}")

    variant_objects = {}
    for logical_name, identity in session.task.variant_object_models.items():
        if identity not in objects_by_model:
            raise ValueError(f"Missing sampled model {identity} for variant object {logical_name}")
        variant_objects[logical_name] = objects_by_model[identity]
    return bindings, variant_objects, sources


def sample_tasks(session: Session) -> None:
    """Run the original samplers with settings from the session configuration."""
    session_root = session.paths.session_dir
    if (session_root / "variant_assignments.json").exists():
        raise FileExistsError(f"Task variants already assigned; use a new output_root: {session_root}")
    repo_root = Path(__file__).resolve().parents[2]
    scripts = repo_root / "OmniGibson/scripts/sampling"
    json_dir = session.paths.scene_json_dir
    for key in [*session.key_ids, session.query_id]:
        activity = session.segments[key]["activity"]
        subprocess.run(
            [sys.executable, str(scripts / "sample_b1k_tasks.py"),
             "--activity", activity, "--output_dir", str(json_dir), "--overwrite"],
            cwd=repo_root, check=True,
        )
        if key == session.key_ids[0]:
            command = [
                sys.executable, str(scripts / "multiply_b1k_tasks.py"),
                "--activity", activity, "--start_idx", "1", "--end_idx", str(session.instance_sampling["count"]),
                "--seed", str(session.instance_sampling["seed"]),
                "--partial_save", "--headless", "--output_dir", str(json_dir),
            ]
            subprocess.run(command, cwd=repo_root, check=True)
            (json_dir / f"{session.scene_model}_task_{activity}_instances").mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [sys.executable, str(scripts / "sample_robot_pose.py"),
                 "--activity", activity, "--output_dir", str(json_dir)],
                cwd=repo_root, check=True,
            )


def assign_variants(session: Session) -> None:
    """Freeze variant assignments and source references; sampling writes the final TROs."""
    _validate_session_task(session)
    session_root = session.paths.session_dir
    assignment_path = session_root / "variant_assignments.json"
    if assignment_path.exists():
        raise FileExistsError(f"Variant assignments already exist; use a new output_root: {assignment_path}")
    seed = session.variant_sampling.get("seed", session.instance_sampling["seed"])
    json_dir = session.paths.scene_json_dir
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
    count = len(tro_files)
    if not count:
        raise ValueError(f"No sampled {first_key_activity} TRO files found in {instance_dir}")

    assignments = session.task.sample_variants(count, seed)
    # Resolve shared object identities once, then apply each instance's variant.
    shared_bindings_by_segment, variant_object_names, object_source_segments = _shared_task_bindings(session, json_dir)
    prepared = []
    for instance_id, (source_id, source_tro) in enumerate(sorted(tro_files.items()), start=1):
        variant_id = assignments[instance_id]
        bindings = {}
        for segment_id, base_bindings in shared_bindings_by_segment.items():
            bindings[segment_id] = session.task.bind_variant(
                base_bindings, variant_id, session.segments[segment_id]["activity"], variant_object_names,
            )
        prepared.append(
            {
                "instance_id": instance_id,
                "source_task_instance_id": source_id,
                "variant_id": variant_id,
                "source_tro": str(source_tro.relative_to(session.paths.output_root)),
                "bindings": bindings,
            }
        )
    inputs = [json_dir / f"{session.task.scene_model}_stable.json"]
    inputs.extend(
        json_dir / f"{session.task.scene_model}_task_{activity}_0_0_template.json"
        for activity in [*session.task.key_activities, session.task.query_activity]
    )
    inputs.extend(tro_files.values())
    manifest = {
        "session_id": session.session_id,
        "count": count,
        "seed": seed,
        "variant_weights": session.task.variant_weights,
        "variants": list(sorted(session.task.variants)),
        "asset_sources": object_source_segments,
        "inputs": sorted(str(path.relative_to(session.paths.output_root)) for path in inputs),
        "status": "variants_assigned",
        "instances": prepared,
    }
    # Requirements refer to Query roles; resolve them only after variant-specific permutations.
    manifest["asset_requirements"] = {
        label: sorted(
            {
                entry["bindings"][session.query_id][instance]
                for entry in manifest["instances"]
                for instance in requirement["instances"]
            }
        )
        for label, requirement in session.assets["requirements"].items()
    }
    write_json_atomic(assignment_path, manifest)
    print(f"Task variant assignments ready ({count} instances): {assignment_path}")


def assemble_scene_assets(session: Session, assignments: dict, scene: dict, intervents: dict) -> tuple[dict, dict]:
    """Build shared object definitions from the assigned recordings' initial states."""
    from memory_bench.utils.scene_state import merge_scene_assets, scene_objects

    intervent_objects = {}
    for segment_id, segment in intervents.items():
        intervent_objects[segment_id] = segment["objects"]["used"]

    asset_requirements = assignments["asset_requirements"]
    protected = sorted({name for names in asset_requirements.values() for name in names})
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

    for segment_id, segment in intervents.items():
        scene = merge_scene_assets(scene, segment["entry"], intervent_objects[segment_id])
    missing = set(protected) - set(scene_objects(scene))
    if missing:
        raise ValueError(f"Shared scene is missing protected assets: {sorted(missing)}")
    stable_path = session.paths.scene_json_dir / f"{session.scene_model}_stable.json"
    stable_init = read_json(stable_path)["objects_info"]["init_info"]
    initializers = scene["objects_info"]["init_info"]
    asset_records = {}
    for label, requirement in session.assets["requirements"].items():
        for name in asset_requirements[label]:
            is_scene_asset = name in stable_init
            if requirement["source"] == "scene" and not is_scene_asset:
                raise ValueError(f"Asset {name} is declared scene-sourced but is absent from the stable scene")
            if requirement["source"] == "insert" and is_scene_asset:
                raise ValueError(f"Asset {name} is declared inserted but already exists in the stable scene")
            asset_records[name] = {
                "source": "scene" if is_scene_asset else "insert",
                "category": initializers[name]["args"]["category"],
                "model": initializers[name]["args"]["model"],
                "initializer": initializers[name],
            }
    assets = {
        "session_id": session.session_id,
        "status": "frozen",
        "assets": asset_records,
        "requirements": asset_requirements,
        "protected": protected,
        "intervent_objects": intervent_objects,
    }
    return scene, assets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Session YAML path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Register sampler inputs and copy the stable scene")
    sample_parser = commands.add_parser("sample", help="Run task samplers using the configured instance count")
    sample_parser.add_argument("--instance-count", type=int, help="Override instance_sampling.count")
    sample_parser.add_argument("--seed", type=int, help="Override instance_sampling.seed for task sampling")
    commands.add_parser("assign-variants", help="Freeze variants and references to original TROs")
    args = parser.parse_args()
    session = Session.load(args.config)
    if args.command == "init":
        init(session)
    elif args.command == "sample":
        if args.instance_count is not None:
            if args.instance_count < 1:
                parser.error("--instance-count must be positive")
            session.instance_sampling["count"] = args.instance_count
        if args.seed is not None:
            session.instance_sampling["seed"] = args.seed
        os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)
        os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] = str(session.paths.task_instances_dir)
        sample_tasks(session)
    elif args.command == "assign-variants":
        assign_variants(session)


if __name__ == "__main__":
    main()
