"""Load segment manifests and check source integrity, interfaces and boundaries."""

from copy import deepcopy

from memory_bench.utils.scene_state import file_hash, read_json, scene_objects


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
    path = session.paths.resolve(segment["states"]["directory"]) / segment["states"][name]
    return mapped_snapshot(read_json(path), segment["interface"]["robot_name"], robot_name)


def load_segments(session, references):
    """Check frozen sources and a common interface before connecting or replaying them."""
    segments = {key: read_json(session.paths.resolve(path)) for key, path in references.items()}
    interface = next(iter(segments.values()))["interface"]
    for key, segment in segments.items():
        if segment["identity"]["id"] != key:
            raise ValueError(f"Manifest identity mismatch for {key}")
        if not compatible_interfaces(interface, segment["interface"]):
            raise ValueError(f"Robot/controller/action/frequency mismatch for {key}")
        source = segment["source"]
        if file_hash(session.paths.resolve(source["hdf5"])) != source["sha256"]:
            raise ValueError(f"Source changed after freezing: {key}")
        if (
            "normalized_hdf5" in source
            and file_hash(session.paths.resolve(source["normalized_hdf5"])) != source["normalized_sha256"]
        ):
            raise ValueError(f"Normalized source changed after freezing: {key}")
    return segments


def validate_prepared_segment(session, key, path):
    """Read one frozen segment and verify its source, normalization and boundaries."""
    manifest = load_segments(session, {key: session.paths.relative(path)})[key]
    states = manifest["states"]
    for name in ("entry", "exit"):
        boundary_path = session.paths.resolve(states["directory"]) / states[name]
        if file_hash(boundary_path) != states["sha256"][name]:
            raise ValueError(f"Prepared boundary changed after freezing: {boundary_path}")
    return manifest


def load_intervent_assignments(session):
    """Read the per-instance I allocation fixed before collection."""
    root = session.paths.resolve(f"sessions/{session.session_id}")
    record = read_json(root / "intervent_segments.json")
    assignments_path = root / "case_assignments.json"
    if record["session_id"] != session.session_id or record["assignments_sha256"] != file_hash(assignments_path):
        raise ValueError("Intervention allocation does not match the frozen case assignments")
    expected_ids = {str(item["instance_id"]) for item in read_json(assignments_path)["instances"]}
    instances = record["instances"]
    expected_keys = {key for key, definition in session.segments.items() if definition["role"] == "intervent_task"}
    if set(instances) != expected_ids or any(set(refs) != expected_keys for refs in instances.values()):
        raise ValueError("Intervention allocation must cover every instance and every I exactly once")
    paths = {path for refs in instances.values() for path in refs.values()}
    if set(record["files"]) != paths:
        raise ValueError("Intervention allocation must freeze every referenced segment manifest")
    for path, expected in record["files"].items():
        if file_hash(session.paths.resolve(path)) != expected:
            raise ValueError(f"Allocated intervention manifest changed after freezing: {path}")
    identities = {key: set() for key in expected_keys}
    for refs in instances.values():
        interface = None
        for key, path in refs.items():
            manifest = validate_prepared_segment(session, key, session.paths.resolve(path))
            if interface is not None and not compatible_interfaces(interface, manifest["interface"]):
                raise ValueError("Allocated intervention segments have incompatible robot interfaces")
            interface = manifest["interface"]
            source = manifest["source"]
            identity = (str(session.paths.resolve(source["hdf5"]).resolve()), source["demo"])
            if identity in identities[key]:
                raise ValueError(f"Intervention allocation repeats a source/demo for {key}")
            identities[key].add(identity)
    return instances
