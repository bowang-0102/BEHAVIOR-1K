"""Scan compatible intervention recordings.

Run this module with a session config to write reports/<session_id>/source_inventory.json.
"""

import argparse
import json

import h5py

from memory_bench.session import Session
from memory_bench.utils.recordings import inspect_source
from memory_bench.utils.scene_state import read_json, write_json


def scan_sources(session):
    inventory_path = session.paths.output_root / "reports" / session.session_id / "source_inventory.json"
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
                    inspect_source(
                        path, segment, session.config["scene_model"], segment.get("extra_objects", []), max(demos)
                    )
                )
            except (ValueError, KeyError, OSError) as error:
                rejected.append({"hdf5": str(path), "reason": str(error)})
        expected = session.config["intervent_selection"]["candidate_count_per_task"]
        report["tasks"][key] = {
            "available": len(candidates) + len(rejected),
            "compatible_metadata": len(candidates),
            "expected_candidates": expected,
            "candidate_shortfall": max(0, expected - len(candidates) - len(rejected)),
            "candidates": candidates,
            "rejected": rejected,
        }
    write_json(inventory_path, report)
    print(f"Source recording inventory saved: {inventory_path}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    args = parser.parse_args()
    report = scan_sources(Session.load(args.config))
    print(
        json.dumps(
            {
                key: {field: task[field] for field in ("available", "compatible_metadata", "candidate_shortfall")}
                for key, task in report["tasks"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
