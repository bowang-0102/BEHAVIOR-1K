"""Step 3: choose one collection demo, build physical bridges, and publish one recipe."""

import argparse
import sys
from copy import deepcopy
import json
import random

import h5py

from memory_bench.session import Session
from memory_bench.utils.instance_scene import load_instance_scene
from memory_bench.utils.recordings import FrameReader
from memory_bench.utils.scene_state import (
    get_state_tolerances, merge_scene_assets, read_json, require_objects_match,
    require_robot_match, scene_objects, state_matches, scene_systems, write_json_atomic,
)
from memory_bench.utils.segment_manifest import boundary, compatible_interfaces, load_segments
from memory_bench.utils.task_segments import describe_collected, describe_intervent


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


def connect_segments(session, scene, order, segments, episode_id, instance_id=None, bridge_builder=None):
    """Generate physical bridges in the world inherited from preceding task exits."""
    if bridge_builder is None:
        from memory_bench.episodes.synthesize_bridges import synthesize_bridge

        bridge_builder = synthesize_bridge
    interface = {**segments[order[0]]["interface"], "robot_name": scene["robot"]["name"]}
    robot_name = interface["robot_name"]
    tolerance = get_state_tolerances()
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
            existing = output.exists()
            if existing:
                with h5py.File(output, "r") as stream:
                    if "memory_bench_bridge" not in stream["data"].attrs:
                        raise ValueError(f"Incomplete Bridge output: {output}")
                    bridge = json.loads(stream["data"].attrs["memory_bench_bridge"])
                saved_entry = boundary(session, bridge, "entry", robot_name)
                require_objects_match(saved_entry, world, set(scene_objects(world)), tolerance, "Saved Bridge entry")
                require_robot_match(saved_entry, world, robot_name, tolerance, "Saved Bridge entry")
            else:
                bridge = bridge_builder(session, deepcopy(world), deepcopy(world), entry, output)
            bridge["identity"] = {"id": bridge_id, "instance_id": instance_id}
            bridge["task"].update(
                id=bridge_id, role="bridge_task", instruction=segment["task"]["instruction"], depends_on=[]
            )
            if not compatible_interfaces(interface, bridge["interface"]):
                raise ValueError("Synthesized bridge has an incompatible robot interface")
            bridge["source"]["fps"] = interface["fps"]
            bridge_refs[bridge_id] = bridge
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
            if not existing:
                # Save successful resume information in the raw file, without a sidecar manifest.
                with h5py.File(output, "r+") as stream:
                    stream["data"].attrs["memory_bench_bridge"] = json.dumps(bridge)
        else:
            require_robot_match(world, entry, robot_name, tolerance, "First robot entry")
        assembled.append(segment)
        exit_state = boundary(session, segment, "exit", robot_name)
        for name in [*segment["objects"]["write"], robot_name]:
            scene_objects(world)[name] = deepcopy(scene_objects(exit_state)[name])
        world["robot"] = deepcopy(exit_state["robot"])
    return assembled, bridge_refs


def select_demo(session, instance, demo_id=1):
    directory = session.paths.resolve(
        f"recordings/{session.session_id}/instance_{instance['instance_id']:04d}/demo_{demo_id:03d}"
    )
    path = directory / "manifest.json"
    record = read_json(path)
    if record["status"] != "complete" or record.get("invalid_reason"):
        raise ValueError(f"Collection demo is incomplete or invalid: {directory}")
    for name, expected in (("session_id", session.session_id), ("instance_id", instance["instance_id"]),
                           ("case_id", instance["case_id"]), ("run_id", directory.name)):
        if record[name] != expected:
            raise ValueError(f"Collection demo {name} differs from its instance: {path}")
    tro_path = session.paths.resolve(f"sessions/{session.session_id}/tro/instance_{instance['instance_id']:04d}.json")
    if session.paths.resolve(record["instance"]).resolve() != tro_path.resolve():
        raise ValueError(f"Collection demo uses a different TRO: {path}")
    keys = [*session.key_ids, session.query_id]
    if record["segment_ids"] != keys or set(record["segments"]) != set(keys):
        raise ValueError("Collection demo must contain every Key and Query in order")
    segments = {}
    for key in keys:
        item = record["segments"][key]
        source = session.paths.resolve(item["hdf5"]).resolve()
        if source.parent != directory.resolve() or item["demo"] != 0:
            raise ValueError("K/Q must use distinct files in the selected collection demo")
        with h5py.File(source, "r") as stream:
            data = stream["data"]
            if not data.attrs.get("collection_complete", False):
                raise ValueError(f"Incomplete collected source: {key}")
            for name, expected in (("instance_id", instance["instance_id"]), ("case_id", instance["case_id"]),
                                   ("segment_id", key), ("run_id", directory.name), ("session_id", session.session_id)):
                if data.attrs[name] != expected:
                    raise ValueError(f"Collected source {name} differs from its demo: {key}")
            if item["frames"] != [0, len(data["demo_0/action"])]:
                raise ValueError(f"Collected source must use its entire local action range: {key}")
        required = ("entry", "exit", "empty_hands", "stable", "collision_free")
        if not all(item["validation"].get(name) is True for name in required):
            raise ValueError(f"Collected boundary checks failed: {key}")
        if any(event.get("forbidden", False) for event in item["validation"].get("object_events", [])):
            raise ValueError("Query includes forbidden object events")
        if session.paths.resolve(item["states"]["directory"]).resolve() != directory.resolve():
            raise ValueError("Collected boundaries must belong to the selected demo")
        segments[key] = describe_collected(session, key, instance, item)
    transfers = record["transfers"]
    if [(item["from_segment"], item["to_segment"]) for item in transfers] != list(zip(keys, keys[1:])):
        raise ValueError("Collection transfers must connect adjacent subtasks in order")
    for item in transfers:
        if not all(item["validation"].get(name) is True for name in ("nonrobot_world_preserved", "entry_pose")):
            raise ValueError("Collection transfer checks failed")
    robot = segments[keys[0]]["interface"]["robot_name"]
    tolerance = get_state_tolerances()
    first = boundary(session, segments[keys[0]], "entry", robot)
    if not state_matches({"pos": first["robot"]["position"], "ori": first["robot"]["orientation"]},
                         {"pos": instance["robot"]["position"], "ori": instance["robot"]["orientation"]}, tolerance):
        raise ValueError("Collection robot start differs from the sampled base pose")
    for key in keys:
        require_robot_match(
            boundary(session, segments[key], "entry", robot), first, robot, tolerance, "Collection entry pose"
        )
    for item in transfers:
        previous = boundary(session, segments[item["from_segment"]], "exit", robot)
        current = boundary(session, segments[item["to_segment"]], "entry", robot)
        names = set(scene_objects(previous)) - {robot}
        if set(scene_objects(current)) - {robot} != names:
            raise ValueError("Collection transfer changed the nonrobot object set")
        require_objects_match(current, previous, names, tolerance, "Collection transfer")
        if not state_matches(scene_systems(current), scene_systems(previous), tolerance):
            raise ValueError("Collection transfer changed particle systems")
    return path, record, segments


def build_episode(session, instance_id, order, demo_id=1, bridge_builder=None):
    session.validate_order(order)
    if order not in session.composition["task_orders"]:
        raise ValueError("Task order was not declared in the session")
    episode_id = f"episode_{instance_id - 1:08d}"
    output = session.paths.resolve(f"recipes/{session.session_id}/{episode_id}.json")
    if output.exists():
        raise FileExistsError(f"Recipe already exists: {output}")
    tro_path = session.paths.resolve(f"sessions/{session.session_id}/tro/instance_{instance_id:04d}.json")
    instance, scene = load_instance_scene(session, tro_path)
    demo_path, _, segments = select_demo(session, instance, demo_id)
    for key, source in instance["intervents"].items():
        segment = describe_intervent(session, key, source, instance_id)
        with FrameReader(session.paths.resolve(source["hdf5"]), source["demo"]) as reader:
            segment["boundaries"] = {name: reader.snapshot(frame) for name, frame in
                                     (("entry", 0), ("exit", len(reader.group["action"])))}
        segments[key] = segment
    load_segments(session, segments)
    assets = read_json(session.paths.resolve(session.assets["manifest"]))
    protected = set(assets["protected"])
    check_assets(session, segments, protected)
    robot = segments[session.key_ids[0]]["interface"]["robot_name"]
    for key in instance["intervents"]:
        segment = segments[key]
        initial = boundary(session, segment, "entry", robot)
        require_objects_match(
            scene, initial, segment["objects"]["read"], get_state_tolerances(), f"{key} initial layout"
        )
        for kq in [*session.key_ids, session.query_id]:
            for name in ("entry", "exit"):
                require_objects_match(
                    boundary(session, segments[kq], name, robot), initial,
                    segment["objects"]["used"], get_state_tolerances(), f"Collection changed {key} layout",
                )
    first_entry = boundary(session, segments[order[0]], "entry", robot)
    scene = merge_scene_assets(scene, first_entry, (robot,))
    scene["robot"] = deepcopy(first_entry["robot"])
    assembled, bridges = connect_segments(session, scene, order, segments, episode_id, instance_id, bridge_builder)
    for segment in assembled:
        segment.pop("boundaries", None)
    recipe = {
        "session_id": session.session_id, "episode_id": episode_id, "instance_id": instance_id,
        "instance": session.paths.relative(tro_path),
        "collection_demo": f"demo_{demo_id:03d}", "collection_manifest": session.paths.relative(demo_path),
        "task_order": order,
        "segments": segments, "bridges": bridges,
        "sequence": [segment["identity"]["id"] for segment in assembled],
    }
    write_json_atomic(output, recipe)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--demo-id", type=int, default=1)
    parser.add_argument("--instance-id", type=int)
    parser.add_argument("--order-seed", type=int, default=1)
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    if args.order_seed == session.instance_sampling["seed"]:
        raise ValueError("Use an order seed independent of the instance seed")
    rng = random.Random(args.order_seed)
    for instance_id in range(1, session.instance_count + 1):
        order = rng.choice(session.composition["task_orders"])
        if args.instance_id is None or args.instance_id == instance_id:
            print(build_episode(session, instance_id, order, args.demo_id))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Kit fast shutdown must preserve a failing CLI exit status.
        og = sys.modules.get("omnigibson")
        if og is not None and og.app is not None:
            og.app._app.post_quit(1)
        raise
    finally:
        og = sys.modules.get("omnigibson")
        if og is not None and og.app is not None:
            og.shutdown()
