"""Compose one explicit recipe while inheriting unrelated world state."""

import argparse
import json
from copy import deepcopy

import h5py

from memory_bench.processing.normalize_recording import normalize_recording
from memory_bench.processing.recordings import FrameReader
from memory_bench.session import Session
from memory_bench.utils.state import (
    file_hash,
    merge_scene_assets,
    read_json,
    require_objects_match,
    require_robot_match,
    scene_objects,
    write_json,
)


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


def check_assets(session, segments, protected):
    intervals = [key for key, item in session.segments.items() if item["role"] == "intervent_task"]
    occupied = set(protected)
    for key in intervals:
        usage = (
            set(segments[key]["objects"]["used"])
            | set(segments[key]["objects"]["read"])
            | set(segments[key]["objects"]["write"])
        )
        if usage & occupied:
            raise ValueError(f"Asset isolation conflict for {key}: {sorted(usage & occupied)}")
        occupied.update(usage)
    for key in [*session.key_ids, session.query_id]:
        conflicts = set(segments[key]["objects"]["used"]) & (occupied - set(protected))
        if conflicts:
            raise ValueError(f"K/Q asset isolation conflict for {key}: {sorted(conflicts)}")


def compose_episode(session, pairing, bridge_builder=None):
    """bridge_builder is the physical backend; tests may supply a canonical fixture backend."""
    if bridge_builder is None:
        from memory_bench.processing.synthesize_bridges import synthesize_bridge

        bridge_builder = synthesize_bridge
    order = pairing["task_order"]
    session.validate_order(order)
    if order not in session.config["composition"]["task_orders"]:
        raise ValueError("Recipe order was not frozen in session configuration")
    episode_id = pairing["episode_id"]
    instance = read_json(session.paths.resolve(pairing["instance"]))
    scene = read_json(session.paths.resolve(pairing["scene"]))
    if file_hash(session.paths.resolve(pairing["scene"])) != instance["scene_sha256"]:
        raise ValueError("Frozen instance scene changed after preparation")
    assets = read_json(session.paths.resolve(session.config["assets"]["manifest"]))
    segments = {key: read_json(session.paths.resolve(path)) for key, path in pairing["segments"].items()}
    if set(segments) != set(session.segments):
        raise ValueError("One source manifest is required for each logical task")
    interface = segments[session.key_ids[0]]["interface"]
    robot_name = interface["robot_name"]
    tolerance = session.config["validation"]["state_tolerances"]
    protected = set(assets["protected"])
    check_assets(session, segments, protected)
    for key, segment in segments.items():
        if segment["identity"]["id"] != key:
            raise ValueError(f"Manifest identity mismatch for {key}")
        if (
            segment["task"]["role"] != "intervent_task"
            and segment["identity"]["instance_id"] != instance["instance_id"]
        ):
            raise ValueError(f"K/Q source belongs to another instance: {key}")
        if not compatible_interfaces(interface, segment["interface"]):
            raise ValueError(f"Robot/controller/action/frequency mismatch for {key}")
        if (
            segment["task"]["activity"] != session.segments[key]["activity"]
            or segment["task"]["room_id"] != session.segments[key]["room_id"]
        ):
            raise ValueError(f"Task/room mismatch for {key}")
        source = segment["source"]
        if file_hash(session.paths.resolve(source["hdf5"])) != source["sha256"]:
            raise ValueError(f"Source changed after freezing: {key}")
        if (
            "normalized_hdf5" in source
            and file_hash(session.paths.resolve(source["normalized_hdf5"])) != source["normalized_sha256"]
        ):
            raise ValueError(f"Normalized source changed after freezing: {key}")
        if key in instance["bindings"]:
            for name, bound in segment["objects"]["memory_bindings"].items():
                if instance["bindings"][key][name] != bound:
                    raise ValueError(f"Frozen memory identity mismatch: {key}/{name}")
        if segment["task"]["role"] == "intervent_task":
            entry = boundary(session, segment, "entry", robot_name)
            allowed = set(assets["intervent_objects"][key])
            if not set(segment["objects"]["used"]).issubset(allowed):
                raise ValueError(f"I source uses assets outside the frozen declaration: {key}")
            scene = merge_scene_assets(scene, entry, allowed)
    first_entry = boundary(session, segments[order[0]], "entry", robot_name)
    scene_objects(scene)[robot_name] = deepcopy(scene_objects(first_entry)[robot_name])
    scene["robot"] = deepcopy(first_entry["robot"])
    episode_dir = session.paths.output_root / "episodes" / session.session_id
    episode_dir.mkdir(parents=True, exist_ok=True)
    episode_path = episode_dir / f"{episode_id}.hdf5"
    scene_path = episode_dir / f"{episode_id}_scene.json"
    recipe_path = session.paths.output_root / "recipes" / session.session_id / f"{episode_id}.json"
    for path in (episode_path, scene_path, recipe_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")
    world = deepcopy(scene)
    assembled = []
    bridge_refs = {}
    for index, key in enumerate(order):
        segment = segments[key]
        entry = boundary(session, segment, "entry", robot_name)
        require_objects_match(world, entry, segment["objects"]["read"], tolerance, f"{key} preconditions")
        if index:
            bridge_id = f"bridge_{index - 1:02d}"
            output = session.paths.output_root / "bridges" / session.session_id / episode_id / f"{bridge_id}.hdf5"
            bridge = bridge_builder(session, deepcopy(world), deepcopy(world), entry, output)
            bridge["identity"] = {"id": bridge_id, "instance_id": instance["instance_id"]}
            bridge["task"].update(
                id=bridge_id, role="bridge_task", instruction=segment["task"]["instruction"], depends_on=[]
            )
            if not compatible_interfaces(interface, bridge["interface"]):
                raise ValueError("Synthesized bridge has an incompatible robot interface")
            bridge_source = bridge["source"]
            normalized = output.with_name(output.stem + "_normalized.hdf5")
            with h5py.File(session.paths.resolve(bridge_source["hdf5"]), "r") as stream:
                canonical = "state_json" in stream["data"][f"demo_{bridge_source['demo']}"]
            if not canonical:
                terminal = session.paths.resolve(bridge["states"]["directory"]) / bridge["states"]["exit"]
                normalize_recording(session, output, normalized, bridge_source["demo"], terminal)
                bridge_source.update(
                    normalized_hdf5=session.paths.relative(normalized),
                    normalized_demo=0,
                    normalized_sha256=file_hash(normalized),
                )
            bridge_source["sha256"] = file_hash(session.paths.resolve(bridge_source["hdf5"]))
            bridge_source["fps"] = interface["fps"]
            bridge_path = output.with_suffix(".json")
            write_json(bridge_path, bridge)
            bridge_refs[bridge_id] = session.paths.relative(bridge_path)
            assembled.append(bridge)
            previous_world = world
            world = boundary(session, bridge, "exit", robot_name)
            require_objects_match(
                world,
                previous_world,
                set(scene_objects(previous_world)) - {robot_name},
                tolerance,
                "Bridge changed nonrobot world",
            )
            require_robot_match(world, entry, robot_name, tolerance, "Bridge endpoint")
        else:
            require_robot_match(world, entry, robot_name, tolerance, "First robot entry")
        assembled.append(segment)
        exit_state = boundary(session, segment, "exit", robot_name)
        for name in [*segment["objects"]["write"], robot_name]:
            scene_objects(world)[name] = deepcopy(scene_objects(exit_state)[name])
        world["robot"] = deepcopy(exit_state["robot"])
    # Build only after all bridges and preconditions succeeded. A failed build retains a report, not an accepted recipe.
    write_json(scene_path, scene)
    annotations = []
    current = deepcopy(scene["state"])
    total = sum(item["source"]["frames"][1] - item["source"]["frames"][0] for item in assembled)
    with h5py.File(episode_path, "x") as stream:
        data = stream.create_group("data")
        first_source = segments[order[0]]["source"]
        with h5py.File(session.paths.resolve(first_source["hdf5"]), "r") as source_file:
            data.attrs["config"] = source_file["data"].attrs["config"]
        data.attrs["scene_file"] = json.dumps(scene)
        data.attrs["format"] = "memory_bench.state_json.v1"
        data.attrs["n_episodes"] = 1
        data.attrs["n_steps"] = total
        group = data.create_group("demo_0")
        group.attrs["num_samples"] = total
        group.attrs["transitions"] = "{}"
        states = group.create_dataset("state_json", (total + 1,), dtype=h5py.string_dtype())
        actions = group.create_dataset("action", (total, interface["action_dim"]), dtype="float32", compression="lzf")
        instructions = group.create_dataset("instruction", (total,), dtype=h5py.string_dtype())
        for field, dtype in (("reward", "float32"), ("terminated", "bool"), ("truncated", "bool")):
            group.create_dataset(field, (total,), dtype=dtype)
        cursor = 0
        for item in assembled:
            source = item["source"]
            source_path = source["normalized_hdf5"] if "normalized_hdf5" in source else source["hdf5"]
            reader = FrameReader(session.paths.resolve(source_path), source.get("normalized_demo", source["demo"]))
            start, end = source["frames"]
            names = [*item["objects"]["write"], robot_name]
            before = cursor
            try:
                baseline = boundary(session, item, "entry", robot_name)
                for frame in range(start, end + 1):
                    incoming = mapped_snapshot(reader.state(frame), item["interface"]["robot_name"], robot_name)
                    # Preserve protected memory throughout I/B, including temporary changes between their boundaries.
                    if item["task"]["role"] in ("intervent_task", "bridge_task"):
                        check_names = protected & set(scene_objects(incoming))
                        require_objects_match(incoming, baseline, check_names, tolerance, "I/B altered memory")
                    for name in names:
                        scene_objects(current)[name] = deepcopy(scene_objects(incoming)[name])
                    if frame == end:
                        continue
                    states[cursor] = json.dumps(current, separators=(",", ":"))
                    actions[cursor] = reader.group["action"][frame]
                    instructions[cursor] = item["task"]["instruction"]
                    for field in ("reward", "terminated", "truncated"):
                        group[field][cursor] = reader.group[field][frame]
                    cursor += 1
            finally:
                reader.close()
            annotations.append(
                {
                    "id": item["identity"]["id"],
                    **item["task"],
                    "frames": [before, cursor],
                    "source_manifest": bridge_refs[item["identity"]["id"]]
                    if item["task"]["role"] == "bridge_task"
                    else pairing["segments"][item["identity"]["id"]],
                    "objects": item["objects"],
                    "bindings": instance["bindings"].get(item["identity"]["id"], {}),
                    "object_events": [
                        {**event, "source_frame": event["frame"], "frame": before + event["frame"] - start}
                        for event in item["validation"].get("object_events", [])
                    ],
                }
            )
        states[total] = json.dumps(current, separators=(",", ":"))
    by_id = {item["id"]: item for item in annotations}
    spans = [
        {
            "key_id": key,
            "query_id": session.query_id,
            "key_end_frame": by_id[key]["frames"][1],
            "query_start_frame": by_id[session.query_id]["frames"][0],
            "distance_seconds": (by_id[session.query_id]["frames"][0] - by_id[key]["frames"][1]) / interface["fps"],
        }
        for key in session.key_ids
    ]
    recipe = {
        "session_id": session.session_id,
        "instance_id": instance["instance_id"],
        "episode_id": episode_id,
        "task_order": order,
        "segments": pairing["segments"],
        "bridges": bridge_refs,
        "replay_scene": session.paths.relative(scene_path),
        "hdf5": session.paths.relative(episode_path),
        "benchmark": session.config["benchmark"],
        "annotations": annotations,
        "memory_spans": spans,
        "case_id": instance["case_id"],
        "key_information": session.task.key_information,
        "total_frames": total,
        "fps": interface["fps"],
        "interface": interface,
    }
    write_json(recipe_path, recipe)
    return recipe_path


def compose_batch(session, pairings):
    count = session.instance_count
    if len(pairings) != count:
        raise ValueError(f"Expected {count} material groups, received {len(pairings)}")
    if len({item["instance"] for item in pairings}) != count or len({item["episode_id"] for item in pairings}) != count:
        raise ValueError("Instances and episode IDs must be one-to-one")
    if {item["episode_id"] for item in pairings} != {f"episode_{index:08d}" for index in range(count)}:
        raise ValueError("Episode IDs must be consecutive from episode_00000000")
    if {read_json(session.paths.resolve(item["instance"]))["instance_id"] for item in pairings} != set(
        range(1, count + 1)
    ):
        raise ValueError("Instance IDs must be consecutive and unique from one")
    for key, definition in session.segments.items():
        if definition["role"] == "intervent_task":
            selected = [read_json(session.paths.resolve(item["segments"][key]))["source"] for item in pairings]
            if len({(item["hdf5"], item["demo"]) for item in selected}) != count:
                raise ValueError(f"I recordings must be selected without replacement: {key}")
    index = []
    for pairing in pairings:
        try:
            recipe = compose_episode(session, pairing)
            index.append(
                {
                    "instance": pairing["instance"],
                    "episode_id": pairing["episode_id"],
                    "recipe": session.paths.relative(recipe),
                }
            )
        except Exception as error:
            report = (
                session.paths.output_root / "reports" / session.session_id / f"{pairing['episode_id']}_failure.json"
            )
            write_json(report, {"pairing": pairing, "error": str(error)})
            raise
    write_json(session.paths.output_root / "sessions" / session.session_id / "source_pairings.json", index)
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("pairings", help="JSON list of explicit material groups and assigned task_order")
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    compose_batch(session, read_json(session.paths.resolve(args.pairings)))


if __name__ == "__main__":
    main()
