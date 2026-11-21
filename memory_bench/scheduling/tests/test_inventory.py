import json
import shutil
from pathlib import Path

import pytest
import yaml

from memory_bench.scheduling.inventory import inventory
from memory_bench.tasks import TASKS
from memory_bench.utils.scene.state import write_json

TASK_NAMES = sorted(TASKS)


@pytest.fixture(scope="module")
def published_shards(tmp_path_factory):
    root = tmp_path_factory.mktemp("inventory-shards")
    task = TASKS[TASK_NAMES[0]]
    shard = root / "lerobot_shards" / task.task_name
    video_key = "observation.images.head"
    write_json(shard / "meta/info.json", {
        "codebase_version": "v3.0", "total_episodes": 2, "total_frames": 8, "fps": 10,
        "features": {"action": {"dtype": "float32"}, "observation.state": {"dtype": "float32"},
                     video_key: {"dtype": "video"}},
    })
    (shard / "meta/tasks.parquet").write_bytes(b"test parquet")
    write_json(shard / "meta/omnigibson.json", {"cam_intrinsics": {"head": [[1]]}, "omnigibson_git_hash": "test"})
    ids = ["episode_00000004", "episode_00000008"]
    write_json(shard / "complete.json", {"session_id": task.task_name, "episode_ids": ids})
    for index, name in enumerate(ids):
        write_json(shard / "annotations" / f"{name}.json", {
            "session_id": task.task_name, "episode_id": name, "instance_id": int(name[8:]) + 1,
            "episode_index": index, "task": task.query_instruction, "fps": 10, "total_frames": 4,
            "segments": [{"id": "key", "role": "key_task", "frames": [0, 2]},
                         {"id": "query", "role": "query_task", "frames": [2, 4]}],
            "memory_spans": [{"key_id": "key", "query_id": "query", "key_end_frame": 2, "query_start_frame": 2}],
        })
        video = shard / "videos" / video_key / "chunk-000" / f"file-{index:03d}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"test video")
    for directory in ("data", "meta/episodes"):
        path = shard / directory / "chunk-000" / "file-000.parquet"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"test parquet")
    return root





@pytest.fixture
def prepared(tmp_path, published_shards):
    task = TASK_NAMES[0]
    root = tmp_path / "canonical"
    directory = root / "sessions" / task
    directory.mkdir(parents=True)
    config = yaml.safe_load((Path("memory_bench/configs") / f"{task}.yaml").read_text())
    config["paths"]["output_root"] = str(tmp_path / "old-root")
    path = directory / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    (directory / "variant_assignments.json").write_text(json.dumps({
        "session_id": task, "instances": [{"instance_id": index} for index in range(1, 10)],
    }))
    shard = root / "parallel" / "worker0" / "lerobot_shards" / task
    shutil.copytree(published_shards / "lerobot_shards" / task, shard)
    annotation_path = shard / "annotations" / "episode_00000008.json"
    annotation = json.loads(annotation_path.read_text())
    annotation["instance_id"] = 9
    annotation_path.write_text(json.dumps(annotation))
    return path, root, task, shard


def native_shard(root, task, legacy):
    """Build publication metadata for a single independently replayed episode."""
    shard = root / "scheduling" / "shards" / task / "episode_00000004"
    shutil.copytree(legacy, shard)
    (shard / "annotations" / "episode_00000008.json").unlink()
    (shard / "complete.json").write_text(json.dumps({"session_id": task, "episode_ids": [shard.name]}))
    info_path = shard / "meta/info.json"
    info = json.loads(info_path.read_text())
    info.update(total_episodes=1, total_frames=4)
    info_path.write_text(json.dumps(info))
    return shard


def test_whole_legacy_shard_has_exact_coverage_and_frame_counts(prepared):
    config, root, task, shard = prepared
    jobs = inventory([config], None)
    assert len(jobs) == 9
    assert {(job.instance, job.frames, job.shard) for job in jobs if job.status == "done"} == {
        (5, 4, shard), (9, 4, shard),
    }
    assert all(job.root == root for job in jobs)


def test_partial_selection_does_not_assign_part_of_legacy_shard(prepared):
    config, _, _, _ = prepared
    jobs = inventory([config], (5, 5))
    assert [(job.instance, job.status) for job in jobs] == [(5, "missing")]


def test_native_wins_without_partially_assigning_overlapping_legacy_shard(prepared):
    config, root, task, legacy = prepared
    shard = native_shard(root, task, legacy)
    jobs = inventory([config], None)
    assert [(job.instance, job.shard) for job in jobs if job.status == "done"] == [(5, shard)]
    assert jobs[8].status == "missing"


@pytest.mark.parametrize("damage", ["video", "parquet", "annotation", "extra_annotation", "metadata"])
def test_corrupt_completed_shard_is_diagnosed(prepared, damage):
    config, _, _, shard = prepared
    if damage == "video":
        shutil.rmtree(shard / "videos")
    elif damage == "parquet":
        next((shard / "data").glob("chunk-*/*.parquet")).unlink()
    elif damage == "annotation":
        path = shard / "annotations" / "episode_00000004.json"
        data = json.loads(path.read_text())
        data["total_frames"] = 10
        path.write_text(json.dumps(data))
    elif damage == "extra_annotation":
        (shard / "annotations" / "extra.json").write_text("{}")
    else:
        (shard / "meta/info.json").write_text("broken json")
    with pytest.raises(ValueError, match=str(shard)):
        inventory([config], None)


def test_legacy_depth_is_ignored_but_native_depth_is_an_error(prepared):
    config, root, task, legacy = prepared
    native = native_shard(root, task, legacy)
    for shard in (legacy, native):
        path = shard / "meta/info.json"
        data = json.loads(path.read_text())
        data["features"]["observation.depth"] = {"dtype": "float32"}
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Expected an RGB shard"):
        inventory([config], None)
    shutil.rmtree(native)
    assert not any(job.status == "done" for job in inventory([config], None))


def test_config_symlink_into_prepared_session_uses_prepared_root(prepared, tmp_path):
    config, root, _, _ = prepared
    link = tmp_path / "selected.yaml"
    link.symlink_to(config)
    assert all(job.root == root for job in inventory([link], None))


def test_prepared_config_symlink_outside_session_preserves_visible_root(prepared, tmp_path):
    config, root, _, _ = prepared
    original = tmp_path / "original.yaml"
    config.rename(original)
    config.symlink_to(original)
    assert all(job.root == root for job in inventory([config], None))


def test_invalid_manifest_error_identifies_file(prepared):
    config, root, task, _ = prepared
    manifest = root / "recordings" / task / "instance_0001" / "demo_001" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("broken")
    with pytest.raises(ValueError, match=str(manifest)):
        inventory([config], None)


def test_partial_selection_ignores_unselected_incomplete_native_shard(prepared):
    config, root, task, _ = prepared
    incomplete = root / "scheduling" / "shards" / task / "episode_00000000"
    incomplete.mkdir(parents=True)
    assert inventory([config], (5, 5))[0].status == "missing"
    with pytest.raises(ValueError, match=str(incomplete)):
        inventory([config], (1, 1))


def test_shard_annotation_must_match_prepared_instance(prepared):
    config, _, _, shard = prepared
    path = shard / "annotations" / "episode_00000004.json"
    data = json.loads(path.read_text())
    data["instance_id"] = 9
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Invalid replay annotation"):
        inventory([config], None)
