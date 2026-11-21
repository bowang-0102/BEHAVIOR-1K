import json
from copy import deepcopy

from memory_bench.utils.recording.reader import compatible_interfaces, inspect_source, read_recording_metadata
from memory_bench.utils.scene.state import (
    get_state_tolerances,
    read_json,
    remap_robot_name,
    scene_objects,
    scene_systems,
    state_matches,
)


def describe_intervent(session, key, source, instance_id):
    definition = session.segments[key]
    info = inspect_source(
        session.paths.resolve(source["hdf5"]), definition, session.scene_model,
        definition.get("extra_objects", []), source["demo"],
    )
    return {
        "identity": {"id": key, "instance_id": instance_id},
        "task": {**definition, "instruction": definition["activity"].replace("_", " ")},
        "source": {**{name: source[name] for name in ("hdf5", "demo")},
                   "frames": info["frames"], "fps": info["interface"]["fps"]},
        "objects": {"used": info["used"], "read": info["used"], "write": info["used"], "memory_bindings": {}},
        "interface": info["interface"],
        "bddl_goal": (definition["activity"], info["activity_definition_id"], info["bindings"]),
        "validation": {},
    }


def describe_collected(session, key, instance, item):
    definition = session.segments[key]
    metadata = read_recording_metadata(session.paths.resolve(item["hdf5"]), item["demo"])
    if metadata["activity"] != definition["activity"] or metadata["config"]["scene"]["scene_model"] != session.scene_model:
        raise ValueError(f"Collected activity or scene differs from the task: {key}")
    bindings = instance["bindings"][key]
    usage = session.task.object_usage[definition["activity"]]
    objects = {name: sorted({bindings[obj] for obj in usage[name]}) for name in ("used", "read", "write")}
    objects["memory_bindings"] = {name: bindings[name] for name in usage["protected"]}
    return {
        "identity": {"id": key, "instance_id": instance["instance_id"]},
        "task": {
            **definition,
            "instruction": session.task.instruction_for(definition["activity"], instance["variant_id"]),
        },
        "source": {name: deepcopy(item[name]) for name in ("hdf5", "demo", "frames")},
        "states": {name: item["states"][name] for name in ("directory", "entry", "exit")},
        "objects": objects,
        "interface": metadata["interface"],
        "validation": deepcopy(item["validation"]),
    }


def bridge_frame_tolerances():
    """Ignore transient joint speeds when comparing objects inside a Bridge."""
    tolerance = get_state_tolerances()
    tolerance["ignored_fields"].append("joint_vel")
    return tolerance


def changed_objects(current, original, robot, tolerance, *, object_checks=None):
    """Find changed objects in a full snapshot or a sparse recording update."""
    changed = set()
    for name, value in current.items():
        if name == robot:
            continue
        if object_checks is not None:
            # FrameReader returns JSON values. Exact encoding also distinguishes booleans from numbers.
            encoded = json.dumps(value)
            previous = object_checks.get(name)
            if previous is not None and previous[0] == encoded:
                if previous[1]:
                    changed.add(name)
                continue
        differs = not state_matches(value, original[name], tolerance) or not state_matches(
            original[name], value, tolerance
        )
        if object_checks is not None:
            object_checks[name] = (encoded, differs)
        if differs:
            changed.add(name)
    return changed


def check_frame(segment, state, baseline, protected, *, is_intermediate=False, object_checks=None):
    """Return observed writes while protecting memory and the Bridge's nonrobot world."""
    robot = segment["interface"]["robot_name"]
    current, original = scene_objects(state), scene_objects(baseline)
    tolerance = get_state_tolerances()
    systems, original_systems = scene_systems(state), scene_systems(baseline)
    if (
        set(current) != set(original)
        or not state_matches(systems, original_systems, tolerance)
        or not state_matches(original_systems, systems, tolerance)
    ):
        raise ValueError("Recording changed object topology or particle systems")
    role = segment["task"]["role"]
    object_tolerance = bridge_frame_tolerances() if role == "bridge_task" and is_intermediate else tolerance
    changed = changed_objects(current, original, robot, object_tolerance, object_checks=object_checks)
    writes = set(segment["objects"]["write"])
    protected_changes = changed.intersection(protected)
    if role not in ("intervent_task", "bridge_task"):
        protected_changes -= writes
    if protected_changes:
        raise ValueError(f"{segment['identity']['id']} altered protected memory: {sorted(protected_changes)}")
    if role == "bridge_task":
        undeclared = changed - writes
        if undeclared:
            raise ValueError(f"Undeclared state changes in {segment['identity']['id']}: {sorted(undeclared)}")
    return changed


def require_write_scope(segment, changed, target_objects, *, include_declared=True):
    """Reject writes that cannot be represented by the prepared scene's initializers."""
    writes = changed.union(segment["objects"]["write"]) if include_declared else changed
    missing = writes - target_objects
    if missing:
        raise ValueError(f"{segment['identity']['id']} writes objects outside the prepared scene: {sorted(missing)}")


def load_segment_boundary(session, segment, name, robot_name):
    from memory_bench.utils.recording.reader import FrameReader

    if name == "entry" and segment["source"]["frames"][0] > 0:
        source = segment["source"]
        terminal = None
        if "states" in segment:
            states = segment["states"]
            terminal = session.paths.resolve(states["directory"]) / states["exit"]
        with FrameReader(session.paths.resolve(source["hdf5"]), source["demo"], terminal=terminal) as reader:
            snapshot = reader.snapshot(source["frames"][0])
    elif "states" in segment:
        states = segment["states"]
        snapshot = read_json(session.paths.resolve(states["directory"]) / states[name])
    else:
        source = segment["source"]
        with FrameReader(session.paths.resolve(source["hdf5"]), source["demo"]) as reader:
            snapshot = reader.snapshot(source["frames"][0 if name == "entry" else 1])
    return remap_robot_name(snapshot, segment["interface"]["robot_name"], robot_name)


def validate_segments(segments):
    """Check task identities and interfaces of inline recipe segments."""
    interface = next(iter(segments.values()))["interface"]
    for key, segment in segments.items():
        if segment["identity"]["id"] != key:
            raise ValueError(f"Manifest identity mismatch for {key}")
        if not compatible_interfaces(interface, segment["interface"]):
            raise ValueError(f"Robot/controller/action/frequency mismatch for {key}")
    return segments


def load_collection_manifest(session, instance, path, *, skip_incomplete=False):
    """Load one complete K/Q collection manifest and check its source references."""
    directory = path.parent
    record = read_json(path)
    if skip_incomplete and record["status"] != "complete":
        return None
    if record["status"] != "complete" or record.get("invalid_reason"):
        raise ValueError(f"Collection demo is incomplete or invalid: {directory}")
    for name, expected in (("session_id", session.session_id), ("instance_id", instance["instance_id"]),
                           ("variant_id", instance["variant_id"]), ("run_id", directory.name)):
        if record[name] != expected:
            raise ValueError(f"Collection demo {name} differs from its instance: {path}")
    tro_path = session.paths.session_dir / "tro" / f"instance_{instance['instance_id']:04d}.json"
    if session.paths.resolve(record["instance"]).resolve() != tro_path.resolve():
        raise ValueError(f"Collection demo uses a different TRO: {path}")
    keys = [*session.key_ids, session.query_id]
    if record["segment_ids"] != keys or set(record["segments"]) != set(keys):
        raise ValueError("Collection demo must contain every Key and Query in order")
    for item in record["segments"].values():
        source = session.paths.resolve(item["hdf5"]).resolve()
        if source.parent != directory.resolve() or item["demo"] != 0:
            raise ValueError("K/Q must use files in the selected collection demo")
        if session.paths.resolve(item["states"]["directory"]).resolve() != directory.resolve():
            raise ValueError("Collected boundaries must belong to the selected demo")
    return record
