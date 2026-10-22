"""Check metadata and assets, then fix raw intervention sources before instance sampling."""

import argparse
import json

import h5py

from memory_bench.session import Session
from memory_bench.utils.recordings import inspect_source, recording_metadata
from memory_bench.utils.scene_state import read_json, merge_scene_assets, write_json_atomic
from memory_bench.utils.segment_manifest import compatible_interfaces


def scan_sources(session):
    """Inventory I recordings by activity, scene, room and basic recording schema."""
    inventory_path = session.paths.reports_dir / "source_inventory.json"
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
        report["tasks"][key] = {
            "available": len(candidates) + len(rejected),
            "compatible_metadata": len(candidates),
            "candidates": candidates,
            "rejected": rejected,
        }
    write_json_atomic(inventory_path, report)
    print(f"Source recording inventory saved: {inventory_path}")
    return report


def prepare_intervents(session, inventory):
    root = session.paths.session_dir
    output_path = root / "intervent_assignments.json"
    if output_path.exists():
        raise FileExistsError(f"Intervention assignments already exist; use a new output_root: {output_path}")
    if inventory["session_id"] != session.session_id:
        raise ValueError("Source inventory belongs to another session")
    expected = {key for key, definition in session.segments.items() if definition["role"] == "intervent_task"}
    if set(inventory["tasks"]) != expected:
        raise ValueError("Source inventory must contain every configured I exactly once")
    assignments_path = root / "variant_assignments.json"
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
        first_scene = None
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
                scene = recording_metadata(source, candidate["demo"])["scene"]
                if interface is not None and not compatible_interfaces(interface, candidate["interface"]):
                    raise ValueError("Intervention sources have incompatible robot interfaces")
                if first_scene is not None:
                    merge_scene_assets(first_scene, scene, names)
                else:
                    first_scene = scene
                interface = candidate["interface"]
                results[key].append({"hdf5": str(source), "demo": candidate["demo"]})
            except (ValueError, KeyError) as error:
                rejected.append({"segment_id": key, "hdf5": str(source), "reason": str(error)})
                print(f"Rejected {key}: {source.name}: {error}", flush=True)
        if len(results[key]) != count:
            write_json_atomic(
                session.paths.reports_dir / f"{key}_selection_failure.json",
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
    inventory = scan_sources(session)
    print(json.dumps(prepare_intervents(session, inventory), indent=2))


if __name__ == "__main__":
    main()
