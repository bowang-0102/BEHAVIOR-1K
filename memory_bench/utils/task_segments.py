from copy import deepcopy

from memory_bench.utils.recordings import inspect_source, recording_metadata
from memory_bench.utils.scene_state import get_state_tolerances, scene_objects, scene_systems, state_matches


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
        "validation": {},
    }


def describe_collected(session, key, instance, item):
    definition = session.segments[key]
    metadata = recording_metadata(session.paths.resolve(item["hdf5"]), item["demo"])
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


def check_frame(segment, state, baseline, protected):
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
    changed = {
        name for name, value in original.items()
        if name != robot and (
            not state_matches(current[name], value, tolerance)
            or not state_matches(value, current[name], tolerance)
        )
    }
    role = segment["task"]["role"]
    guarded = set(protected)
    if role not in ("intervent_task", "bridge_task"):
        guarded -= set(segment["objects"]["write"])
    if changed & guarded:
        raise ValueError(f"{segment['identity']['id']} altered protected memory: {sorted(changed & guarded)}")
    if role == "bridge_task" and changed:
        raise ValueError(f"Undeclared state changes in {segment['identity']['id']}: {sorted(changed)}")
    return changed


def require_write_scope(segment, changed, target_objects):
    """Reject writes that cannot be represented by the prepared scene's initializers."""
    missing = (set(segment["objects"]["write"]) | changed) - target_objects
    if missing:
        raise ValueError(f"{segment['identity']['id']} writes objects outside the prepared scene: {sorted(missing)}")
