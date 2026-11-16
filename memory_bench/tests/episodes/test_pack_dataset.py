"""Exercise packing with real LeRobot parquet, MP4, and dataset readback."""

import json
import shutil
from copy import deepcopy

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from datasets import config as datasets_config
from lerobot.configs.video import VideoEncoderConfig
from lerobot.datasets import LeRobotDataset
from lerobot.datasets.utils import DEFAULT_VIDEO_PATH

from memory_bench.episodes.pack_dataset import pack_dataset
from memory_bench.tasks import TASKS

TASK_NAMES = sorted(TASKS)
CAMERA = "observation.images.head"


@pytest.fixture(autouse=True)
def local_dataset_cache(tmp_path, monkeypatch):
    cache = tmp_path / "huggingface_cache"
    monkeypatch.setenv("HF_DATASETS_CACHE", str(cache))
    monkeypatch.setattr(datasets_config, "HF_DATASETS_CACHE", cache)


@pytest.fixture(scope="module")
def real_shards(tmp_path_factory):
    output = tmp_path_factory.mktemp("real_lerobot_shards")
    for task_index in (0, 2):
        task = TASKS[TASK_NAMES[task_index]]
        root = output / "lerobot_shards" / task.task_name
        dataset = LeRobotDataset.create(
            repo_id=f"memory-bench/{task.task_name}",
            root=root,
            fps=10,
            robot_type="test_robot",
            features={
                "action": {"dtype": "float32", "shape": (2,), "names": ["a", "b"]},
                "observation.state": {"dtype": "float32", "shape": (2,), "names": ["a", "b"]},
                CAMERA: {"dtype": "video", "shape": (32, 32, 3), "names": ["height", "width", "channels"]},
            },
            camera_encoder=VideoEncoderConfig(vcodec="h264", preset="ultrafast", crf=0),
            video_backend="pyav",
            encoder_threads=1,
            metadata_buffer_size=1,
        )
        for episode_index in range(2):
            for frame in range(4):
                dataset.add_frame(
                    {
                        "action": np.array([task_index, frame], dtype=np.float32),
                        "observation.state": np.array([episode_index, frame + 0.25], dtype=np.float32),
                        CAMERA: np.full((32, 32, 3), task_index * 40 + episode_index * 15 + frame, dtype=np.uint8),
                        "task": task.query_instruction,
                    }
                )
            dataset.save_episode()
        dataset.finalize()
        (root / "meta/omnigibson.json").write_text(
            json.dumps(
                {
                    "cam_intrinsics": {"head": [[10, 0, 16], [0, 10, 16], [0, 0, 1]]},
                    "omnigibson_git_hash": "test-revision",
                }
            )
        )
        episode_ids = ["episode_00000004", "episode_00000008"]
        (root / "annotations").mkdir()
        for index, episode_id in enumerate(episode_ids):
            (root / "annotations" / f"{episode_id}.json").write_text(
                json.dumps(
                    {
                        "session_id": task.task_name,
                        "episode_id": episode_id,
                        "instance_id": index + 5,
                        "episode_index": index,
                        "task": task.query_instruction,
                        "fps": 10,
                        "total_frames": 4,
                        "segments": [
                            {"id": "key", "role": "key_task", "instruction": "Remember this.", "frames": [0, 2]},
                            {
                                "id": "query",
                                "role": "query_task",
                                "instruction": task.query_instruction,
                                "frames": [2, 4],
                            },
                        ],
                        "memory_spans": [
                            {"key_id": "key", "query_id": "query", "key_end_frame": 2, "query_start_frame": 2}
                        ],
                    }
                )
            )
        (root / "complete.json").write_text(json.dumps({"session_id": task.task_name, "episode_ids": episode_ids}))
    return output


@pytest.fixture
def shards(tmp_path, real_shards):
    shutil.copytree(real_shards / "lerobot_shards", tmp_path / "lerobot_shards")
    return tmp_path


def test_pack_fixed_tasks_schema_annotations_and_real_video_readback(shards):
    root = pack_dataset(shards)
    tasks = pd.read_parquet(root / "meta/tasks.parquet")
    assert tasks.index.tolist() == [TASKS[name].query_instruction for name in TASK_NAMES]
    assert tasks.task_index.tolist() == list(range(8))
    task_rows = [json.loads(line) for line in (root / "meta/tasks.jsonl").read_text().splitlines()]
    assert task_rows == [
        {"task_index": index, "task_name": name, "task": TASKS[name].query_instruction}
        for index, name in enumerate(TASK_NAMES)
    ]
    info = json.loads((root / "meta/info.json").read_text())
    assert (info["total_episodes"], info["total_frames"], info["total_tasks"]) == (4, 16, 8)
    assert sorted(path.name for path in (root / "data").iterdir()) == ["chunk-000", "chunk-002"]
    packed = LeRobotDataset("memory-bench/packed", root=root, video_backend="pyav")
    assert len(packed) == 16
    for task_index, episode_offset, frame_offset in ((0, 0, 0), (2, 2, 8)):
        source = shards / "lerobot_shards" / TASK_NAMES[task_index]
        original = LeRobotDataset(f"memory-bench/{TASK_NAMES[task_index]}", root=source, video_backend="pyav")
        source_table = pq.read_table(source / "data/chunk-000/file-000.parquet")
        packed_table = pq.read_table(root / f"data/chunk-{task_index:03d}/file-000.parquet")
        assert packed_table.schema.equals(source_table.schema, check_metadata=True)
        for frame in range(8):
            actual, expected = packed[frame_offset + frame], original[frame]
            assert actual["task"] == TASKS[TASK_NAMES[task_index]].query_instruction
            assert int(actual["task_index"]) == task_index
            assert int(actual["episode_index"]) == int(expected["episode_index"]) + episode_offset
            assert int(actual["index"]) == frame_offset + frame
            for key in ("action", "observation.state", "timestamp", "frame_index", CAMERA):
                np.testing.assert_array_equal(actual[key], expected[key])
        source_video = source / DEFAULT_VIDEO_PATH.format(video_key=CAMERA, chunk_index=0, file_index=0)
        packed_video = root / DEFAULT_VIDEO_PATH.format(video_key=CAMERA, chunk_index=task_index, file_index=0)
        assert source_video.read_bytes() == packed_video.read_bytes()
        assert source_video.stat().st_ino != packed_video.stat().st_ino
        for local_index, source_id in enumerate(("episode_00000004", "episode_00000008")):
            annotation = json.loads(
                (
                    root / "annotations" / f"task-{task_index:04d}" / f"episode_{episode_offset + local_index:08d}.json"
                ).read_text()
            )
            original_annotation = json.loads((source / "annotations" / f"{source_id}.json").read_text())
            assert annotation == {
                **original_annotation,
                "episode_index": episode_offset + local_index,
                "task_index": task_index,
            }
    stats = json.loads((root / "meta/stats.json").read_text())
    assert stats["index"]["min"] == [0]
    assert stats["index"]["max"] == [15]
    assert stats["index"]["mean"] == [7.5]
    assert stats["episode_index"]["max"] == [3]
    assert stats["task_index"]["max"] == [2]
    assert stats["task_index"]["mean"] == [1]
    rows = pq.read_table(root / "meta/episodes/chunk-002/file-000.parquet").to_pylist()
    assert rows[0]["stats/index/min"] == [8]
    assert rows[1]["stats/episode_index/mean"] == [3]
    assert rows[0]["stats/task_index/mean"] == [2]


def test_flatten_multiple_source_chunks_independently(shards):
    source = shards / "lerobot_shards" / TASK_NAMES[2]
    # Split actual source data and metadata files across chunks. Keep the packed
    # video in a different source chunk to catch accidental shared file maps.
    data_path = source / "data/chunk-000/file-000.parquet"
    data = pq.read_table(data_path)
    data_path.unlink()
    for index in range(2):
        path = source / f"data/chunk-{index:03d}/file-000.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(data.slice(index * 4, 4), path)
    video_path = source / DEFAULT_VIDEO_PATH.format(video_key=CAMERA, chunk_index=0, file_index=0)
    moved_video = source / DEFAULT_VIDEO_PATH.format(video_key=CAMERA, chunk_index=3, file_index=5)
    moved_video.parent.mkdir(parents=True)
    video_path.rename(moved_video)
    meta_paths = sorted((source / "meta/episodes").glob("*/*.parquet"))
    metadata = pa.concat_tables([pq.read_table(path) for path in meta_paths])
    for path in meta_paths:
        path.unlink()
    original_rows = metadata.to_pylist()
    for index, row in enumerate(deepcopy(original_rows)):
        row["data/chunk_index"] = index
        row["data/file_index"] = 0
        row["meta/episodes/chunk_index"] = index + 4
        row["meta/episodes/file_index"] = 7
        row[f"videos/{CAMERA}/chunk_index"] = 3
        row[f"videos/{CAMERA}/file_index"] = 5
        path = source / f"meta/episodes/chunk-{index + 4:03d}/file-007.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist([row], schema=metadata.schema), path)

    root = pack_dataset(shards)
    dataset = LeRobotDataset("memory-bench/packed", root=root, video_backend="pyav")
    assert int(dataset[12]["episode_index"]) == 3
    for index in range(2):
        row = pq.read_table(root / f"meta/episodes/chunk-002/file-{index:03d}.parquet").to_pylist()[0]
        assert (row["data/chunk_index"], row["data/file_index"]) == (2, index)
        assert (row["meta/episodes/chunk_index"], row["meta/episodes/file_index"]) == (2, index)
        assert (row[f"videos/{CAMERA}/chunk_index"], row[f"videos/{CAMERA}/file_index"]) == (2, 0)
        assert row[f"videos/{CAMERA}/from_timestamp"] == original_rows[index][f"videos/{CAMERA}/from_timestamp"]
        assert row[f"videos/{CAMERA}/to_timestamp"] == original_rows[index][f"videos/{CAMERA}/to_timestamp"]


def test_subset_keeps_all_eight_task_indices(shards):
    shutil.rmtree(shards / "lerobot_shards" / TASK_NAMES[0])
    root = pack_dataset(shards)
    dataset = LeRobotDataset("memory-bench/subset", root=root, video_backend="pyav")
    assert dataset.meta.total_tasks == 8
    assert int(dataset[0]["task_index"]) == 2
    assert (root / "data/chunk-002").is_dir()
    assert (root / "annotations/task-0002/episode_00000000.json").is_file()


def test_large_task_chunk_uses_sortable_file_numbers_and_info_paths(shards):
    source = shards / "lerobot_shards" / TASK_NAMES[0]
    video = source / DEFAULT_VIDEO_PATH.format(video_key=CAMERA, chunk_index=0, file_index=0)
    for index in range(1, 1001):
        chunk, file_index = divmod(index, 1000)
        path = source / DEFAULT_VIDEO_PATH.format(video_key=CAMERA, chunk_index=chunk, file_index=file_index)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, path)

    root = pack_dataset(shards)

    info = json.loads((root / "meta/info.json").read_text())
    assert "{file_index:04d}" in info["data_path"]
    assert "{file_index:04d}" in info["video_path"]
    assert info["chunks_size"] >= 1001
    assert sorted(path.name for path in (root / "videos" / CAMERA / "chunk-000").iterdir()) == [
        f"file-{index:04d}.mp4" for index in range(1001)
    ]
    dataset = LeRobotDataset("memory-bench/large-chunk", root=root, video_backend="pyav")
    assert len(dataset) == 16
    assert int(dataset[0]["task_index"]) == 0
    assert int(dataset[15]["task_index"]) == 2


@pytest.mark.parametrize(
    "defect", ["incomplete", "wrong_query", "wrong_annotation", "intrinsics", "revision", "fps", "shape", "completion"]
)
def test_rejects_incomplete_or_incompatible_shards(shards, defect):
    source = shards / "lerobot_shards" / TASK_NAMES[2]
    if defect == "incomplete":
        (source / "complete.json").unlink()
    elif defect == "wrong_query":
        frame = pd.read_parquet(source / "meta/tasks.parquet")
        frame.index = pd.Index(["Wrong Query"], name="task")
        frame.to_parquet(source / "meta/tasks.parquet")
    else:
        path = source / "meta/info.json"
        if defect in ("intrinsics", "revision"):
            path = source / "meta/omnigibson.json"
        elif defect == "wrong_annotation":
            path = source / "annotations/episode_00000004.json"
        elif defect == "completion":
            path = source / "complete.json"
        value = json.loads(path.read_text())
        if defect == "intrinsics":
            value["cam_intrinsics"]["head"][0][0] = 20
        elif defect == "revision":
            value["omnigibson_git_hash"] = "different-revision"
        elif defect == "fps":
            value["fps"] = 20
        elif defect == "shape":
            value["features"][CAMERA]["shape"] = [16, 32, 3]
        elif defect == "wrong_annotation":
            value["task"] = "Wrong Query"
        else:
            value["episode_ids"].pop()
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        pack_dataset(shards)
    assert not (shards / "lerobot").exists()
    assert not list(shards.glob(".lerobot-pack-*"))


def test_overwrite_builds_before_replacing_and_cleans_temporary_files(shards):
    root = pack_dataset(shards)
    sentinel = root / "old_dataset_marker"
    sentinel.write_text("keep until replacement succeeds")
    with pytest.raises(FileExistsError):
        pack_dataset(shards)
    source_data = shards / "lerobot_shards" / TASK_NAMES[2] / "data/chunk-000/file-000.parquet"
    original_bytes = source_data.read_bytes()
    source_data.write_bytes(b"broken parquet")
    with pytest.raises(pa.ArrowInvalid):
        pack_dataset(shards, overwrite=True)
    assert sentinel.read_text() == "keep until replacement succeeds"
    assert not list(shards.glob(".lerobot-pack-*"))
    source_data.write_bytes(original_bytes)
    assert pack_dataset(shards, overwrite=True) == root
    assert not sentinel.exists()
    assert not list(shards.glob(".lerobot-pack-*"))


def test_no_shards_does_not_publish(tmp_path):
    with pytest.raises(ValueError, match="No completed"):
        pack_dataset(tmp_path)
    assert not (tmp_path / "lerobot").exists()
