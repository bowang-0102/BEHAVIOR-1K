import argparse
import sys
from copy import deepcopy
import json
import os
import random
import traceback

import h5py

from memory_bench.session import Session
from memory_bench.utils.instance_scene import load_instance_scene
from memory_bench.utils.recordings import FrameReader
from memory_bench.utils.scene_state import (
    get_state_tolerances, merge_scene_assets, read_json, require_objects_match,
    require_robot_match, scene_objects, write_json_atomic,
)
from memory_bench.utils.segment_manifest import boundary, compatible_interfaces, load_collection_manifest, load_segments
from memory_bench.utils.task_segments import check_frame, describe_collected, describe_intervent


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


def build_bridges(session, scene, order, segments, episode_id, instance_id):
    """Generate physically validated bridges in the inherited world."""
    from memory_bench.episodes.generate_bridges import generate_bridge

    interface = {**segments[order[0]]["interface"], "robot_name": scene["robot"]["name"]}
    robot_name = interface["robot_name"]
    tolerance = get_state_tolerances()
    world = deepcopy(scene)
    sequence = []
    bridges = {}
    protected = read_json(session.paths.session_dir / "assets.json")["protected"]

    for index, key in enumerate(order):
        segment = segments[key]
        entry = boundary(session, segment, "entry", robot_name)
        # Task inputs must already match; Bridges only move the robot.
        require_objects_match(world, entry, segment["objects"]["read"], tolerance, f"{key} preconditions")
        # The first task starts at its entry; each later task needs a connecting Bridge.
        if index:
            bridge_id = f"bridge_{index - 1:02d}"
            output = session.paths.bridges_dir / episode_id / f"{bridge_id}.hdf5"
            existing = output.exists()
            # Reuse a completed Bridge only if its boundaries still match the current connection.
            if existing:
                with h5py.File(output, "r") as stream:
                    if "memory_bench_bridge" not in stream["data"].attrs:
                        raise ValueError(f"Incomplete Bridge output: {output}")
                    bridge = json.loads(stream["data"].attrs["memory_bench_bridge"])
                saved_entry = boundary(session, bridge, "entry", robot_name)
                require_objects_match(saved_entry, world, set(scene_objects(world)), tolerance, "Saved Bridge entry")
                require_robot_match(saved_entry, world, robot_name, tolerance, "Saved Bridge entry")
            else:
                bridge = generate_bridge(session, deepcopy(world), deepcopy(world), entry, output)
            bridge["identity"] = {"id": bridge_id, "instance_id": instance_id}
            bridge["task"].update(
                id=bridge_id, role="bridge_task", instruction=segment["task"]["instruction"], depends_on=[]
            )
            if not compatible_interfaces(interface, bridge["interface"]):
                raise ValueError("Synthesized bridge has an incompatible robot interface")
            bridge["source"]["fps"] = interface["fps"]
            bridges[bridge_id] = bridge
            sequence.append(bridge)
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
        sequence.append(segment)
        exit_state = boundary(session, segment, "exit", robot_name)
        # Bridges must inherit incidental task changes as well as declared writes.
        changed = check_frame(segment, exit_state["state"], entry["state"], protected)
        for name in set(segment["objects"]["write"]) | changed | {robot_name}:
            scene_objects(world)[name] = deepcopy(scene_objects(exit_state)[name])
        world["robot"] = deepcopy(exit_state["robot"])
    return sequence, bridges


def build_episode(session, instance_id, order):
    """Build an episode recipe and its physical Bridges."""
    episode_id = f"episode_{instance_id - 1:08d}"
    output = session.paths.recipes_dir / f"{episode_id}.json"
    if output.exists():
        raise FileExistsError(f"Recipe already exists: {output}")

    # Load the prepared instance and the selected K/Q collection run.
    tro_path = session.paths.session_dir / "tro" / f"instance_{instance_id:04d}.json"
    instance, scene = load_instance_scene(session, tro_path)

    demo_id = session.composition["demo_id"]
    manifest_path = (
        session.paths.recordings_dir / f"instance_{instance_id:04d}" / f"demo_{demo_id:03d}" / "manifest.json"
    )
    record = load_collection_manifest(session, instance, manifest_path)
    segments = {
        key: describe_collected(session, key, instance, item)
        for key, item in record["segments"].items()
    }

    # Add the assigned I recordings and decode their boundary states.
    for key, source in instance["intervents"].items():
        segment = describe_intervent(session, key, source, instance_id)
        with FrameReader(session.paths.resolve(source["hdf5"]), source["demo"]) as reader:
            segment["boundaries"] = {name: reader.snapshot(frame) for name, frame in
                                     (("entry", 0), ("exit", len(reader.group["action"])))}
        segments[key] = segment

    load_segments(segments)
    assets = read_json(session.paths.session_dir / "assets.json")
    protected = set(assets["protected"])
    check_assets(session, segments, protected)

    # Initialize the robot from the first segment, then connect tasks with Bridges in the requested order.
    robot = segments[session.key_ids[0]]["interface"]["robot_name"]
    first_entry = boundary(session, segments[order[0]], "entry", robot)
    scene = merge_scene_assets(scene, first_entry, (robot,))
    scene["robot"] = deepcopy(first_entry["robot"])

    sequence, bridges = build_bridges(session, scene, order, segments, episode_id, instance_id)

    for segment in sequence:
        segment.pop("boundaries", None)

    # Save source references and the task/Bridge sequence for later composition.
    recipe = {
        "session_id": session.session_id, "episode_id": episode_id, "instance_id": instance_id,
        "instance": str(tro_path.relative_to(session.paths.output_root)),
        "task_order": order,
        "segments": segments, "bridges": bridges,
        "sequence": [segment["identity"]["id"] for segment in sequence],
    }
    write_json_atomic(output, recipe)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--instance-id", type=int, help="Override composition.instance_id")
    parser.add_argument("--seed", type=int, help="Override composition.order_seed")
    parser.add_argument("--demo-id", type=int, help="Override composition.demo_id")
    args = parser.parse_args()

    session = Session.load(args.config)
    if args.instance_id is not None:
        session.composition["instance_id"] = args.instance_id
    if args.seed is not None:
        session.composition["order_seed"] = args.seed
    if args.demo_id is not None:
        session.composition["demo_id"] = args.demo_id
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)
    order_seed = session.composition["order_seed"]
    rng = random.Random(order_seed)
    selected_instance = session.composition["instance_id"]

    for instance_id in range(1, session.instance_count + 1):
        order = rng.choice(session.composition["task_orders"])
        if selected_instance is None or selected_instance == instance_id:
            print(build_episode(session, instance_id, order))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Kit fast shutdown can exit before Python displays the unhandled exception.
        traceback.print_exc()
        sys.stderr.flush()
        # Kit fast shutdown must preserve a failing CLI exit status.
        og = sys.modules.get("omnigibson")
        if og is not None and og.app is not None:
            og.app._app.post_quit(1)
        raise
    finally:
        og = sys.modules.get("omnigibson")
        if og is not None and og.app is not None:
            og.shutdown()
