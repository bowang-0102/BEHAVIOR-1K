import json
import sqlite3
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory_bench.scheduling import __main__ as scheduler
from memory_bench.scheduling.inventory import Episode
from memory_bench.scheduling.resources import ResourceSnapshot


@pytest.fixture
def pool(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("{}")
    run = tmp_path / "run"
    (run / "results").mkdir(parents=True)
    args = SimpleNamespace(
        gpus=None, configs=[config], prep_per_gpu=2, prep_vram=6, replay_vram=8,
        worker_cpus=4, worker_ram=12, disk_reserve=8, job_disk=2,
        timeout_minutes=1, retries=1,
    )
    job = Episode(config, tmp_path, "task", 1, "prepare", tmp_path / "shard")
    snapshot = ResourceSnapshot({4: 24}, 64, 8)
    monkeypatch.setattr(scheduler, "resource_snapshot", lambda: snapshot)
    monkeypatch.setattr(scheduler.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3))
    monkeypatch.setattr(scheduler.time, "sleep", lambda seconds: None)
    state = SimpleNamespace(args=args, job=job, run=run, commands=[], processes=[], failures=Counter())

    class FakeProcess:
        def __init__(self, command, **options):
            self.pid = 10000 + len(state.processes)
            self.returncode = None
            self.stdin = self
            self.env = options["env"]
            state.processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def write(self, line):
            job = json.loads(line)
            state.commands.append(job)
            stage = job["stage"]
            if state.failures[stage]:
                state.failures[stage] -= 1
                result = {"status": "failed", "error": "CUDA out of memory"}
            else:
                result = {"status": "ready" if stage == "prepare" else "done", "frames": 120}
            Path(job["result"]).write_text(json.dumps(result))

        def flush(self):
            pass

        def close(self):
            self.returncode = 0

    def kill_process(pid, signal):
        next(process for process in state.processes if process.pid == pid).returncode = -signal

    monkeypatch.setattr(scheduler.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(scheduler.os, "killpg", kill_process)
    with sqlite3.connect(":memory:") as db:
        db.execute(
            "CREATE TABLE jobs(task TEXT, instance INTEGER, status TEXT, frames INTEGER, attempts INTEGER, "
            "shard TEXT, error TEXT, pid INTEGER, gpu INTEGER, log TEXT, PRIMARY KEY(task, instance))"
        )
        state.db = db
        yield state


def test_single_gpu_prepares_then_replays_and_skips_completed_jobs(pool):
    complete = Episode(pool.job.config, pool.job.root, "task", 2, "done", pool.job.root / "old_shard")
    scheduler.run_pools([pool.job, complete], pool.args, pool.run, pool.db, ())
    assert [(command["stage"], command["instance_id"]) for command in pool.commands] == [
        ("prepare", 1), ("replay", 1),
    ]
    assert pool.job.status == complete.status == "done"
    assert pool.db.execute("SELECT status, frames, gpu FROM jobs WHERE instance=1").fetchone() == ("done", 120, 4)
    assert all(process.env["OMNIGIBSON_GPU_ID"] == "4" for process in pool.processes)
    assert all(process.env["OMNIGIBSON_PHYSICS_GPU_ID"] == "0" for process in pool.processes)
    assert all(process.poll() is not None for process in pool.processes)
    assert not list((pool.run / "runtime").iterdir())


def test_resume_ready_episode_does_not_prepare_again(pool):
    pool.job.status = "ready"
    pool.job.frames = 100
    scheduler.run_pools([pool.job], pool.args, pool.run, pool.db, ())
    assert [command["stage"] for command in pool.commands] == ["replay"]
    assert pool.job.status == "done"


def test_failed_worker_retried_then_progresses(pool):
    pool.failures["prepare"] = 1
    scheduler.run_pools([pool.job], pool.args, pool.run, pool.db, ())
    assert [command["stage"] for command in pool.commands] == ["prepare", "prepare", "replay"]
    assert pool.job.status == "done"
    assert pool.job.error == ""
    assert len({process.pid for process in pool.processes}) == 3


def test_failure_attempts_are_bounded_and_saved(pool):
    pool.failures["prepare"] = 100
    scheduler.run_pools([pool.job], pool.args, pool.run, pool.db, ())
    assert len(pool.commands) == 2
    assert pool.job.status == "failed"
    assert pool.job.attempts == 2
    assert "CUDA out of memory" in pool.job.error
    assert pool.db.execute("SELECT status, attempts FROM jobs").fetchone() == ("failed", 2)
    assert all(process.poll() is not None for process in pool.processes)


@pytest.mark.parametrize("budget,value", [("worker_ram", 1000), ("worker_cpus", 1000), ("disk_reserve", 1000)])
def test_resource_admission_refuses_without_launching(pool, budget, value):
    setattr(pool.args, budget, value)
    with pytest.raises(RuntimeError, match="No job fits"):
        scheduler.run_pools([pool.job], pool.args, pool.run, pool.db, ())
    assert not pool.processes
    assert pool.job.attempts == 0


def test_plan_reads_prepared_inventory_without_gpu_or_writes(tmp_path, monkeypatch, capsys):
    root = tmp_path / "output"
    session_dir = root / "sessions" / "task"
    session_dir.mkdir(parents=True)
    config = session_dir / "config.yaml"
    config.write_text("{}")
    (session_dir / "variant_assignments.json").write_text(
        json.dumps({"session_id": "task", "instances": [{"instance_id": 1}]})
    )
    session = SimpleNamespace(session_id="task", paths=SimpleNamespace(output_root=root))
    monkeypatch.setattr(scheduler.Session, "load", lambda config: session)

    def unexpected_resource_query():
        pytest.fail("--plan must not query GPUs")

    monkeypatch.setattr(scheduler, "resource_snapshot", unexpected_resource_query)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    scheduler.main([str(config), "--plan"])
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after
    assert not (root / "parallel").exists()
    assert not (root / "scheduling").exists()
    assert "'missing': 1" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["done", "failed"])
def test_main_publishes_manifest_only_for_successful_task(pool, monkeypatch, status):
    pool.job.status = status
    (pool.job.root / "sessions").mkdir()
    session = SimpleNamespace(session_id="task", paths=SimpleNamespace(output_root=pool.job.root))
    monkeypatch.setattr(scheduler.Session, "load", lambda config: session)
    missing = Episode(pool.job.config, pool.job.root, "task", 2, "missing", pool.job.root / "missing_shard")
    monkeypatch.setattr(scheduler, "inventory", lambda configs, selection: [pool.job, missing])
    if status == "failed":
        with pytest.raises(RuntimeError, match="Incomplete task"):
            scheduler.main([str(pool.job.config)])
    else:
        scheduler.main([str(pool.job.config)])
    runs = list((pool.job.root / "scheduling" / "runs").iterdir())
    assert len(runs) == 1
    manifest = runs[0] / "manifest.json"
    assert manifest.exists() == (status == "done")
    if status == "done":
        assert json.loads(manifest.read_text()) == {"task": {"episode_00000000": str(pool.job.shard)}}
    assert not pool.processes
    with sqlite3.connect(runs[0] / "progress.sqlite3") as db:
        assert db.execute("SELECT instance, status FROM jobs ORDER BY instance").fetchall() == [
            (1, status), (2, "missing"),
        ]


def test_two_gpus_reserve_host_slot_for_replay(pool, monkeypatch):
    first = pool.job
    second = Episode(first.config, first.root, "task", 2, "prepare", first.root / "second")
    monkeypatch.setattr(scheduler, "resource_snapshot", lambda: ResourceSnapshot({2: 24, 5: 24}, 64, 8))
    scheduler.run_pools([first, second], pool.args, pool.run, pool.db, ())
    attempts = pool.db.execute("SELECT instance,stage,gpu FROM attempts ORDER BY rowid").fetchall()
    # The first replay starts before preparation of the second episode.
    assert attempts[:2] == [(1, "prepare", 2), (1, "replay", 5)]
    assert first.status == second.status == "done"


def test_low_disk_after_prepare_does_not_leave_idle_worker_spinning(pool, monkeypatch):
    def disk_usage(path):
        free_gib = 100 if not pool.commands else 1
        return SimpleNamespace(free=free_gib * 1024**3)

    monkeypatch.setattr(scheduler.shutil, "disk_usage", disk_usage)
    with pytest.raises(RuntimeError, match="No job fits"):
        scheduler.run_pools([pool.job], pool.args, pool.run, pool.db, ())
    assert pool.job.status == "ready"
    assert [j["stage"] for j in pool.commands] == ["prepare"]


def test_existing_parallel_lock_blocks_second_launcher(tmp_path, monkeypatch):
    (tmp_path / "sessions").mkdir()
    parallel = tmp_path / "parallel"
    parallel.mkdir()
    session = SimpleNamespace(session_id="task", paths=SimpleNamespace(output_root=tmp_path))
    monkeypatch.setattr(scheduler.Session, "load", lambda config: session)
    fd = scheduler.os.open(parallel, scheduler.os.O_RDONLY)
    try:
        scheduler.fcntl.flock(fd, scheduler.fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match="Another launcher"):
            scheduler.main([str(tmp_path / "config.yaml")])
        assert not (tmp_path / "scheduling").exists()
    finally:
        scheduler.os.close(fd)
