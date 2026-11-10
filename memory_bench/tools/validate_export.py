import argparse
import json

import h5py
import numpy as np

from memory_bench.session import Session
from memory_bench.utils.episode.recipe import episode_annotations, validate_annotations
from memory_bench.utils.scene.state import read_json, write_json_atomic


def readback(session, recipes, *, episodes=None):
    from lerobot.datasets import LeRobotDataset

    dataset_root = session.paths.export_dir
    dataset = LeRobotDataset(session.session_id, root=dataset_root, episodes=episodes)
    metadata = read_json(dataset_root / "meta" / "omnigibson.json")
    if not metadata["omnigibson_git_hash"]:
        raise ValueError("Export is missing the OmniGibson git hash")
    expected = sum(recipe["output"]["total_frames"] for recipe in recipes)
    if len(dataset) != expected or dataset.num_episodes != len(recipes):
        raise ValueError("LeRobot episode/frame counts do not match recipes")
    if not dataset.meta.video_keys:
        raise ValueError("Export contains no videos")
    video_keys = {key for key, feature in dataset.meta.features.items() if feature["dtype"] == "video"}
    if set(dataset.meta.video_keys) != video_keys:
        raise ValueError("Export video keys differ from dataset features")
    cursor = 0
    video_fps_probes = []
    for recipe in recipes:
        episode_index = int(recipe["episode_id"].removeprefix("episode_"))
        annotation = read_json(dataset_root / "annotations" / f"{recipe['episode_id']}.json")
        episode_index = annotation.get("episode_index", episode_index)
        validate_annotations(annotation)
        if annotation != episode_annotations(recipe, episode_index):
            raise ValueError("Exported annotations differ from the recipe")
        output = recipe["output"]
        if dataset.meta.fps != output["fps"]:
            raise ValueError("Dataset metadata FPS differs from recipe")
        for key in dataset.meta.video_keys:
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
            video_fps = (feature.get("info") or {}).get("video.fps")
            if video_fps is None:
                from lerobot.datasets.video_utils import get_video_info

                video_path = dataset_root / dataset.meta.get_video_file_path(episode_index, key)
                video_fps = get_video_info(video_path).get("video.fps")
                video_fps_probes.append(
                    {"episode_index": episode_index, "key": key,
                     "path": str(video_path.relative_to(session.paths.output_root)), "fps": video_fps}
                )
            if video_fps != output["fps"]:
                raise ValueError(f"Video FPS mismatch: {key}, got {video_fps}, expected {output['fps']}")
        with h5py.File(session.paths.resolve(output["hdf5"]), "r") as stream:
            group = stream["data"]["demo_0"]
            segment_index = 0
            for frame in range(output["total_frames"]):
                while frame >= annotation["segments"][segment_index]["frames"][1]:
                    segment_index += 1
                sample = dataset[cursor]
                if int(sample["episode_index"]) != episode_index:
                    raise ValueError("Episode indices are not aligned")
                if not np.allclose(sample["action"].numpy(), group["action"][frame], atol=1e-6, rtol=0):
                    raise ValueError(f"Action mismatch at exported frame {cursor}")
                if sample["task"] != group["instruction"].asstr()[frame]:
                    raise ValueError(f"Instruction mismatch at exported frame {cursor}")
                if sample["task"] != annotation["segments"][segment_index]["instruction"]:
                    raise ValueError(f"Instruction differs from segment at exported frame {cursor}")
                if abs(float(sample["timestamp"]) - frame / output["fps"]) > 1e-4:
                    raise ValueError(f"Timestamp mismatch at exported frame {cursor}")
                proprio = sample["observation.state"].numpy()
                if proprio.shape != tuple(dataset.meta.features["observation.state"]["shape"]):
                    raise ValueError(f"Proprioception shape mismatch at exported frame {cursor}")
                if not np.isfinite(proprio).all():
                    raise ValueError(f"Proprioception contains non-finite values at exported frame {cursor}")
                for key in dataset.meta.video_keys:
                    height, width, channels = dataset.meta.features[key]["shape"]
                    if key not in sample or tuple(sample[key].shape) != (channels, height, width):
                        raise ValueError(f"Video frame shape/missing mismatch: {key}/{cursor}")
                cursor += 1
                if cursor % 1000 == 0:
                    print(f"Read back {cursor}/{expected} frames", flush=True)
    report = {
        "episodes": len(recipes),
        "episode_ids": [recipe["episode_id"] for recipe in recipes],
        "frames": cursor,
        "videos": dataset.meta.video_keys,
        "video_fps_probes": video_fps_probes,
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
        "--episode-id", type=int, nargs="+",
        help="Zero-based source episode IDs to validate; default: all exported episodes",
    )
    args = parser.parse_args()
    session = Session.load(args.config)
    instance_count = session.instance_count
    if args.episode_id is None:
        episode_ids = sorted(
            int(path.stem.removeprefix("episode_"))
            for path in (session.paths.export_dir / "annotations").glob("episode_*.json")
        )
        if not episode_ids:
            parser.error("No exported episodes found")
    else:
        episode_ids = sorted(set(args.episode_id))
    if any(index < 0 or index >= instance_count for index in episode_ids):
        parser.error(f"--episode-id must be between 0 and {instance_count - 1}")
    recipes = [read_json(session.paths.recipes_dir / f"episode_{index:08d}.json") for index in episode_ids]
    episode_indices = [
        read_json(session.paths.export_dir / "annotations" / f"episode_{index:08d}.json").get("episode_index", index)
        for index in episode_ids
    ]
    print(json.dumps(readback(session, recipes, episodes=None if args.episode_id is None else episode_indices), indent=2))


if __name__ == "__main__":
    main()
