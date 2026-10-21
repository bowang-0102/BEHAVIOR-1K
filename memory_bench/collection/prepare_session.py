"""Register sampler inputs and freeze Memory Bench instance bindings."""

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import re
import shutil
from pathlib import Path

from memory_bench.session import Session
from memory_bench.utils.paths import _scene_json_dir, _task_instance_root
from memory_bench.utils.scene_state import read_json, write_json_atomic


def _write_prepared_json(path: Path, value: dict) -> None:
    """Reuse matching preparation output without replacing frozen data."""
    if path.exists():
        if read_json(path) != value:
            raise ValueError(
                f"Prepared output does not match the current inputs: {path}. "
                "Use a separate output_root to prepare a new session."
            )
    else:
        write_json_atomic(path, value)


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
            raise ValueError(f"Sampler registration for {activity} does not match the current task: {custom_lists_path}")
        custom_lists[activity] = activity_spec

    metadata_dir.mkdir(parents=True, exist_ok=True)
    encoded_lists = json.dumps(custom_lists, indent=4)
    if not custom_lists_path.exists() or custom_lists_path.read_text() != encoded_lists:
        custom_lists_path.write_text(encoded_lists)

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
            raise ValueError(f"Cached stable scene does not match the source scene: {stable_scene}")
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
            raise ValueError(
                f"Session configuration does not match the saved snapshot: {session_path}. "
                "Use a separate output_root to prepare a new session."
            )
    else:
        session_path.write_text(encoded)

    print(f"Sampler registration ready ({len(_task_activities(session))} activities): {custom_lists_path}")
    print(f"Stable scene ready: {stable_scene}")
    print(f"Session configuration verified: {session_path}")


def _template_bindings(session: Session, json_dir: Path, activity: str) -> dict[str, str]:
    template_path = json_dir / f"{session.task.scene_model}_task_{activity}_0_0_template.json"
    template = json.loads(template_path.read_text())
    return template["metadata"]["task"]["inst_to_name"]


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


def _bindings_for_case(session: Session, base_bindings: dict, variant_objects: dict, case_id: str) -> dict:
    task = session.task
    bindings = {}
    for segment_id, segment in session.segments.items():
        if segment["role"] not in {"key_task", "query_task"}:
            continue
        activity = segment["activity"]
        bindings[segment_id] = task.bind_variant(base_bindings[segment_id], case_id, activity, variant_objects)
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


def assign_variants(session: Session, seed: int | None = None) -> None:
    """Freeze variant assignments and source references; sampling writes the final TROs."""
    _validate_session_task(session)
    seed = session.case_sampling.get("seed", session.instance_sampling["seed"]) if seed is None else seed
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
    count = len(tro_files)
    if not count:
        raise ValueError(f"No sampled {first_key_activity} TRO files found in {instance_dir}")

    session_root = session.paths.resolve(f"sessions/{session.session_id}")
    assignment_path = session_root / "case_assignments.json"
    assignments = session.task.sample_cases(count, seed)
    # Resolve shared object identities once, then apply each instance's variant.
    shared_bindings_by_segment, variant_object_names, object_source_segments = _shared_task_bindings(session, json_dir)
    prepared = []
    for instance_id, (source_id, source_tro) in enumerate(sorted(tro_files.items()), start=1):
        case_id = assignments[instance_id]
        bindings = _bindings_for_case(session, shared_bindings_by_segment, variant_object_names, case_id)
        prepared.append(
            {
                "instance_id": instance_id,
                "source_task_instance_id": source_id,
                "case_id": case_id,
                "source_tro": session.paths.relative(source_tro),
                "bindings": bindings,
            }
        )
    inputs = [json_dir / f"{session.task.scene_model}_stable.json"]
    inputs.extend(
        json_dir / f"{session.task.scene_model}_task_{activity}_0_0_template.json"
        for activity in _task_activities(session)
    )
    inputs.extend(tro_files.values())
    manifest = {
        "session_id": session.session_id,
        "count": count,
        "seed": seed,
        "case_weights": session.task.case_weights,
        "cases": list(sorted(session.task.variants)),
        "config": session.config,
        "task_definition": json.loads(json.dumps(asdict(session.task))),
        "asset_sources": object_source_segments,
        "inputs": sorted(session.paths.relative(path) for path in inputs),
        "status": "variants_assigned",
        "instances": prepared,
    }
    # Requirements refer to Query roles; resolve them only after case-specific permutations.
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
    if assignment_path.exists():
        existing = read_json(assignment_path)
        existing["inputs"] = sorted(existing["inputs"])
        if existing != manifest:
            raise ValueError(f"Prepared output does not match the current inputs: {assignment_path}")
    else:
        write_json_atomic(assignment_path, manifest)
    print(f"Task variant assignments ready ({count} instances): {assignment_path}")


def validate_assignments(session: Session, assignments: dict) -> None:
    """Check session and task definitions before reusing prepared assignments."""
    if assignments["session_id"] != session.session_id or assignments["config"] != session.config:
        raise ValueError("Variant assignments do not match the current session configuration")
    if assignments["task_definition"] != json.loads(json.dumps(asdict(session.task))):
        raise ValueError("Task definition changed after variant assignment")


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
    stable_path = _scene_json_dir(session, _task_instance_root(session)) / f"{session.task.scene_model}_stable.json"
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
    variants_parser = commands.add_parser("assign-variants", help="Freeze variants and references to original TROs")
    variants_parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    session = Session.load(args.config)
    if args.command == "init":
        init(session)
    elif args.command == "assign-variants":
        assign_variants(session, args.seed)


if __name__ == "__main__":
    main()
