from copy import deepcopy
import json

from memory_bench.utils.scene_state import read_json, scene_objects
from memory_bench.utils.segment_manifest import load_intervent_assignments


def prepared_scene(snapshot, robot_name):
    """Keep a JSON world snapshot without a preparation robot or derived task bindings."""
    scene = json.loads(json.dumps(snapshot, default=lambda value: value.tolist()))
    del scene["objects_info"]["init_info"][robot_name]
    del scene_objects(scene)[robot_name]
    scene.pop("robot", None)
    # The scene already contains its object definitions and state.
    scene["init_info"]["args"].pop("scene_file", None)
    metadata = scene["metadata"]["task"]
    metadata.pop("robot_poses", None)
    metadata.pop("inst_to_name", None)
    if "task" in metadata:
        metadata["task"].pop("inst_to_name", None)
        if not metadata["task"]:
            del metadata["task"]
    return scene


def build_instance_tro(template, snapshot, record):
    """Save exact world differences against an unchanged JSON baseline."""
    if snapshot["objects_info"] != template["objects_info"]:
        raise ValueError("Instance preparation changed the shared scene object initializers")
    original_objects = scene_objects(template)
    current_objects = scene_objects(snapshot)
    state = {
        key: deepcopy(value) for key, value in snapshot["state"].items()
        if key != "registry" and value != template["state"][key]
    }
    state["registry"] = {
        key: deepcopy(value) for key, value in snapshot["state"]["registry"].items()
        if key != "object_registry" and value != template["state"]["registry"][key]
    }
    state["registry"]["object_registry"] = {
        name: deepcopy(value) for name, value in current_objects.items() if value != original_objects[name]
    }
    original_metadata = template["metadata"]["task"]
    current_metadata = snapshot["metadata"]["task"]
    tro = {**record, "state": state}
    changed_metadata = {
        key: deepcopy(value) for key, value in current_metadata.items()
        if key not in original_metadata or original_metadata[key] != value
    }
    if changed_metadata:
        tro["task_metadata"] = changed_metadata
    removed_metadata = sorted(set(original_metadata) - set(current_metadata))
    if removed_metadata:
        tro["removed_task_metadata"] = removed_metadata
    return tro


def restore_instance_scene(template, tro):
    """Reconstruct the full checked state in memory; never write a per-instance scene."""
    scene = deepcopy(template)
    objects = scene_objects(scene)
    incoming = scene_objects(tro)
    if set(incoming) - set(objects):
        raise ValueError("Instance TRO contains objects absent from its shared scene template")
    objects.update(deepcopy(incoming))
    scene["state"].update({key: deepcopy(value) for key, value in tro["state"].items() if key != "registry"})
    scene["state"]["registry"].update({
        key: deepcopy(value) for key, value in tro["state"]["registry"].items() if key != "object_registry"
    })
    for key in tro.get("removed_task_metadata", []):
        scene["metadata"]["task"].pop(key, None)
    scene["metadata"]["task"].update(deepcopy(tro.get("task_metadata", {})))
    scene["robot"] = deepcopy(tro["robot"])
    return scene


def load_instance_scene(session, path):
    tro = read_json(path)
    if tro["session_id"] != session.session_id:
        raise ValueError(f"Instance TRO belongs to another session: {path}")
    template_path = session.paths.resolve(tro["scene_template"])
    template = read_json(template_path)
    inputs = template["metadata"]["task"]["memory_bench_inputs"]
    assignments_path = session.paths.resolve(inputs["assignments"])
    assignment = next(
        item for item in read_json(assignments_path)["instances"] if item["instance_id"] == tro["instance_id"]
    )
    # Consumers use one resolved record; bindings are stored only in the assignments file.
    intervents = load_intervent_assignments(session)
    instance = {**assignment, **tro, "intervents": intervents[str(tro["instance_id"])]}
    scene = restore_instance_scene(template, tro)
    scene["metadata"]["task"]["inst_to_name"] = deepcopy(instance["bindings"][session.key_ids[0]])
    return instance, scene
