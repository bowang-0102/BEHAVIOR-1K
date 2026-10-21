"""Raw task descriptions, interface compatibility and recipe boundaries."""

from copy import deepcopy

from memory_bench.utils.scene_state import read_json, scene_objects


def compatible_interfaces(a, b):
    fields = set(a) - {"robot_name"}
    return fields == set(b) - {"robot_name"} and all(a[key] == b[key] for key in fields)


def mapped_snapshot(snapshot, source_robot, target_robot):
    snapshot = deepcopy(snapshot)
    objects = scene_objects(snapshot)
    if source_robot != target_robot:
        objects[target_robot] = objects.pop(source_robot)
        if "robot" in snapshot:
            snapshot["robot"]["name"] = target_robot
        if "objects_info" in snapshot:
            initializers = snapshot["objects_info"]["init_info"]
            initializers[target_robot] = initializers.pop(source_robot)
            initializers[target_robot]["args"]["name"] = target_robot
            args = initializers[target_robot]["args"]
            if "relative_prim_path" in args:
                args["relative_prim_path"] = f"/{target_robot}"
            bindings = snapshot["metadata"]["task"]["inst_to_name"]
            for key, name in bindings.items():
                if name == source_robot:
                    bindings[key] = target_robot
    return snapshot


def boundary(session, segment, name, robot_name):
    from memory_bench.utils.recordings import FrameReader

    if "boundaries" in segment:
        snapshot = segment["boundaries"][name]
    elif "states" in segment:
        states = segment["states"]
        snapshot = read_json(session.paths.resolve(states["directory"]) / states[name])
    else:
        source = segment["source"]
        with FrameReader(session.paths.resolve(source["hdf5"]), source["demo"]) as reader:
            snapshot = reader.snapshot(source["frames"][0 if name == "entry" else 1])
    return mapped_snapshot(snapshot, segment["interface"]["robot_name"], robot_name)


def load_segments(session, segments):
    """Check task identities and interfaces of inline recipe segments."""
    interface = next(iter(segments.values()))["interface"]
    for key, segment in segments.items():
        if segment["identity"]["id"] != key:
            raise ValueError(f"Manifest identity mismatch for {key}")
        if not compatible_interfaces(interface, segment["interface"]):
            raise ValueError(f"Robot/controller/action/frequency mismatch for {key}")
    return segments


def load_intervent_assignments(session):
    """Read raw I sources fixed before collection, without processing trajectories."""
    root = session.paths.resolve(f"sessions/{session.session_id}")
    record = read_json(root / "intervent_assignments.json")
    assignments_path = root / "case_assignments.json"
    if record["session_id"] != session.session_id:
        raise ValueError("Intervention allocation belongs to another session")
    expected_ids = {str(item["instance_id"]) for item in read_json(assignments_path)["instances"]}
    instances = record["instances"]
    expected_keys = {key for key, definition in session.segments.items() if definition["role"] == "intervent_task"}
    if set(instances) != expected_ids or any(set(refs) != expected_keys for refs in instances.values()):
        raise ValueError("Intervention allocation must cover every instance and every I exactly once")
    identities = {key: set() for key in expected_keys}
    for refs in instances.values():
        for key, source in refs.items():
            identity = (str(session.paths.resolve(source["hdf5"]).resolve()), source["demo"])
            if identity in identities[key]:
                raise ValueError(f"Intervention allocation repeats a source/demo for {key}")
            identities[key].add(identity)
    return instances
