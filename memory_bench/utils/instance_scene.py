"""Restore a prepared instance from one shared scene template and its TRO."""

from copy import deepcopy

from memory_bench.utils.scene_state import file_hash, read_json, scene_objects


def build_instance_tro(template, snapshot, record):
    """Keep task objects and every other changed object, without duplicating initializers."""
    if snapshot["objects_info"] != template["objects_info"]:
        raise ValueError("Instance preparation changed the shared scene object initializers")
    original_objects = scene_objects(template)
    current_objects = scene_objects(snapshot)
    if set(original_objects) != set(current_objects):
        raise ValueError("Instance preparation changed the shared scene object set")
    task_objects = {name for bindings in record["bindings"].values() for name in bindings.values()}
    state = deepcopy(snapshot["state"])
    state["registry"]["object_registry"] = {
        name: value
        for name, value in current_objects.items()
        if name in task_objects or name == snapshot["robot"]["name"] or value != original_objects[name]
    }
    original_metadata = template["metadata"]["task"]
    current_metadata = snapshot["metadata"]["task"]
    return {
        **record,
        "state": state,
        "task_metadata": {
            key: value
            for key, value in current_metadata.items()
            if key not in original_metadata or original_metadata[key] != value
        },
        "removed_task_metadata": sorted(set(original_metadata) - set(current_metadata)),
        "robot": snapshot["robot"],
    }


def restore_instance_scene(template, tro):
    """Reconstruct the full checked state in memory; never write a per-instance scene."""
    scene = deepcopy(template)
    objects = deepcopy(scene_objects(template))
    incoming = scene_objects(tro)
    if set(incoming) - set(objects):
        raise ValueError("Instance TRO contains objects absent from its shared scene template")
    objects.update(deepcopy(incoming))
    scene["state"] = deepcopy(tro["state"])
    scene["state"]["registry"]["object_registry"] = objects
    for key in tro["removed_task_metadata"]:
        scene["metadata"]["task"].pop(key, None)
    scene["metadata"]["task"].update(deepcopy(tro["task_metadata"]))
    scene["robot"] = deepcopy(tro["robot"])
    return scene


def load_instance_scene(session, path):
    tro = read_json(path)
    if tro["session_id"] != session.session_id:
        raise ValueError(f"Instance TRO belongs to another session: {path}")
    template_path = session.paths.resolve(tro["scene_template"])
    if file_hash(template_path) != tro["scene_template_sha256"]:
        raise ValueError("Shared scene template changed after instance preparation")
    assignments_path = session.paths.resolve(tro["assignments"])
    if file_hash(assignments_path) != tro["assignments_sha256"]:
        raise ValueError("Variant assignments changed after instance preparation")
    return tro, restore_instance_scene(read_json(template_path), tro)
