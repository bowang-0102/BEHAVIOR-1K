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
