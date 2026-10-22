"""Step 3: build episode recipes from selected recordings and physical bridges."""

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
    require_robot_match, scene_objects, write_json_atomic,
)
from memory_bench.utils.segment_manifest import boundary, compatible_interfaces, load_collection_manifest, load_segments
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
    """Generate bridges in the inherited world; builders return physically validated segments."""
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
            exit_state = boundary(session, bridge, "exit", robot_name)
            if existing:
                require_objects_match(
                    exit_state, world, set(scene_objects(world)) - {robot_name},
                    tolerance, "Bridge changed nonrobot world",
                )
                require_robot_match(exit_state, entry, robot_name, tolerance, "Bridge endpoint")
            world = exit_state
            if not existing:
                # Save successful resume information in the raw file, without a sidecar manifest.
                with h5py.File(output, "r+") as stream:
                    stream["data"].attrs["memory_bench_bridge"] = json.dumps(bridge)
        assembled.append(segment)
        exit_state = boundary(session, segment, "exit", robot_name)
        for name in [*segment["objects"]["write"], robot_name]:
            scene_objects(world)[name] = deepcopy(scene_objects(exit_state)[name])
        world["robot"] = deepcopy(exit_state["robot"])
    return assembled, bridge_refs


def select_demo(session, instance, demo_id=1):
    path, record = load_collection_manifest(session, instance, f"demo_{demo_id:03d}")
    segments = {
        key: describe_collected(session, key, instance, item)
        for key, item in record["segments"].items()
    }
    return path, record, segments


def build_episode(session, instance_id, order, demo_id=1, bridge_builder=None):
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
