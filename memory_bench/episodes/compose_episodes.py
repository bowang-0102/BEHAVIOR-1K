"""Step 4: decode and stitch raw K/I/Q/B recordings into a replayable world."""

import argparse
import sys
from copy import deepcopy
import json
import os

import h5py

from memory_bench.session import Session
from memory_bench.utils.instance_scene import load_instance_scene
from memory_bench.utils.recordings import FrameReader
from memory_bench.utils.scene_state import (
    get_state_tolerances, merge_scene_assets, read_json,
    require_objects_match, require_robot_match, scene_objects, write_json, write_json_atomic,
)
from memory_bench.utils.segment_manifest import (
    boundary, compatible_interfaces, load_segments, mapped_snapshot,
)
from memory_bench.utils.task_segments import check_frame


def compose_trajectory(session, scene, assembled, protected, episode_id, instance):
    """Decode raw tasks and bridges into one checked cumulative-world episode."""
    interface = {**assembled[0]["interface"], "robot_name": scene["robot"]["name"]}
    robot_name = interface["robot_name"]
    tolerance = get_state_tolerances()
    episode_dir = session.paths.episodes_dir
    episode_dir.mkdir(parents=True, exist_ok=True)
    episode_path = episode_dir / f"{episode_id}.hdf5"
    scene_path = episode_dir / f"{episode_id}_scene.json"
    query_path = episode_dir / f"{episode_id}_query_start.json"
    for path in (episode_path, scene_path, query_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")
    annotations = []
    current = deepcopy(scene["state"])
    previous_exit = scene
    query_snapshot = None
    query_frame = None
    total = sum(item["source"]["frames"][1] - item["source"]["frames"][0] for item in assembled)
    temporary = episode_path.with_suffix(".hdf5.tmp")
    with h5py.File(temporary, "w") as stream:
        data = stream.create_group("data")
        first_source = assembled[0]["source"]
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
            terminal = None
            if "states" in item:
                terminal = session.paths.resolve(item["states"]["directory"]) / item["states"]["exit"]
            source_path = session.paths.resolve(source["hdf5"])
            reader = FrameReader(source_path, source["demo"], terminal=terminal)
            start, end = source["frames"]
            names = [*item["objects"]["write"], robot_name]
            before = cursor
            task_bindings = {
                key: robot_name if key.startswith("agent.n.") else name
                for key, name in instance["bindings"].get(item["identity"]["id"], {}).items()
            }
            try:
                if not compatible_interfaces(item["interface"], reader.metadata["interface"]):
                    raise ValueError(f"Raw source interface differs from recipe: {item['identity']['id']}")
                key = item["identity"]["id"]
                if key in [*session.key_ids, session.query_id]:
                    source_data = reader.stream["data"]
                    if not source_data.attrs.get("collection_complete", False):
                        raise ValueError(f"Incomplete collected source: {key}")
                    for name, expected in (
                        ("session_id", session.session_id), ("instance_id", instance["instance_id"]),
                        ("variant_id", instance["variant_id"]), ("run_id", source_path.parent.name),
                    ):
                        if source_data.attrs[name] != expected:
                            raise ValueError(f"Collected source {name} differs from its demo: {key}")
                    if source_data.attrs["segment_id"] != key:
                        raise ValueError(f"Collected source segment_id differs from its demo: {key}")
                    if source["frames"] != [0, len(reader.group["action"])]:
                        raise ValueError(f"Collected source must use its entire local action range: {key}")
                native_robot = item["interface"]["robot_name"]
                native_entry = reader.snapshot(start)
                entry = mapped_snapshot(native_entry, native_robot, robot_name)
                if "states" in item:
                    expected = boundary(session, item, "entry", robot_name)
                    require_objects_match(
                        entry, expected, set(item["objects"]["used"]) | {robot_name}, tolerance, "Raw entry"
                    )
                read_objects = (
                    set(scene_objects(current)) - {robot_name}
                    if item["task"]["role"] == "bridge_task" else item["objects"]["read"]
                )
                require_objects_match(current, entry, read_objects, tolerance, f"{item['identity']['id']} preconditions")
                require_robot_match(previous_exit, entry, robot_name, tolerance, f"{item['identity']['id']} handoff")
                for frame in range(start, end + 1):
                    native_state = reader.state(frame)
                    check_frame(item, native_state, native_entry, protected)
                    incoming = mapped_snapshot(native_state, native_robot, robot_name)
                    if frame == end and "states" in item:
                        expected = boundary(session, item, "exit", robot_name)
                        require_objects_match(
                            incoming, expected, set(item["objects"]["used"]) | {robot_name}, tolerance, "Raw exit"
                        )
                    for name in names:
                        scene_objects(current)[name] = deepcopy(scene_objects(incoming)[name])
                    if frame == end:
                        continue
                    if item["task"]["role"] == "query_task" and frame == start:
                        query_snapshot = deepcopy(scene)
                        query_snapshot["state"] = deepcopy(current)
                        query_snapshot["robot"] = deepcopy(entry["robot"])
                        query_snapshot["metadata"]["task"]["inst_to_name"] = deepcopy(task_bindings)
                        query_snapshot["metadata"]["task"].pop("robot_poses", None)
                        query_snapshot["metadata"]["task"]["activity_name"] = item["task"]["activity"]
                        query_frame = cursor
                    states[cursor] = json.dumps(current, separators=(",", ":"))
                    actions[cursor] = reader.group["action"][frame]
                    instructions[cursor] = item["task"]["instruction"]
                    for field in ("reward", "terminated", "truncated"):
                        group[field][cursor] = reader.group[field][frame]
                    cursor += 1
                previous_exit = mapped_snapshot(reader.snapshot(end), native_robot, robot_name)
            finally:
                reader.close()
            annotations.append(
                {
                    "id": item["identity"]["id"],
                    **item["task"],
                    "frames": [before, cursor],
                    "source": deepcopy(item["source"]),
                    "objects": item["objects"],
                    "bindings": task_bindings,
                }
            )
        states[total] = json.dumps(current, separators=(",", ":"))
    write_json(scene_path, scene)
    if query_snapshot is not None:
        write_json(query_path, query_snapshot)
    temporary.replace(episode_path)
    return {
        "session_id": session.session_id,
        "episode_id": episode_id,
        "task_order": [item["identity"]["id"] for item in assembled if item["task"]["role"] != "bridge_task"],
        "replay_scene": str(scene_path.relative_to(session.paths.output_root)),
        "hdf5": str(episode_path.relative_to(session.paths.output_root)),
        "query_start": str(query_path.relative_to(session.paths.output_root)) if query_snapshot is not None else None,
        "query_start_frame": query_frame,
        "annotations": annotations,
        "memory_spans": [],
        "total_frames": total,
        "fps": interface["fps"],
        "interface": interface,
    }


def compose_episode(session, recipe):
    """Step 4 consumes a complete recipe; it never creates bridges or intermediate recordings."""
    episode_id = recipe["episode_id"]
    tro_path = session.paths.resolve(recipe["instance"])
    instance, scene = load_instance_scene(session, tro_path)
    if (
        recipe["session_id"] != session.session_id
        or recipe["instance_id"] != instance["instance_id"]
        or episode_id != f"episode_{instance['instance_id'] - 1:08d}"
    ):
        raise ValueError("Recipe identity differs from its instance")
    if recipe["task_order"] not in session.composition["task_orders"]:
        raise ValueError("Recipe order was not declared in the session")
    sequence = []
    for index, key in enumerate(recipe["task_order"]):
        if index:
            sequence.append(f"bridge_{index - 1:02d}")
        sequence.append(key)
    if recipe["sequence"] != sequence or set(recipe["segments"]) != set(session.segments):
        raise ValueError("Recipe sequence must contain each task and every adjacent Bridge in order")
    collection_dir = session.paths.resolve(recipe["segments"][session.key_ids[0]]["source"]["hdf5"]).parent.resolve()
    instance_dir = session.paths.recordings_dir / f"instance_{instance['instance_id']:04d}"
    if collection_dir.parent != instance_dir.resolve():
        raise ValueError("Recipe K/Q sources are outside the instance's recording directory")
    for key in [*session.key_ids, session.query_id]:
        segment = recipe["segments"][key]
        source = segment["source"]
        if session.paths.resolve(source["hdf5"]).parent.resolve() != collection_dir or source["demo"] != 0:
            raise ValueError(f"Recipe mixes sources outside the selected collection demo: {key}")
        if session.paths.resolve(segment["states"]["directory"]).resolve() != collection_dir:
            raise ValueError(f"Collected boundaries must belong to the selected demo: {key}")
    segments = load_segments({**recipe["segments"], **recipe["bridges"]})
    for key, source in instance["intervents"].items():
        actual = segments[key]["source"]
        if any(actual[name] != source[name] for name in ("hdf5", "demo")):
            raise ValueError(f"I source differs from the instance's fixed allocation: {key}")
    assembled = [segments[key] for key in recipe["sequence"]]
    first = assembled[0]
    robot = segments[session.key_ids[0]]["interface"]["robot_name"]
    entry = boundary(session, first, "entry", robot)
    scene = merge_scene_assets(scene, entry, (robot,))
    scene["robot"] = deepcopy(entry["robot"])
    assets = read_json(session.paths.session_dir / "assets.json")
    result = compose_trajectory(
        session, scene, assembled, set(assets["protected"]), episode_id, instance,
    )
    by_id = {item["id"]: item for item in result["annotations"]}
    result.update(
        instance_id=recipe["instance_id"],
        benchmark=session.benchmark, variant_id=instance["variant_id"],
        memory_spans=[{
            "key_id": key, "query_id": session.query_id, "key_end_frame": by_id[key]["frames"][1],
            "query_start_frame": by_id[session.query_id]["frames"][0],
            "distance_seconds": (by_id[session.query_id]["frames"][0] - by_id[key]["frames"][1]) / result["fps"],
        } for key in session.key_ids],
    )
    output = session.paths.recipes_dir / f"{episode_id}.json"
    write_json_atomic(output, {**recipe, **result})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--instance-id", type=int, help="Override composition.instance_id")
    args = parser.parse_args()
    
    session = Session.load(args.config)
    if args.instance_id is not None:
        session.composition["instance_id"] = args.instance_id
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)
    for instance_id in range(1, session.instance_count + 1):
        if session.composition["instance_id"] is None or instance_id == session.composition["instance_id"]:
            path = session.paths.recipes_dir / f"episode_{instance_id - 1:08d}.json"
            print(compose_episode(session, read_json(path)))


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
