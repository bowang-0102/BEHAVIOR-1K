"""Prepare collected K/Q and index the intervention allocation fixed before collection."""

import argparse
import hashlib
import json
import re

import h5py

from memory_bench.processing.make_segments import make_segment
from memory_bench.processing.normalize_recording import NormalizationContext, normalize_recording
from memory_bench.session import Session
from memory_bench.utils.instance_scene import load_instance_scene
from memory_bench.utils.recordings import recording_metadata
from memory_bench.utils.scene_state import (
    file_hash,
    get_state_tolerances,
    read_json,
    require_objects_match,
    require_robot_match,
    scene_objects,
    scene_systems,
    state_matches,
    write_json,
    write_json_atomic,
)
from memory_bench.utils.segment_manifest import (
    boundary,
    load_intervent_assignments,
    load_segments,
    validate_prepared_segment,
)


INDEX_FORMAT = "memory_bench.segment_index.v3"


def select_run(session, instance, run_id=None):
    """Select the latest completed numeric run; never combine sources across runs."""
    root = session.paths.resolve(f"recordings/{session.session_id}/instance_{instance['instance_id']:04d}")
    candidates = []
    for path in root.glob("run_*/manifest.json"):
        match = re.fullmatch(r"run_(\d+)", path.parent.name)
        if match is None or (run_id is not None and int(match[1]) != run_id):
            continue
        record = read_json(path)
        if record.get("status") == "complete":
            candidates.append((int(match[1]), path, record))
    if not candidates:
        raise ValueError(f"No complete collection run for instance {instance['instance_id']}")
    _, path, record = max(candidates, key=lambda value: value[0])
    tro_path = session.paths.resolve(f"sessions/{session.session_id}/tro/instance_{instance['instance_id']:04d}.json")
    tro, _ = load_instance_scene(session, tro_path)
    for field, expected in (
        ("session_id", session.session_id),
        ("instance_id", instance["instance_id"]),
        ("case_id", instance["case_id"]),
    ):
        if record[field] != expected or tro[field] != expected:
            raise ValueError(f"Collection run/TRO {field} differs from the frozen assignment: {path}")
    if record["format"] != "memory_bench.collection_run.v1" or record.get("invalid_reason"):
        raise ValueError(f"Invalid completed collection run: {path}")
    if record["run_id"] != path.parent.name:
        raise ValueError(f"Collection run_id differs from its directory: {path}")
    if session.paths.resolve(record["instance"]).resolve() != tro_path.resolve():
        raise ValueError(f"Collection run refers to a different TRO: {path}")
    if record["instance_sha256"] != file_hash(tro_path):
        raise ValueError(f"Collection run TRO hash changed: {path}")
    expected = [*session.key_ids, session.query_id]
    if record["segment_ids"] != expected or set(record["segments"]) != set(expected):
        raise ValueError(f"Collection run must contain all Keys then Query: {path}")
    return path, record, tro


def prepare_run(session, instance, run_path, run, tro, resume=False):
    """Normalize one accepted file per subtask, retaining the collector's role checks."""
    keys = [*session.key_ids, session.query_id]
    tolerance = get_state_tolerances()
    boundaries, metadata_by_key, sources = {}, {}, set()
    entry_target = {"robot": dict(tro["robot"])}
    intervents = load_segments(session, tro["intervents"])
    for key in keys:
        item = run["segments"][key]
        raw = session.paths.resolve(item["hdf5"]).resolve()
        if not raw.is_relative_to(run_path.parent.resolve()) or raw in sources:
            raise ValueError("Each K/Q needs a distinct raw file inside the same run")
        sources.add(raw)
        if item["demo"] != 0 or file_hash(raw) != item["sha256"]:
            raise ValueError(f"Collected source/demo changed after close: {key}")
        with h5py.File(raw, "r") as stream:
            data = stream["data"]
            for name, expected in (
                ("session_id", session.session_id), ("instance_id", instance["instance_id"]),
                ("case_id", instance["case_id"]), ("segment_id", key), ("run_id", run_path.parent.name),
            ):
                if data.attrs.get(name) != expected:
                    raise ValueError(f"Collected raw {name} does not match its run: {key}")
            if not data.attrs.get("collection_complete", False):
                raise ValueError(f"Collected source was not closed as complete: {key}")
        metadata = recording_metadata(raw, item["demo"])
        metadata_by_key[key] = metadata
        if item["frames"] != [0, metadata["frames"]]:
            raise ValueError(f"Collected source must use its entire local [0,N) action range: {key}")
        if metadata["activity"] != session.segments[key]["activity"]:
            raise ValueError(f"Collected source activity differs from its subtask: {key}")
        if metadata["config"]["scene"]["scene_model"] != session.scene_model:
            raise ValueError(f"Collected source scene differs from the session: {key}")
        states = item["states"]
        state_dir = session.paths.resolve(states["directory"]).resolve()
        if state_dir != run_path.parent.resolve():
            raise ValueError(f"Collected boundary snapshots must belong to their run: {key}")
        boundaries[key] = {}
        for name in ("entry", "exit"):
            boundary_path = (state_dir / states[name]).resolve()
            if boundary_path.parent != state_dir:
                raise ValueError(f"Collected boundary snapshot escapes its run: {key}")
            boundaries[key][name] = read_json(boundary_path)
        robot = metadata["interface"]["robot_name"]
        names = set(scene_objects(boundaries[key]["entry"]))
        require_objects_match(metadata["scene"], boundaries[key]["entry"], names, tolerance, "Raw scene entry")
        # The sampled base pose stays fixed; this run's first entry defines the common reset joints.
        if key == keys[0]:
            entry_target["robot"] = {**boundaries[key]["entry"]["robot"], **tro["robot"]}
        require_robot_match(boundaries[key]["entry"], entry_target, robot, tolerance, "Collection entry pose")
        for i_key, segment in intervents.items():
            initial = boundary(session, segment, "entry", robot)
            for snapshot in boundaries[key].values():
                require_objects_match(
                    snapshot, initial, segment["objects"]["used"], tolerance, f"Collection changed {i_key} layout"
                )
        if any(scene_systems(boundary) for boundary in boundaries[key].values()):
            raise ValueError("Collected boundary particle systems require a separate merge contract")
        required = ("entry", "exit", "empty_hands", "stable", "collision_free")
        if not all(item["validation"].get(name) is True for name in required):
            raise ValueError(f"Collected subtask has not passed physical boundary checks: {key}")
        for event in item["validation"].get("object_events", []):
            if not isinstance(event["frame"], int) or not 0 <= event["frame"] < metadata["frames"]:
                raise ValueError("Query object events must use local action-frame indices")
        if any(event.get("forbidden", False) for event in item["validation"].get("object_events", [])):
            raise ValueError("Query includes forbidden object events")
    expected_transfers = list(zip(keys, keys[1:]))
    transfers = run["transfers"]
    if [(item["from_segment"], item["to_segment"]) for item in transfers] != expected_transfers:
        raise ValueError("Each adjacent collected subtask needs exactly one ordered transfer")
    for transfer in transfers:
        if not all(transfer["validation"].get(name) is True for name in ("nonrobot_world_preserved", "entry_pose")):
            raise ValueError("Collection transfer has not passed world/entry-pose checks")
        previous, current = transfer["from_segment"], transfer["to_segment"]
        robot = metadata_by_key[current]["interface"]["robot_name"]
        exit_state, entry = boundaries[previous]["exit"], boundaries[current]["entry"]
        names = set(scene_objects(exit_state)) - {robot}
        if names != set(scene_objects(entry)) - {robot}:
            raise ValueError("Collection transfer changed the nonrobot object set")
        require_objects_match(entry, exit_state, names, tolerance, "Collection transfer")
        if not state_matches(scene_systems(entry), scene_systems(exit_state), tolerance):
            raise ValueError("Collection transfer changed particle systems")
    directory = (
        session.paths.output_root / "segments" / session.session_id
        / f"instance_{instance['instance_id']:04d}" / run_path.parent.name
    )
    identity = {"run_manifest": session.paths.relative(run_path), "sha256": file_hash(run_path)}
    identity_path = directory / "source.json"
    if directory.exists():
        if not resume:
            raise FileExistsError(f"Use --resume to validate existing preparation: {directory}")
        if not identity_path.exists() or read_json(identity_path) != identity:
            raise ValueError(f"Cannot resume preparation with a changed run: {directory}")
    else:
        write_json(identity_path, identity)
    results = {}
    protected = read_json(session.paths.resolve(session.assets["manifest"]))["protected"]
    with NormalizationContext(session) as context:
        for key in keys:
            item = run["segments"][key]
            output = directory / f"{key}.json"
            results[key] = session.paths.relative(output)
            if output.exists():
                manifest = validate_prepared_segment(session, key, output)
                frozen = manifest["source"]
                if (
                    session.paths.resolve(frozen["hdf5"]).resolve() != session.paths.resolve(item["hdf5"]).resolve()
                    or frozen["demo"] != item["demo"] or frozen["frames"] != item["frames"]
                    or manifest["identity"]["instance_id"] != instance["instance_id"]
                ):
                    raise ValueError(f"Prepared manifest refers to a different collected source: {output}")
                continue
            normalized = directory / f"{key}_normalized.hdf5"
            if normalized.exists():
                with h5py.File(normalized, "r") as stream:
                    data = stream["data"]
                    group = data["demo_0"]
                    if not data.attrs.get("normalization_complete", False):
                        raise ValueError(f"Incomplete normalization; preserve it and use a fresh output: {normalized}")
                    if (
                        len(group["action"]) != item["frames"][1]
                        or len(group["state_json"]) != len(group["action"]) + 1
                    ):
                        raise ValueError(f"Normalized accepted trajectory has incorrect frame counts: {normalized}")
            else:
                terminal = session.paths.resolve(item["states"]["directory"]) / item["states"]["exit"]
                normalize_recording(
                    session, session.paths.resolve(item["hdf5"]), normalized, 0, terminal, context=context
                )
            bindings = tro["bindings"][key]
            usage = session.task.object_usage[session.segments[key]["activity"]]
            objects = {name: sorted({bindings[obj] for obj in usage[name]}) for name in ("used", "read", "write")}
            objects["memory_bindings"] = {name: value for name, value in bindings.items() if name in usage["protected"]}
            source = {
                "hdf5": item["hdf5"], "demo": 0, "frames": item["frames"],
                "normalized_hdf5": session.paths.relative(normalized), "normalized_demo": 0,
            }
            make_segment(
                session, key, instance["instance_id"], source, item["states"], objects,
                item["validation"], output, instance["case_id"], protected=protected,
            )
    load_segments(session, results)
    return {
        "instance_id": instance["instance_id"], "case_id": instance["case_id"],
        "instance": run["instance"], "instance_sha256": run["instance_sha256"],
        "run_manifest": session.paths.relative(run_path), "run_manifest_sha256": identity["sha256"],
        "segments": results,
    }


def _config_hash(session):
    return hashlib.sha256(json.dumps(session.config, sort_keys=True).encode()).hexdigest()


def validate_segment_index(session, index, require_complete=True):
    """Check index coverage and all frozen files before resume or export."""
    if index["format"] != INDEX_FORMAT or index["session_id"] != session.session_id:
        raise ValueError("Segment index format/session does not match")
    if index["config_sha256"] != _config_hash(session) or index["instance_count"] != session.instance_count:
        raise ValueError("Segment index configuration or instance count changed")
    for path, expected in index["files"].items():
        if file_hash(session.paths.resolve(path)) != expected:
            raise ValueError(f"Segment index frozen input/output changed: {path}")
    assignments = read_json(session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json"))["instances"]
    expected_ids = {item["instance_id"] for item in assignments}
    assigned = {item["instance_id"]: item for item in assignments}
    actual_ids = [item["instance_id"] for item in index["instances"]]
    if len(actual_ids) != len(set(actual_ids)) or not set(actual_ids).issubset(expected_ids):
        raise ValueError("Segment index contains duplicated or unassigned instances")
    kq = set([*session.key_ids, session.query_id])
    for item in index["instances"]:
        if item["case_id"] != assigned[item["instance_id"]]["case_id"]:
            raise ValueError("Segment index case differs from its frozen assignment")
        for name in ("instance", "run_manifest"):
            if file_hash(session.paths.resolve(item[name])) != item[f"{name}_sha256"]:
                raise ValueError(f"Segment index {name} changed after freezing")
        if set(item["segments"]) != kq:
            raise ValueError("Segment index instance must contain every Key and Query")
        for key, path in item["segments"].items():
            validate_prepared_segment(session, key, session.paths.resolve(path))
    frozen_i = load_intervent_assignments(session)
    if index["intervents"] != frozen_i:
        raise ValueError("Segment index intervention allocation differs from the prepared instances")
    complete = set(actual_ids) == expected_ids
    if index["complete"] != complete or (require_complete and not complete):
        raise ValueError("Segment index is partial; process all configured instances and I sources before export")
    for item in index["instances"]:
        load_segments(session, {**item["segments"], **frozen_i[str(item["instance_id"])]})
    return index


def _publish_index(session, index, index_path):
    """Publish a checkpoint only after every referenced artifact has been frozen."""
    files = dict(index["files"])
    for item in index["instances"]:
        for name in ("instance", "run_manifest"):
            files[item[name]] = file_hash(session.paths.resolve(item[name]))
        tro = read_json(session.paths.resolve(item["instance"]))
        files[tro["scene_template"]] = file_hash(session.paths.resolve(tro["scene_template"]))
    references = [item["segments"] for item in index["instances"]]
    references.extend(index["intervents"].values())
    for mapping in references:
        for path in mapping.values():
            files[path] = file_hash(session.paths.resolve(path))
            manifest = read_json(session.paths.resolve(path))
            source, states = manifest["source"], manifest["states"]
            for name in ("hdf5", "normalized_hdf5"):
                files[source[name]] = file_hash(session.paths.resolve(source[name]))
            for name in ("entry", "exit"):
                boundary = session.paths.resolve(states["directory"]) / states[name]
                files[session.paths.relative(boundary)] = file_hash(boundary)
    # Never bless a changed artifact already frozen by an earlier checkpoint.
    for name, expected in index["files"].items():
        if files[name] != expected:
            raise ValueError(f"Frozen preparation artifact changed: {name}")
    index["files"] = files
    index["complete"] = len(index["instances"]) == session.instance_count
    validate_segment_index(session, index, require_complete=False)
    write_json_atomic(index_path, index)


def process_recordings(session, instance_id=None, run_id=None, resume=False):
    root = session.paths.resolve(f"sessions/{session.session_id}")
    assignments_path = root / "case_assignments.json"
    assignments = read_json(assignments_path)["instances"]
    if instance_id is not None and instance_id not in {item["instance_id"] for item in assignments}:
        raise ValueError("instance_id must refer to a frozen assignment")
    if run_id is not None and instance_id is None:
        raise ValueError("Explicit --run-id requires --instance-id")
    intervents_path = root / "intervent_segments.json"
    intervents = load_intervent_assignments(session)
    index_path = root / "segment_index.json"
    if index_path.exists():
        if not resume:
            raise FileExistsError(f"Use --resume to validate frozen preparation: {index_path}")
        index = validate_segment_index(session, read_json(index_path), require_complete=False)
    else:
        index = {
            "format": INDEX_FORMAT, "session_id": session.session_id, "config_sha256": _config_hash(session),
            "instance_count": session.instance_count, "complete": False, "instances": [], "intervents": intervents,
            "files": {
                session.paths.relative(assignments_path): file_hash(assignments_path),
                session.paths.relative(intervents_path): file_hash(intervents_path),
                session.assets["manifest"]: file_hash(session.paths.resolve(session.assets["manifest"])),
            },
        }
    selected = [item for item in assignments if instance_id is None or item["instance_id"] == instance_id]
    for instance in selected:
        run_path, run, tro = select_run(session, instance, run_id)
        previous = next((item for item in index["instances"] if item["instance_id"] == instance["instance_id"]), None)
        if previous is not None:
            if (
                previous["run_manifest"] != session.paths.relative(run_path)
                or previous["run_manifest_sha256"] != file_hash(run_path)
            ):
                raise ValueError("Selected run changed after freezing; use a separate output root")
            continue
        result = prepare_run(session, instance, run_path, run, tro, resume=resume)
        index["instances"].append(result)
        index["instances"].sort(key=lambda item: item["instance_id"])
        _publish_index(session, index, index_path)
    return index_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--instance-id", type=int)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    print(process_recordings(session, args.instance_id, args.run_id, args.resume))


if __name__ == "__main__":
    main()
