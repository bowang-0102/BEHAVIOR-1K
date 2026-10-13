"""Cut referenced sources into checked manifests without modifying raw recordings."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

import yaml

from memory_bench.processing.sources import FrameReader, inspect_source, recording_metadata
from memory_bench.session import Session
from memory_bench.utils.state import (
    file_hash,
    read_json,
    require_objects_match,
    scene_objects,
    state_matches,
    write_json,
)


def make_segment(session, segment_id, instance_id, source, states, objects, validation, output, case_id=None):
    definition = session.segments[segment_id]
    metadata = recording_metadata(session.paths.resolve(source["hdf5"]), source["demo"])
    if definition["role"] == "intervent_task":
        inspected = inspect_source(
            session.paths.resolve(source["hdf5"]),
            definition,
            session.config["scene_model"],
            definition.get("extra_objects", []),
            source["demo"],
        )
        if not set(inspected["used"]).issubset(objects["used"]):
            raise ValueError("I usage declaration omits task-bound assets")
    start, end = source["frames"]
    if not 0 <= start < end <= metadata["frames"]:
        raise ValueError(f"Invalid action range for {segment_id}")
    used, read, write = (set(objects[key]) for key in ("used", "read", "write"))
    if not read.issubset(used) or not write.issubset(used):
        raise ValueError("Read/write objects must belong to the task usage set")
    if not {"entry", "exit", "empty_hands", "stable"}.issubset(validation) or not all(
        validation[key] for key in ("entry", "exit", "empty_hands", "stable")
    ):
        raise ValueError(f"{segment_id} has not passed physical task/robot boundary checks")
    entry = read_json(session.paths.resolve(states["directory"]) / states["entry"])
    exit_state = read_json(session.paths.resolve(states["directory"]) / states["exit"])
    tolerance = session.config["validation"]["state_tolerances"]
    protected = set(read_json(session.paths.resolve(session.config["assets"]["manifest"]))["protected"])
    robot = metadata["interface"]["robot_name"]
    reference = source["normalized_hdf5"] if "normalized_hdf5" in source else source["hdf5"]
    frames = FrameReader(session.paths.resolve(reference), source.get("normalized_demo", source["demo"]))
    changed = set()
    try:
        require_objects_match(frames.state(start), entry, used | {robot}, tolerance, "Entry boundary")
        require_objects_match(frames.state(end), exit_state, used | {robot}, tolerance, "Post-action exit boundary")
        baseline = scene_objects(entry)
        checked = set(baseline) - {robot}
        for frame in range(start, end + 1):
            current = scene_objects(frames.state(frame))
            for name in checked:
                if name in baseline and name in current and not state_matches(current[name], baseline[name], tolerance):
                    changed.add(name)
        if changed - write:
            raise ValueError(f"Undeclared state changes in {segment_id}: {sorted(changed - write)}")
        if definition["role"] == "intervent_task" and (used | changed) & protected:
            raise ValueError(f"Intervening task overlaps protected assets: {sorted((used | changed) & protected)}")
    finally:
        frames.close()
    source = deepcopy(source)
    source["sha256"] = file_hash(session.paths.resolve(source["hdf5"]))
    source["fps"] = metadata["interface"]["fps"]
    if "normalized_hdf5" in source:
        source["normalized_sha256"] = file_hash(session.paths.resolve(source["normalized_hdf5"]))
    instruction = (
        metadata["activity"].replace("_", " ")
        if definition["role"] == "intervent_task"
        else session.task.instruction_for(definition["activity"], case_id)
    )
    manifest = {
        "identity": {"id": segment_id, "instance_id": instance_id},
        "task": {**definition, "instruction": instruction},
        "source": source,
        "states": states,
        "objects": objects,
        "interface": metadata["interface"],
        "validation": validation,
    }
    write_json(output, manifest)
    return manifest


def make_joint_segments(session, marks_path, normalized_path, instance_path):
    marks = yaml.safe_load(Path(marks_path).read_text())
    instance = read_json(instance_path)
    recording = marks["recording"]
    expected = [*session.key_ids, session.query_id]
    if set(marks["segments"]) != set(expected):
        raise ValueError("marks must contain every Key and the Query")
    # Audit discarded motion before producing accepted task manifests.
    reader = FrameReader(normalized_path)
    try:
        robot = recording_metadata(session.paths.resolve(recording["hdf5"]), recording["demo"])["interface"][
            "robot_name"
        ]
        for a, b in zip(expected, expected[1:]):
            start, end = marks["segments"][a]["frames"][1], marks["segments"][b]["frames"][0]
            baseline = reader.state(start)
            names = set(scene_objects(baseline)) - {robot}
            for frame in range(start + 1, end + 1):
                require_objects_match(
                    reader.state(frame),
                    baseline,
                    names,
                    session.config["validation"]["state_tolerances"],
                    "Discarded transfer",
                )
    finally:
        reader.close()
    results = {}
    previous_end = None
    for key in expected:
        mark = marks["segments"][key]
        start, end = mark["frames"]
        if previous_end is not None and start < previous_end:
            raise ValueError("Joint recording segments overlap or violate collection order")
        if previous_end is not None:
            previous = expected[expected.index(key) - 1]
            transfer = [
                item
                for item in marks["transfers"].values()
                if item["from_segment"] == previous and item["to_segment"] == key
            ]
            if len(transfer) != 1:
                raise ValueError("Each adjacent collection segment needs exactly one transfer")
            nav_start, nav_end = transfer[0]["navigation_frames"]
            if not previous_end <= nav_start <= nav_end <= start:
                raise ValueError("Navigation range is outside its transfer interval")
        bindings = instance["bindings"][key]
        usage = session.task.object_usage[session.segments[key]["activity"]]
        objects = {name: sorted({bindings[obj] for obj in usage[name]}) for name in ("used", "read", "write")}
        objects["memory_bindings"] = {name: value for name, value in bindings.items() if name in usage["protected"]}
        source = {
            "hdf5": recording["hdf5"],
            "demo": recording["demo"],
            "frames": mark["frames"],
            "normalized_hdf5": session.paths.relative(normalized_path),
            "normalized_demo": 0,
        }
        states = {"directory": recording["state_dir"], "entry": mark["entry_state"], "exit": mark["exit_state"]}
        output = (
            session.paths.output_root
            / "segments"
            / session.session_id
            / f"instance_{instance['instance_id']:04d}"
            / f"{key}.json"
        )
        results[key] = session.paths.relative(output)
        validation = dict(mark["validation"])
        validation["object_events"] = marks.get("object_events", []) if key == session.query_id else []
        if any(event.get("forbidden", False) for event in validation["object_events"]):
            raise ValueError("Query includes forbidden object events")
        make_segment(
            session, key, instance["instance_id"], source, states, objects, validation, output, instance["case_id"]
        )
        previous_end = end
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--marks")
    parser.add_argument("--normalized", required=True)
    parser.add_argument("--instance")
    parser.add_argument("--spec", help="I segment specification with source, states, objects and validation")
    args = parser.parse_args()
    session = Session.load(args.config)
    if args.spec:
        spec = read_json(session.paths.resolve(args.spec))
        output = session.paths.resolve(spec["output"])
        make_segment(
            session,
            spec["segment_id"],
            spec["instance_id"],
            {
                **spec["source"],
                "normalized_hdf5": session.paths.relative(session.paths.resolve(args.normalized)),
                "normalized_demo": 0,
            },
            spec["states"],
            spec["objects"],
            spec["validation"],
            output,
        )
    else:
        if not args.marks or not args.instance:
            parser.error("--marks and --instance are required for joint recordings")
        print(
            json.dumps(
                make_joint_segments(
                    session,
                    session.paths.resolve(args.marks),
                    session.paths.resolve(args.normalized),
                    session.paths.resolve(args.instance),
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
