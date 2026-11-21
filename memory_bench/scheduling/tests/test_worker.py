import io
import json
import random
import sys
from copy import deepcopy
from pathlib import Path

import h5py
import pytest

from memory_bench.scheduling import worker
from memory_bench.tests.conftest import collected_episode, episode_sources, memory_task  # noqa: F401
from memory_bench.utils.scene.state import read_json, write_json


@pytest.fixture
def worker_job(collected_episode, monkeypatch, tmp_path):
    session, _, _, collect, _ = collected_episode
    session.config["paths"] = {
        "omnigibson_data_root": str(tmp_path), "intervent_rawdata_root": str(tmp_path),
        "output_root": str(tmp_path / "old-output"),
    }
    session.config["export"] = {"obs_modalities": ["proprio", "rgb", "depth_linear"]}
    monkeypatch.setattr(worker.Session, "load", lambda _: deepcopy(session))
    collect()
    return {
        "config": str(tmp_path / "config.yaml"), "output_root": str(tmp_path),
        "instance_id": 1, "stage": "prepare", "shard": str(tmp_path / "shards" / "episode_00000000"),
        "rgb": True, "result": str(tmp_path / "status.json"),
    }, session


def test_prepare_recovers_partial_files_and_reuses_completed_episode(worker_job):
    job, session = worker_job
    bridge_dir = session.paths.bridges_dir / "episode_00000000"
    bridge_dir.mkdir(parents=True)
    with h5py.File(bridge_dir / "bridge_00.hdf5", "w") as stream:
        stream.create_group("data")
    (bridge_dir / "bridge_00_entry.json").write_text("incomplete")
    (bridge_dir / "bridge_01_exit.json").write_text("orphan")
    session.paths.episodes_dir.mkdir(parents=True)
    (session.paths.episodes_dir / "episode_00000000.hdf5.tmp").write_text("partial")
    (session.paths.episodes_dir / "episode_00000000_scene.json").write_text("partial")

    assert worker.execute(job) == {"status": "ready", "frames": 14}
    recipe_path = session.paths.recipes_dir / "episode_00000000.json"
    recipe = read_json(recipe_path)
    assert recipe["output"]["total_frames"] == 14
    assert worker.execute(job) == {"status": "ready", "frames": 14}
    # Recover a composed recipe whose companion was lost, using the existing sources.
    session.paths.resolve(recipe["output"]["replay_scene"]).unlink()
    assert worker.execute(job) == {"status": "ready", "frames": 14}


def test_prepare_missing_collection_returns_missing(worker_job):
    job, session = worker_job
    directory = session.paths.recordings_dir / "instance_0001" / "demo_001"
    record = read_json(directory / "manifest.json")
    record["status"] = "incomplete"
    (directory / "manifest.json").write_text(json.dumps(record))
    assert worker.execute(job) == {"status": "missing"}


def test_prepare_preserves_range_order_draws(worker_job, monkeypatch):
    job, session = worker_job
    session.config["composition"]["task_orders"] *= 2
    orders = session.config["composition"]["task_orders"]
    orders[1] = ["key_1", "intervent_1", "intervent_2", "query"]
    monkeypatch.setattr(worker.Session, "instance_count", property(lambda _: 8))
    job["instance_id"] = 7
    actual = []

    def build(current, instance_id, order):
        assert current.composition["demo_id"] is None
        assert current.config["paths"]["output_root"] == job["output_root"]
        assert current.paths.output_root == Path(job["output_root"])
        actual.append(order)
        return None

    monkeypatch.setattr(worker, "build_episode", build)
    assert worker.execute(job) == {"status": "missing"}
    rng = random.Random(session.composition["order_seed"])
    expected = [rng.choice(orders) for _ in range(7)][-1]
    assert actual == [expected]


def test_replay_publishes_private_shard_and_recovers_interrupted_export(worker_job, monkeypatch):
    job, session = worker_job
    worker.execute(job)
    job["stage"] = "replay"
    shard = Path(job["shard"])
    temporary = shard.parent / f".{shard.name}.tmp"
    temporary.mkdir(parents=True)
    (temporary / "incomplete").write_text("old")

    def replay(current, recipe):
        assert current.paths.output_root == session.paths.output_root
        assert current.paths.export_dir == temporary / session.session_id
        assert current.export["obs_modalities"] == ["proprio", "rgb"]
        assert not (temporary / "incomplete").exists()
        assert not shard.exists()
        write_json(current.paths.export_dir / "annotations" / f"{recipe['episode_id']}.json", {
            "episode_id": recipe["episode_id"],
        })
        return recipe["output"]["total_frames"]

    monkeypatch.setattr(worker, "replay_episode", replay)
    assert worker.execute(job) == {"status": "done"}
    assert not temporary.exists()
    assert read_json(shard / "complete.json") == {
        "session_id": session.session_id, "episode_ids": ["episode_00000000"],
    }
    monkeypatch.setattr(worker, "replay_episode", lambda *_: pytest.fail("replayed completed shard"))
    assert worker.execute(job) == {"status": "done"}


def test_worker_loops_and_stops_after_failure(tmp_path, monkeypatch):
    jobs = [{"result": str(tmp_path / f"{index}.json")} for index in range(4)]
    results = iter([{"status": "missing"}, {"status": "done"}])
    calls = []

    def execute(job):
        calls.append(job)
        if len(calls) == 3:
            raise RuntimeError("broken scene")
        return next(results)

    monkeypatch.setattr(worker, "execute", execute)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(json.dumps(job) for job in jobs)))
    monkeypatch.delitem(sys.modules, "omnigibson", raising=False)
    assert worker.main() == 1
    assert len(calls) == 3
    assert read_json(tmp_path / "0.json") == {"status": "missing"}
    assert read_json(tmp_path / "1.json") == {"status": "done"}
    assert read_json(tmp_path / "2.json") == {"status": "failed", "error": "RuntimeError: broken scene"}
    assert not (tmp_path / "3.json").exists()


def test_prepare_retry_retains_valid_completed_bridges(worker_job, monkeypatch):
    job, session = worker_job
    worker.execute(job)
    bridge = session.paths.bridges_dir / "episode_00000000" / "bridge_00.hdf5"
    before = bridge.stat().st_mtime_ns
    # Simulate losing the atomic recipe publication after every bridge finished.
    (session.paths.recipes_dir / "episode_00000000.json").unlink()
    monkeypatch.setattr(
        "memory_bench.episodes.generate_bridges.generate_bridge",
        lambda *args, **kwargs: pytest.fail("discarded a completed bridge"),
    )
    assert worker.execute(job) == {"status": "ready", "frames": 14}
    assert bridge.stat().st_mtime_ns == before


def test_failed_replay_does_not_publish_destination(worker_job, monkeypatch):
    job, _ = worker_job
    worker.execute(job)
    job["stage"] = "replay"
    shard = Path(job["shard"])

    def replay(session, recipe):
        write_json(session.paths.export_dir / "partial.json", {})
        raise RuntimeError("render failed")

    monkeypatch.setattr(worker, "replay_episode", replay)
    with pytest.raises(RuntimeError, match="render failed"):
        worker.execute(job)
    assert not shard.exists()
    assert not (shard / "complete.json").exists()


def test_failed_recompose_keeps_previous_files_for_retry(worker_job, monkeypatch):
    job, session = worker_job
    worker.execute(job)
    episode = "episode_00000000"
    files = session.paths.episodes_dir
    old_hdf = files / f"{episode}.hdf5"
    inode = old_hdf.stat().st_ino
    (files / f"{episode}_scene.json").unlink()

    with monkeypatch.context() as patch:
        def fail_compose(session, recipe):
            raise RuntimeError("interrupted compose")

        patch.setattr(worker, "compose_episode", fail_compose)
        with pytest.raises(RuntimeError, match="interrupted compose"):
            worker.execute(job)
    previous = files / f".{episode}.previous"
    assert (previous / old_hdf.name).stat().st_ino == inode
    assert "output" not in read_json(session.paths.recipes_dir / f"{episode}.json")
    assert worker.execute(job) == {"status": "ready", "frames": 14}
    assert old_hdf.is_file() and not previous.exists()
