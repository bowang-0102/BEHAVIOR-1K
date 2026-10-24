import argparse
import json

import h5py
import numpy as np

from memory_bench.episodes.replay_episodes import validate_annotations
from memory_bench.session import Session
from memory_bench.utils.scene_state import (
    get_state_tolerances,
    read_json,
    scene_objects,
    state_matches,
    write_json_atomic,
)


def _validate_scene_snapshot(snapshot, state, segment, inherited, robot_name, tolerance, context):
    objects = scene_objects(snapshot["scene"])
    if snapshot["bindings"] != segment["bindings"]:
        raise ValueError(f"{context} bindings mismatch: {segment['id']}")
    for name in inherited:
        if name not in objects or not state_matches(objects[name], scene_objects(state)[name], tolerance):
            raise ValueError(f"{context} inherited state mismatch: {segment['id']}/{name}")
    robot_state = scene_objects(state)[robot_name]
    expected_robot = {key: robot_state[key] for key in ("root_link", "joint_pos")}
    if not state_matches(objects[robot_name], expected_robot, tolerance):
        raise ValueError(f"{context} robot mismatch: {segment['id']}")


def readback(session, recipes, *, episodes=None):
    from lerobot.datasets import LeRobotDataset

    dataset_root = session.paths.export_dir
    dataset = LeRobotDataset(session.session_id, root=dataset_root, episodes=episodes)
    metadata = read_json(dataset_root / "meta" / "omnigibson.json")
    if not metadata["omnigibson_git_hash"]:
        raise ValueError("Export is missing the OmniGibson git hash")
    expected = sum(recipe["total_frames"] for recipe in recipes)
    if len(dataset) != expected or dataset.num_episodes != len(recipes):
        raise ValueError("LeRobot episode/frame counts do not match recipes")
    if not dataset.meta.video_keys:
        raise ValueError("Export contains no videos")
    cursor = 0
    video_fps_probes = []
    last_observations = []
    for recipe in recipes:
        episode_index = int(recipe["episode_id"].removeprefix("episode_"))
        validate_annotations(recipe)
        annotation = read_json(dataset_root / "annotations" / f"{recipe['episode_id']}.json")
        validate_annotations(annotation)
        if (
            annotation["total_frames"] != recipe["total_frames"]
            or annotation["memory_spans"] != recipe["memory_spans"]
            or [
                {key: value for key, value in item.items() if key != "start_snapshot"}
                for item in annotation["annotations"]
            ]
            != recipe["annotations"]
        ):
            raise ValueError("Exported annotations differ from the recipe")
        contract = read_json(dataset_root / "export_contracts" / f"{recipe['episode_id']}.json")
        if set(dataset.meta.video_keys) != set(contract["videos"]):
            raise ValueError("Export video keys differ from the live sensor contract")
        if dataset.meta.fps != recipe["fps"]:
            raise ValueError("Dataset metadata FPS differs from recipe")
        for key, video in contract["videos"].items():
            camera = key.split(".", 2)[2]
            intrinsics = np.asarray(metadata["cam_intrinsics"].get(camera, []), dtype=float)
            if (
                intrinsics.shape != (3, 3)
                or not np.isfinite(intrinsics).all()
                or intrinsics[0, 0] <= 0
                or intrinsics[1, 1] <= 0
                or not np.isclose(intrinsics[2, 2], 1)
            ):
                raise ValueError(f"Missing or invalid camera intrinsics: {camera}")
            feature = dataset.meta.features[key]
            if list(feature["shape"]) != video["shape"]:
                raise ValueError(f"Video metadata shape mismatch: {key}")
            video_fps = (feature.get("info") or {}).get("video.fps")
            if video_fps is None:
                from lerobot.datasets.video_utils import get_video_info

                video_path = dataset_root / dataset.meta.get_video_file_path(episode_index, key)
                video_fps = get_video_info(video_path).get("video.fps")
                video_fps_probes.append(
                    {"episode_index": episode_index, "key": key,
                     "path": str(video_path.relative_to(session.paths.output_root)), "fps": video_fps}
                )
            if video_fps != video["fps"]:
                raise ValueError(f"Video FPS mismatch: {key}, got {video_fps}, expected {video['fps']}")
        boundaries = {item["frames"][0]: item for item in annotation["annotations"]}
        inherited = set()
        tolerance = get_state_tolerances()
        tolerance["ignored_fields"] = [*tolerance["ignored_fields"], "lin_vel", "ang_vel", "joint_vel"]
        with h5py.File(session.paths.resolve(recipe["hdf5"]), "r") as stream:
            group = stream["data"]["demo_0"]
            for frame in range(recipe["total_frames"]):
                sample = dataset[cursor]
                if int(sample["episode_index"]) != episode_index:
                    raise ValueError("Episode indices are not aligned")
                if not np.allclose(sample["action"].numpy(), group["action"][frame], atol=1e-6, rtol=0):
                    raise ValueError(f"Action mismatch at exported frame {cursor}")
                if sample["task"] != group["instruction"].asstr()[frame]:
                    raise ValueError(f"Instruction mismatch at exported frame {cursor}")
                if abs(float(sample["timestamp"]) - frame / recipe["fps"]) > 1e-4:
                    raise ValueError(f"Timestamp mismatch at exported frame {cursor}")
                state = json.loads(group["state_json"][frame])
                robot_state = scene_objects(state)[contract["robot_name"]]
                proprio = sample["observation.state"].numpy()
                if proprio.shape != (contract["proprio_dim"],):
                    raise ValueError(f"Proprioception shape mismatch at exported frame {cursor}")
                for item in contract["proprio"]:
                    field = "joint_pos" if item["kind"] == "qpos" else "joint_vel"
                    expected_values = np.asarray(robot_state[field])[item["joints"]]
                    if "yaw_joint" in item:
                        yaw = robot_state["joint_pos"][item["yaw_joint"]]
                        c, s = np.cos(yaw), np.sin(yaw)
                        x, y, z = expected_values
                        expected_values = np.asarray([c * x + s * y, -s * x + c * y, z])
                    actual = proprio[slice(*item["slots"])]
                    if not np.allclose(actual, expected_values, atol=1e-5, rtol=0):
                        raise ValueError(f"Proprioception mismatch for {item['name']} at exported frame {cursor}")
                for key, video in contract["videos"].items():
                    height, width, channels = video["shape"]
                    if key not in sample or tuple(sample[key].shape) != (channels, height, width):
                        raise ValueError(f"Video frame shape/missing mismatch: {key}/{cursor}")
                if frame in boundaries:
                    segment = boundaries[frame]
                    inherited.update(segment["objects"]["write"])
                    snapshot = read_json(session.paths.resolve(segment["start_snapshot"]))
                    _validate_scene_snapshot(
                        snapshot, state, segment, inherited, contract["robot_name"], tolerance, "Start snapshot"
                    )
                if frame == recipe["total_frames"] - 1:
                    last_segment = annotation["annotations"][-1]
                    snapshot = read_json(session.paths.resolve(annotation["last_observation_snapshot"]))
                    if (
                        snapshot["frame"] != frame
                        or snapshot["segment_id"] != last_segment["id"]
                        or snapshot["state_convention"] != "pre_action"
                    ):
                        raise ValueError("Last observation snapshot must describe pre-action state[N-1]")
                    _validate_scene_snapshot(
                        snapshot,
                        state,
                        last_segment,
                        inherited,
                        contract["robot_name"],
                        tolerance,
                        "Last observation snapshot",
                    )
                    last_observations.append(
                        {
                            "episode_id": recipe["episode_id"],
                            "frame": frame,
                            "snapshot": annotation["last_observation_snapshot"],
                        }
                    )
                cursor += 1
                if cursor % 1000 == 0:
                    print(f"Read back {cursor}/{expected} frames", flush=True)
    report = {
        "episodes": len(recipes),
        "episode_ids": [recipe["episode_id"] for recipe in recipes],
        "frames": cursor,
        "videos": dataset.meta.video_keys,
        "video_fps_probes": video_fps_probes,
        "last_observation_snapshots": last_observations,
        "camera_intrinsics": sorted(metadata["cam_intrinsics"]),
        "omnigibson_git_hash": metadata["omnigibson_git_hash"],
        "readback": "passed",
    }
    write_json_atomic(session.paths.reports_dir / "export_readback.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument(
        "--episode-id", type=int, nargs="+", help="Zero-based episode IDs to validate; default: all episodes",
    )
    args = parser.parse_args()
    session = Session.load(args.config)
    instance_count = session.instance_count
    episode_ids = list(range(instance_count)) if args.episode_id is None else sorted(set(args.episode_id))
    if any(index < 0 or index >= instance_count for index in episode_ids):
        parser.error(f"--episode-id must be between 0 and {instance_count - 1}")
    recipes = [read_json(session.paths.recipes_dir / f"episode_{index:08d}.json") for index in episode_ids]
    print(json.dumps(readback(session, recipes, episodes=None if args.episode_id is None else episode_ids), indent=2))


if __name__ == "__main__":
    main()
