"""Pack completed replay shards into one LeRobot v3 dataset without re-encoding videos."""

import argparse
import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path

# Initialize LeRobot's Torch libraries before Arrow for the simulation environment's native runtime.
from lerobot.datasets.compute_stats import aggregate_stats

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from lerobot.configs.video import VIDEO_ENCODER_INFO_KEYS
from lerobot.datasets.feature_utils import features_equal_for_merge
from lerobot.datasets.io_utils import load_stats, write_stats, write_tasks
from lerobot.datasets.utils import DEFAULT_DATA_PATH, DEFAULT_EPISODES_PATH, DEFAULT_VIDEO_PATH

from memory_bench.tasks import TASKS
from memory_bench.utils.episode.recipe import validate_annotations


def _remap_stats(stats, frame_offset, episode_offset, task_index):
    """Translate index statistics, including quantiles, without resampling observations."""
    stats = deepcopy(stats)
    for key, offset in (("index", frame_offset), ("episode_index", episode_offset), ("task_index", task_index)):
        for statistic, values in stats[key].items():
            values = np.asarray(values)
            if statistic == "count":
                pass
            elif key == "task_index":
                values = np.full_like(values, 0 if statistic == "std" else task_index)
            elif statistic != "std":
                values = values + offset
            stats[key][statistic] = values
    return stats


def pack_dataset(source_root, dataset_root, *, overwrite=False, manifest=None):
    """Publish ``dataset_root`` from completed task shards in ``source_root``.

    Task indices and chunk numbers always follow mb000 through mb007, even when
    only a subset has been replayed. Existing incomplete shards are errors.
    ``manifest`` optionally names a JSON object mapping task names to episode IDs
    and absolute shard directories. Every referenced shard must be covered exactly.
    Shards are ordered by their sorted episode IDs within each task, retaining
    local episode order. Interleaved episode ranges therefore remain shard-contiguous.
    """
    source_root = Path(source_root)
    destination = Path(dataset_root)
    if source_root.resolve().is_relative_to(destination.resolve()) or destination.resolve().is_relative_to(
        (source_root / "lerobot_shards").resolve()
    ):
        raise ValueError(f"Dataset destination overlaps replay sources: {destination}")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite an existing dataset: {destination}")

    tasks = sorted((task for name, task in TASKS.items() if name.startswith("mb00")), key=lambda task: task.task_name)
    if len(tasks) != 8 or [task.task_name[:5] for task in tasks] != [f"mb{i:03d}" for i in range(8)]:
        raise ValueError("Packing requires the eight registered mb000 through mb007 tasks")

    assignments = None
    if manifest is not None:
        manifest = Path(manifest)
        if manifest.resolve().is_relative_to(destination.resolve()):
            raise ValueError(f"Dataset destination overlaps replay sources: {destination}")

        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"Duplicate manifest key: {key}")
                result[key] = value
            return result

        assignments = json.loads(manifest.read_text(), object_pairs_hook=unique_keys)
        if not isinstance(assignments, dict) or set(assignments) - {task.task_name for task in tasks}:
            raise ValueError("Manifest must map registered task names to episode assignments")
        for task_name, episodes in assignments.items():
            if not isinstance(episodes, dict) or not episodes:
                raise ValueError(f"Manifest requires episode assignments for {task_name}")
            for episode_id, root in episodes.items():
                if not episode_id or Path(episode_id).name != episode_id:
                    raise ValueError(f"Invalid source episode ID: {episode_id}")
                if not isinstance(root, str) or not Path(root).is_absolute():
                    raise ValueError(f"Manifest shard path must be absolute: {root}")

    shard_inputs = []
    for task_index, task in enumerate(tasks):
        if assignments is None:
            root = source_root / "lerobot_shards" / task.task_name
            if root.exists():
                shard_inputs.append((task_index, task, root, None))
            continue
        grouped = {}
        for episode_id, root in assignments.get(task.task_name, {}).items():
            grouped.setdefault(Path(root).resolve(), set()).add(episode_id)
        for root, episode_ids in sorted(grouped.items(), key=lambda item: sorted(item[1])):
            shard_inputs.append((task_index, task, root, episode_ids))
    for _, _, root, _ in shard_inputs:
        if root.resolve().is_relative_to(destination.resolve()) or destination.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Dataset destination overlaps replay sources: {destination}")

    sources = []
    for task_index, task, root, expected_ids in shard_inputs:
        marker = root / "complete.json"
        if not marker.is_file():
            raise ValueError(f"Incomplete replay shard (missing complete.json): {root}")
        completion = json.loads(marker.read_text())
        if completion["session_id"] != task.task_name:
            raise ValueError(f"Completion marker session differs from shard: {root}")
        info = json.loads((root / "meta/info.json").read_text())
        if info["codebase_version"] != "v3.0":
            raise ValueError(f"Expected LeRobot v3.0 shard: {root}")
        source_tasks = pd.read_parquet(root / "meta/tasks.parquet")
        if list(source_tasks.index) != [task.query_instruction] or source_tasks["task_index"].tolist() != [0]:
            raise ValueError(f"Shard must contain only its registered Query instruction: {root}")
        episode_ids = completion["episode_ids"]
        if not episode_ids or len(set(episode_ids)) != len(episode_ids) or len(episode_ids) != info["total_episodes"]:
            raise ValueError(f"Completion marker episode count differs from shard: {root}")
        if expected_ids is not None and set(episode_ids) != expected_ids:
            raise ValueError(f"Manifest episode coverage differs from completed shard: {root}")
        annotations = {}
        for episode_id in episode_ids:
            if Path(episode_id).name != episode_id:
                raise ValueError(f"Invalid source episode ID: {episode_id}")
            annotation = json.loads((root / "annotations" / f"{episode_id}.json").read_text())
            validate_annotations(annotation)
            if (annotation["session_id"], annotation["episode_id"], annotation["task"], annotation["fps"]) != (
                task.task_name,
                episode_id,
                task.query_instruction,
                info["fps"],
            ):
                raise ValueError(f"Annotation identity or Query instruction differs from shard: {episode_id}")
            index = annotation["episode_index"]
            if index in annotations:
                raise ValueError(f"Duplicate annotation episode index in {root}")
            annotations[index] = annotation
        if set(annotations) != set(range(info["total_episodes"])):
            raise ValueError(f"Annotation episode indices differ from shard: {root}")
        if {path.stem for path in (root / "annotations").glob("*.json")} != set(episode_ids):
            raise ValueError(f"Completion marker differs from annotation inventory: {root}")
        omni = json.loads((root / "meta/omnigibson.json").read_text())
        if not omni["cam_intrinsics"] or not omni["omnigibson_git_hash"]:
            raise ValueError(f"Missing camera intrinsics or simulator revision: {root}")
        if sources:
            first = sources[0]
            if (info["fps"], info["robot_type"]) != (first["info"]["fps"], first["info"]["robot_type"]):
                raise ValueError(f"Shard fps or robot_type differs: {root}")
            if not features_equal_for_merge(first["info"]["features"], info["features"]):
                raise ValueError(f"Shard feature schemas differ: {root}")
            if omni != first["omni"]:
                raise ValueError(f"Shard camera intrinsics or simulator revision differs: {root}")
        sources.append(dict(root=root, info=info, annotations=annotations, omni=omni, task_index=task_index))
    if not sources:
        raise ValueError(f"No completed registered replay shards in {source_root / 'lerobot_shards'}")

    info = deepcopy(sources[0]["info"])
    video_keys = [key for key, feature in info["features"].items() if feature["dtype"] == "video"]
    for key in video_keys:
        # Encoder settings may differ while the stored pixels and video schemas agree.
        for setting in VIDEO_ENCODER_INFO_KEYS:
            values = [source["info"]["features"][key]["info"].get(setting) for source in sources]
            if any(value != values[0] for value in values[1:]):
                info["features"][key]["info"][setting] = {} if setting == "video.extra_options" else None

    # The reader sorts filenames lexically. Widen every file number together
    # when flattening more than 1,000 files into a single MB task chunk.
    directories = ("data", "meta/episodes", *(f"videos/{key}" for key in video_keys))
    largest_chunk = max(
        sum(
            sum(1 for _ in (source["root"] / directory).glob("chunk-*/file-*"))
            for source in sources
            if source["task_index"] == task_index
        )
        for task_index in {source["task_index"] for source in sources}
        for directory in directories
    )
    file_width = max(3, len(str(largest_chunk - 1)))
    data_path, episodes_path, video_path = (
        pattern.replace("{file_index:03d}", f"{{file_index:0{file_width}d}}")
        for pattern in (DEFAULT_DATA_PATH, DEFAULT_EPISODES_PATH, DEFAULT_VIDEO_PATH)
    )

    frame_offset = episode_offset = 0
    source_stats = []
    data_schema = episode_schema = None
    file_counts = {}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".lerobot-pack-", dir=destination.parent) as temporary:
        build = Path(temporary) / "lerobot"
        (build / "meta").mkdir(parents=True)
        for source in sources:
            root, task_index = source["root"], source["task_index"]
            file_maps = {}
            for name, directory, pattern in (
                ("data", "data", data_path),
                ("meta/episodes", "meta/episodes", episodes_path),
                *((f"videos/{key}", f"videos/{key}", video_path) for key in video_keys),
            ):
                suffix = ".mp4" if name.startswith("videos/") else ".parquet"
                paths = sorted(
                    (root / directory).glob(f"chunk-*/file-*{suffix}"),
                    key=lambda path: (
                        int(path.parent.name.removeprefix("chunk-")),
                        int(path.stem.removeprefix("file-")),
                    ),
                )
                if not paths:
                    raise ValueError(f"Missing shard files: {root / directory}")
                first_file_index = file_counts.get((task_index, name), 0)
                file_counts[task_index, name] = first_file_index + len(paths)
                info["chunks_size"] = max(info["chunks_size"], first_file_index + len(paths))
                file_maps[name] = {}
                for file_index, path in enumerate(paths, start=first_file_index):
                    pair = (int(path.parent.name.removeprefix("chunk-")), int(path.stem.removeprefix("file-")))
                    relative = pattern.format(chunk_index=task_index, file_index=file_index, video_key=name[7:])
                    file_maps[name][pair] = (file_index, path, build / relative)

            seen_frames = 0
            for _, path, target in file_maps["data"].values():
                table = pq.read_table(path)
                if data_schema is None:
                    data_schema = table.schema
                elif not data_schema.equals(table.schema):
                    # Hugging Face cache fingerprints differ between valid shards.
                    # Compare fields here and retain each file's schema metadata below.
                    raise ValueError(f"Arrow data schemas differ: {path}")
                indices = table["index"].to_numpy()
                if not np.array_equal(indices, np.arange(seen_frames, seen_frames + len(table))):
                    raise ValueError(f"Shard frame indices are not consecutive: {path}")
                if not np.all(table["task_index"].to_numpy() == 0):
                    raise ValueError(f"Shard frame task indices differ from Query task: {path}")
                for key, offset in (("index", frame_offset), ("episode_index", episode_offset)):
                    column = table.schema.get_field_index(key)
                    values = pc.add(table[key], pa.scalar(offset, type=table[key].type))
                    table = table.set_column(column, table.schema.field(column), values)
                column = table.schema.get_field_index("task_index")
                table = table.set_column(
                    column,
                    table.schema.field(column),
                    pa.array([task_index] * len(table), type=table["task_index"].type),
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(table, target)
                seen_frames += len(table)
            if seen_frames != source["info"]["total_frames"]:
                raise ValueError(f"Shard frame count differs from metadata: {root}")

            for key in video_keys:
                for _, path, target in file_maps[f"videos/{key}"].values():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)

            seen_episodes = set()
            for meta_file_index, path, target in file_maps["meta/episodes"].values():
                table = pq.read_table(path)
                if episode_schema is None:
                    episode_schema = table.schema
                elif not episode_schema.equals(table.schema):
                    raise ValueError(f"Arrow episode schemas differ: {path}")
                rows = table.to_pylist()
                for row in rows:
                    local_index = row["episode_index"]
                    if local_index in seen_episodes or local_index not in source["annotations"]:
                        raise ValueError(f"Invalid shard episode index: {path}: {local_index}")
                    seen_episodes.add(local_index)
                    annotation = deepcopy(source["annotations"][local_index])
                    if row["length"] != annotation["total_frames"] or row["tasks"] != [
                        tasks[task_index].query_instruction
                    ]:
                        raise ValueError(f"Episode metadata differs from annotation: {path}: {local_index}")
                    for name in ("data", *(f"videos/{key}" for key in video_keys)):
                        pair = (row[f"{name}/chunk_index"], row[f"{name}/file_index"])
                        row[f"{name}/chunk_index"] = task_index
                        row[f"{name}/file_index"] = file_maps[name][pair][0]
                    row["meta/episodes/chunk_index"] = task_index
                    row["meta/episodes/file_index"] = meta_file_index
                    row["episode_index"] += episode_offset
                    row["dataset_from_index"] += frame_offset
                    row["dataset_to_index"] += frame_offset
                    episode_stats = {}
                    for column, values in row.items():
                        if column.startswith("stats/"):
                            _, key, statistic = column.split("/")
                            episode_stats.setdefault(key, {})[statistic] = values
                    remapped = _remap_stats(episode_stats, frame_offset, episode_offset, task_index)
                    for key, statistics in remapped.items():
                        for statistic, values in statistics.items():
                            row[f"stats/{key}/{statistic}"] = np.asarray(values).tolist()
                    annotation.update(episode_index=row["episode_index"], task_index=task_index)
                    annotation_path = (
                        build / "annotations" / f"task-{task_index:04d}" / f"episode_{row['episode_index']:08d}.json"
                    )
                    annotation_path.parent.mkdir(parents=True, exist_ok=True)
                    annotation_path.write_text(json.dumps(annotation, indent=2) + "\n")
                target.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), target)
            if seen_episodes != set(source["annotations"]):
                raise ValueError(f"Episode metadata count differs from completion marker: {root}")
            source_stats.append(_remap_stats(load_stats(root), frame_offset, episode_offset, task_index))
            frame_offset += source["info"]["total_frames"]
            episode_offset += source["info"]["total_episodes"]

        info.update(
            total_episodes=episode_offset,
            total_frames=frame_offset,
            total_tasks=8,
            splits={"train": f"0:{episode_offset}"},
            data_path=data_path,
            video_path=video_path if video_keys else None,
        )
        (build / "meta/info.json").write_text(json.dumps(info, indent=2) + "\n")
        (build / "meta/omnigibson.json").write_text(json.dumps(sources[0]["omni"], indent=2) + "\n")
        write_tasks(
            pd.DataFrame(
                {"task_index": range(8)}, index=pd.Index([task.query_instruction for task in tasks], name="task")
            ),
            build,
        )
        (build / "meta/tasks.jsonl").write_text(
            "".join(
                json.dumps({"task_index": index, "task_name": task.task_name, "task": task.query_instruction}) + "\n"
                for index, task in enumerate(tasks)
            )
        )
        write_stats(aggregate_stats(source_stats), build)

        # Build failures leave the current published dataset intact. Restore it if publication fails.
        previous = Path(temporary) / "previous"
        if destination.exists():
            if not overwrite:
                raise FileExistsError(f"Dataset appeared during packing: {destination}")
            destination.rename(previous)
        try:
            build.rename(destination)
        except OSError:
            if previous.exists():
                previous.rename(destination)
            raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path, help="Source root containing lerobot_shards/")
    parser.add_argument("dataset_root", type=Path, help="Final dataset directory (contains data/, meta/, videos/)")
    parser.add_argument("--manifest", type=Path, help="JSON task-to-episode mapping to absolute completed shard paths")
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace an existing packed dataset after building successfully"
    )
    args = parser.parse_args()
    print(pack_dataset(args.source_root, args.dataset_root, overwrite=args.overwrite, manifest=args.manifest))


if __name__ == "__main__":
    main()
