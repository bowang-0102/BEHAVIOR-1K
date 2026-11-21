import argparse
import json
import os
import random
import sys
import traceback
from copy import deepcopy

import h5py

from memory_bench.session import Session
from memory_bench.utils.episode.recipe import recipe_segment
from memory_bench.utils.recording.reader import compatible_interfaces
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
    protected = set(read_json(session.paths.session_dir / "assets.json")["protected"])
    target_objects = set(scene["objects_info"]["init_info"])
    completed_intervents = []

    for index, key in enumerate(order):
        segment = segments[key]
        entry = load_segment_boundary(session, segment, "entry", robot_name)
        if not index:
            require_objects_match(world, entry, segment["objects"]["read"], tolerance, f"{key} preconditions")
        else:
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
                bridge_scene = deepcopy(world)
                bridge = generate_bridge(
                    session, bridge_scene, bridge_scene, entry, output,
                    completed_intervents=completed_intervents, protected=protected, next_segment=segment,
                )
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
            bridge_writes = set(bridge["objects"]["write"])
            require_write_scope(bridge, bridge_writes, target_objects)
            if existing:
                try:
                    check_frame(bridge, exit_state, saved_entry, protected)
                except ValueError as error:
                    raise ValueError(f"Bridge changed nonrobot world: {error}") from error
                require_robot_match(exit_state, entry, robot_name, tolerance, "Bridge endpoint")
                require_objects_match(
                    exit_state, entry, segment["objects"]["read"], tolerance, f"{key} preconditions after Bridge",
                )
            # Apply the same writes that composition and replay will carry forward.
            for name in bridge_writes | {robot_name}:
                scene_objects(world)[name] = deepcopy(scene_objects(exit_state)[name])
            world["robot"] = deepcopy(exit_state["robot"])
            if existing:
                require_objects_match(
                    world, entry, segment["objects"]["read"], tolerance, f"{key} preconditions after Bridge",
                )
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
        if "bddl_goal" in segment:
            completed_intervents.append(segment["bddl_goal"])
    return sequence, bridges


def _collected_segments(session, instance, directory):
    demo_id = session.composition["demo_id"]
    if demo_id is None:
        manifests = sorted(
            (path for path in directory.glob("demo_*/manifest.json") if path.parent.name[5:].isdigit()),
            key=lambda path: (int(path.parent.name[5:]), path.parent.name),
        )
    else:
        manifests = [directory / f"demo_{demo_id:03d}" / "manifest.json"]

    rejected = False
    for path in manifests:
        try:
            if demo_id is None:
                record = read_json(path)
                if record["status"] != "complete":
                    continue
            record = load_collection_manifest(session, instance, path)
            segments = {}
            for key, item in record["segments"].items():
                states = item["states"]
                for boundary in ("entry", "exit"):
                    boundary_path = session.paths.resolve(states["directory"]) / states[boundary]
                    if not boundary_path.is_file():
                        raise FileNotFoundError(f"Collected {key} boundary is missing: {boundary_path}")
                segments[key] = describe_collected(session, key, instance, item)
            return validate_segments(segments)
        except (OSError, ValueError, KeyError, TypeError) as error:
            if demo_id is not None:
                raise
            rejected = True
            print(f"[Build] rejected {path}: {error}", file=sys.stderr, flush=True)
    if rejected:
        raise ValueError(f"No usable collection demo for instance {instance['instance_id']}")
    return None


def build_episode(session, instance_id, order):
    """Build an episode recipe and its physical Bridges."""
    episode_id = f"episode_{instance_id - 1:08d}"
    output = session.paths.recipes_dir / f"{episode_id}.json"
    if output.exists():
        raise FileExistsError(f"Recipe already exists: {output}")

    # Load the prepared instance and the selected K/Q collection run.
    directory = session.paths.recordings_dir / f"instance_{instance_id:04d}"
    if session.composition["demo_id"] is None and not directory.is_dir():
        return None
    tro_path = session.paths.session_dir / "tro" / f"instance_{instance_id:04d}.json"
    instance, scene = load_instance_scene(session, tro_path)

    segments = _collected_segments(session, instance, directory)
    if segments is None:
        return None

    # Add the assigned I recordings.
    for key, source in instance["intervents"].items():
        segments[key] = describe_intervent(session, key, source, instance_id)

    validate_segments(segments)
    assets = read_json(session.paths.session_dir / "assets.json")
    protected = set(assets["protected"])
    check_assets(session, segments, protected)

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
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--instance-id", type=int, help="Override composition.instance_id")
    selection.add_argument("--instance-range", metavar="FIRST-LAST", help="Select inclusive instance IDs")
    parser.add_argument("--seed", type=int, help="Override composition.order_seed")
    parser.add_argument("--demo-id", type=int, help="Require this exact demo instead of scanning the range")
    args = parser.parse_args()

    session = Session.load(args.config)
    count = session.instance_count
    first, last = 1, count
    selected_instance = args.instance_id if args.instance_id is not None else session.composition["instance_id"]
    if args.instance_range is not None:
        try:
            first, last = map(int, args.instance_range.split("-"))
        except ValueError:
            parser.error("--instance-range expects FIRST-LAST, for example 1-50")
        if not 1 <= first <= last <= count:
            parser.error(f"--instance-range must be within 1-{count} in ascending order")
    if args.seed is not None:
        session.composition["order_seed"] = args.seed
    if args.instance_range is not None or args.demo_id is not None:
        session.composition["demo_id"] = args.demo_id
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.omnigibson_data_root)
    order_seed = session.composition["order_seed"]
    rng = random.Random(order_seed)
    single_instance = args.instance_range is None and selected_instance is not None
    failures = []
    report_path = session.paths.reports_dir / "build_episodes.json"

    # Consume earlier draws so a range uses the same task orders as the full session.
    for instance_id in range(1, last + 1):
        order = rng.choice(session.composition["task_orders"])
        if instance_id < first or (single_instance and instance_id != selected_instance):
            continue
        output = session.paths.recipes_dir / f"episode_{instance_id - 1:08d}.json"
        if not single_instance and output.exists():
            print(f"[Build] instance {instance_id}: recipe already exists, skipping", flush=True)
            continue
        try:
            result = build_episode(session, instance_id, order)
            if result is None:
                print(f"[Build] instance {instance_id}: no complete collection demo, skipping", flush=True)
            else:
                print(result, flush=True)
        except Exception as error:
            if single_instance:
                raise
            failures.append({
                "instance_id": instance_id, "order": order,
                "error": f"{type(error).__name__}: {error}",
            })
            traceback.print_exc()
            write_json_atomic(report_path, {"failed": failures})
    if not single_instance:
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
