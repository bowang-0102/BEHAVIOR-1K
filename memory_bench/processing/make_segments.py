"""Build checked segment manifests without modifying raw recordings."""

import argparse
from copy import deepcopy

from memory_bench.session import Session
from memory_bench.utils.recordings import FrameReader, inspect_source, recording_metadata
from memory_bench.utils.scene_state import (
    file_hash,
    get_state_tolerances,
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
            session.scene_model,
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
    tolerance = get_state_tolerances()
    protected = set(read_json(session.paths.resolve(session.assets["manifest"]))["protected"])
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
                if (
                    name in current
                    and current[name] != baseline[name]
                    and not state_matches(current[name], baseline[name], tolerance)
                ):
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
        "states": {
            **states,
            "sha256": {
                name: file_hash(session.paths.resolve(states["directory"]) / states[name])
                for name in ("entry", "exit")
            },
        },
        "objects": objects,
        "interface": metadata["interface"],
        "validation": validation,
    }
    write_json(output, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--normalized", required=True)
    parser.add_argument("--spec", required=True, help="Segment specification with source, states, objects and validation")
    args = parser.parse_args()
    session = Session.load(args.config)
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


if __name__ == "__main__":
    main()
