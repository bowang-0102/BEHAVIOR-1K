import argparse
import json
import os
import random
import sys
import traceback
from copy import deepcopy

import h5py

from memory_bench.session import Session
from memory_bench.utils.episode.entry_selection import find_entry_frame
from memory_bench.utils.episode.recipe import recipe_segment
from memory_bench.utils.recording.reader import FrameReader, compatible_interfaces
from memory_bench.utils.recording.segments import (
    check_frame,
    describe_collected,
    describe_intervent,
    load_collection_manifest,
    load_segment_boundary,
    require_write_scope,
    validate_segments,
)
from memory_bench.utils.scene.instances import load_instance_scene
from memory_bench.utils.scene.state import (
    get_state_tolerances,
    merge_scene_assets,
    read_json,
    require_objects_match,
    require_robot_match,
    scene_objects,
    write_json_atomic,
)


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
    target_objects = set(scene["objects_info"]["init_info"])

    for index, key in enumerate(order):
        segment = segments[key]
        entry = load_segment_boundary(session, segment, "entry", robot_name)
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
                saved_entry = load_segment_boundary(session, bridge, "entry", robot_name)
                require_objects_match(saved_entry, world, set(scene_objects(world)), tolerance, "Saved Bridge entry")
                require_robot_match(saved_entry, world, robot_name, tolerance, "Saved Bridge entry")
            else:
                bridge = generate_bridge(session, deepcopy(world), deepcopy(world), entry, output)
            bridge["identity"] = {"id": bridge_id, "instance_id": instance_id}
            bridge["task"].update(
                id=bridge_id, role="bridge_task", instruction="", depends_on=[]
            )
            if not compatible_interfaces(interface, bridge["interface"]):
                raise ValueError("Synthesized bridge has an incompatible robot interface")
            bridge["source"]["fps"] = interface["fps"]
            bridges[bridge_id] = bridge
            sequence.append(bridge)
            exit_state = load_segment_boundary(session, bridge, "exit", robot_name)
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
        exit_state = load_segment_boundary(session, segment, "exit", robot_name)
        # Bridges must inherit incidental task changes as well as declared writes.
        # Boundaries already use the episode robot name, including legacy I sources.
        changed = check_frame({**segment, "interface": interface}, exit_state["state"], entry["state"], protected)
        require_write_scope(segment, changed, target_objects)
        for name in set(segment["objects"]["write"]) | changed | {robot_name}:
            scene_objects(world)[name] = deepcopy(scene_objects(exit_state)[name])
        world["robot"] = deepcopy(exit_state["robot"])
    return sequence, bridges


def build_episode(session, instance_id, order, trim_entry=False):
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

    # Add the assigned I recordings.
    for key, source in instance["intervents"].items():
        segments[key] = describe_intervent(session, key, source, instance_id)

    validate_segments(segments)
    assets = read_json(session.paths.session_dir / "assets.json")
    protected = set(assets["protected"])
    check_assets(session, segments, protected)

    # TODO：有问题，需要修改
    if trim_entry:
        # Trim only Key/I navigation prefixes; their original exits and all Query actions remain.
        for key, segment in segments.items():
            if segment["task"]["role"] == "query_task":
                continue
            source = segment["source"]
            terminal = None
            if "states" in segment:
                states = segment["states"]
                terminal = session.paths.resolve(states["directory"]) / states["exit"]
            with FrameReader(session.paths.resolve(source["hdf5"]), source["demo"], terminal=terminal) as reader:
                start, event = find_entry_frame(reader, segment["objects"]["used"])
                source["frames"][0] = start
                # Read the exit before seeking backwards to the selected entry.
                exit_state = read_json(terminal) if terminal is not None else reader.snapshot(source["frames"][1])
                entry = (
                    read_json(session.paths.resolve(states["directory"]) / states["entry"])
                    if start == 0 and terminal is not None else reader.snapshot(start)
                )
                segment["boundaries"] = {"entry": entry, "exit": exit_state}
                print(f"[Entry] {key}: event：{event}, entry：{start}", flush=True)

    # Initialize the robot from the first segment, then connect tasks with Bridges in the requested order.
    robot = segments[session.key_ids[0]]["interface"]["robot_name"]
    first_entry = load_segment_boundary(session, segments[order[0]], "entry", robot)
    scene = merge_scene_assets(scene, first_entry, (robot,))
    scene["robot"] = deepcopy(first_entry["robot"])

    sequence, _ = build_bridges(session, scene, order, segments, episode_id, instance_id)

    # Save source references and the task/Bridge sequence for later composition.
    recipe = {
        "session_id": session.session_id, "episode_id": episode_id, "instance_id": instance_id,
        "instance": str(tro_path.relative_to(session.paths.output_root)),
        "segments": [recipe_segment(segment) for segment in sequence],
    }
    write_json_atomic(output, recipe)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--instance-id", type=int, help="Override composition.instance_id")
    parser.add_argument("--seed", type=int, help="Override composition.order_seed")
    parser.add_argument("--demo-id", type=int, help="Override composition.demo_id")
    parser.add_argument("--trim-entry", action="store_true", help="Trim Key/I navigation prefixes")
    args = parser.parse_args()

    session = Session.load(args.config)
    if args.instance_id is not None:
        session.composition["instance_id"] = args.instance_id
    if args.seed is not None:
        session.composition["order_seed"] = args.seed
    if args.demo_id is not None:
        session.composition["demo_id"] = args.demo_id
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.omnigibson_data_root)
    order_seed = session.composition["order_seed"]
    rng = random.Random(order_seed)
    selected_instance = session.composition["instance_id"]
    failures = []
    report_path = session.paths.reports_dir / "build_episodes.json"

    for instance_id in range(1, session.instance_count + 1):
        order = rng.choice(session.composition["task_orders"])
        if selected_instance is not None and selected_instance != instance_id:
            continue
        output = session.paths.recipes_dir / f"episode_{instance_id - 1:08d}.json"
        if selected_instance is None and output.exists():
            print(f"[Build] instance {instance_id}: recipe already exists, skipping", flush=True)
            continue
        try:
            print(build_episode(session, instance_id, order, trim_entry=args.trim_entry))
        except Exception as error:
            if selected_instance is not None:
                raise
            failures.append({
                "instance_id": instance_id, "order": order,
                "error": f"{type(error).__name__}: {error}",
            })
            traceback.print_exc()
            write_json_atomic(report_path, {"failed": failures})
    if selected_instance is None:
        write_json_atomic(report_path, {"failed": failures})
        if failures:
            failed_ids = [item["instance_id"] for item in failures]
            raise RuntimeError(f"Episode batch finished with failed instances: {failed_ids}")


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
