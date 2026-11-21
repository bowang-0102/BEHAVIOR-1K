"""Schedule prepared Memory Bench sessions on one Linux host."""

import argparse
import fcntl
import json
import math
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import ExitStack, closing
from dataclasses import dataclass
from pathlib import Path

from memory_bench.scheduling.inventory import Episode, inventory, session_root
from memory_bench.scheduling.resources import resource_snapshot, worker_environment
from memory_bench.session import Session


@dataclass
class Worker:
    process: subprocess.Popen
    stage: str
    gpu: int
    config: Path
    scratch: Path
    log: Path
    job: Episode = None
    result: Path = None
    started: float = 0
    warm: bool = False
    retiring: bool = False
    retire_started: float = 0


def save(db, job, worker=None):
    db.execute(
        "INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job.task, job.instance, job.status, job.frames, job.attempts, str(job.shard), job.error,
         worker.process.pid if worker else None, worker.gpu if worker else None, str(worker.log) if worker else None),
    )
    db.commit()


def stop(worker):
    if worker.process.poll() is None:
        try:
            os.killpg(worker.process.pid, signal.SIGTERM)
            worker.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(worker.process.pid, signal.SIGKILL)
            worker.process.wait()
        except ProcessLookupError:
            pass
    try:
        worker.process.stdin.close()
    except BrokenPipeError:
        pass
    shutil.rmtree(worker.scratch, ignore_errors=True)


def gpu_roles(gpus, pending, ready, workers, rates, prepare_slots):
    """Allocate GPUs by estimated seconds per frame, and drain a large ready buffer."""
    if not pending:
        return {gpu: "replay" for gpu in gpus}
    if len(gpus) == 1:
        buffering = (any(w.stage == "prepare" and not w.retiring for w in workers)
                     and sum(job.frames for job in ready) * rates["replay"] < 1800)
        return {gpus[0]: "replay" if ready and not buffering else "prepare"}
    replay_cards = max(1, sum(w.stage == "replay" and not w.retiring for w in workers))
    seconds_ready = sum(job.frames for job in ready) * rates["replay"] / replay_cards
    # A prep GPU can run several simulations. Reserve at least one render GPU.
    prep_time = rates["prepare"] / prepare_slots
    count = math.ceil(len(gpus) * prep_time / (prep_time + rates["replay"]))
    if seconds_ready >= 3600:
        count = 0
    elif seconds_ready >= 1800:
        count = 1
    count = min(count, len(gpus) - 1, len(pending))
    return {gpu: "prepare" if i < count else "replay" for i, gpu in enumerate(gpus)}


def run_pools(jobs, args, run, db, lock_fds):
    initial = resource_snapshot()
    requested = args.gpus if args.gpus is not None else list(initial.gpus)
    if not requested or len(set(requested)) != len(requested) or set(requested) - initial.gpus.keys():
        raise ValueError(f"Select distinct visible physical GPUs from {list(initial.gpus)}")
    # Prefer using a smaller eligible card for shared preparation.
    gpus = sorted((g for g in requested if initial.gpus[g] >= min(args.prep_vram, args.replay_vram)),
                  key=initial.gpus.get)
    if not gpus:
        raise RuntimeError("No selected GPU meets the worker VRAM budget")
    host_slots = int(min(initial.cpus / args.worker_cpus, initial.ram_gib / args.worker_ram))
    if host_slots == 1:
        # The two stages must time-share the one process slot.
        gpus = [gpus[-1]]
    elif 1 < host_slots < len(gpus):
        gpus = gpus[-host_slots:]
    if max(initial.gpus[g] for g in gpus) < args.replay_vram:
        raise RuntimeError("No selected GPU meets the replay VRAM budget")
    if any(j.status == "prepare" for j in jobs) and max(initial.gpus[g] for g in gpus) < args.prep_vram:
        raise RuntimeError("No selected GPU meets the preparation VRAM budget")
    gpus.sort(key=lambda g: (initial.gpus[g] < args.prep_vram, initial.gpus[g]))
    workers = []
    prep_caps = {g: min(args.prep_per_gpu, int(initial.gpus[g] / args.prep_vram)) for g in gpus}
    rates = {"prepare": 0.045, "replay": 0.16}  # startup estimates, seconds per output frame
    db.execute("CREATE TABLE IF NOT EXISTS attempts(task TEXT, instance INTEGER, stage TEXT, "
               "attempt INTEGER, gpu INTEGER, log TEXT, started REAL, seconds REAL, result TEXT, error TEXT)")
    priorities = {config: i for i, config in enumerate(args.configs)}
    vram = {"prepare": args.prep_vram, "replay": args.replay_vram}
    last_report = 0
    serial = 0
    try:
        while True:
            now = time.monotonic()
            for w in workers.copy():
                job = w.job
                exited = w.process.poll() is not None
                if w.retiring and now - w.retire_started > 30:
                    stop(w)
                    workers.remove(w)
                    continue
                expired = job is not None and now - w.started > args.timeout_minutes * 60
                if job and (w.result.exists() or exited or expired):
                    if w.result.is_file():
                        result = json.loads(w.result.read_text())
                    else:
                        reason = "time limit" if expired else f"worker exit {w.process.returncode}"
                        result = {"status": "failed", "error": reason}
                    status = result["status"]
                    expected = "ready" if w.stage == "prepare" else "done"
                    if status == "missing":
                        status = "failed"
                        result = {"status": status, "error": "Episode inputs disappeared after inventory"}
                    if status not in (expected, "failed"):
                        raise ValueError(f"Invalid worker result: {result}")
                    db.execute(
                        "UPDATE attempts SET seconds=?, result=?, error=? "
                        "WHERE task=? AND instance=? AND stage=? AND attempt=?",
                        (now - w.started, status, result.get("error"), job.task, job.instance, w.stage, job.attempts),
                    )
                    if status == "failed":
                        job.error = f"{result['error']} (log {w.log})"
                        print(f"FAILED {job.task}/{job.instance} {job.error}", flush=True)
                        oom = any(word in job.error.lower() for word in
                                  ("out of memory", "outofmemory", "cuda_error_out_of_memory"))
                        if w.stage == "prepare" and oom:
                            survivors = sum(other.gpu == w.gpu for other in workers) - 1
                            prep_caps[w.gpu] = min(prep_caps[w.gpu], max(1, survivors))
                        if job.attempts <= args.retries:
                            status = "prepare" if w.stage == "prepare" else "ready"
                    elif status == expected:
                        job.frames = result.get("frames", job.frames)
                        if job.frames:
                            sample = (now - w.started) / job.frames
                            rates[w.stage] = 0.5 * rates[w.stage] + 0.5 * sample
                        job.error = ""
                        job.attempts = 0
                    job.status = status
                    save(db, job, w)
                    w.job = None
                    w.warm = True
                    if result["status"] == "failed":
                        stop(w)
                        workers.remove(w)
                        continue
                if exited:
                    stop(w)
                    workers.remove(w)

            pending = [j for j in jobs if j.status == "prepare"]
            ready = sorted((j for j in jobs if j.status == "ready"), key=lambda j: (priorities[j.config], -j.frames))
            if not pending and not ready and not any(w.job for w in workers):
                break
            if now - last_report >= 60:
                replay_workers = max(1, sum(w.stage == "replay" and not w.retiring for w in workers))
                minutes = sum(j.frames for j in ready) * rates["replay"] / replay_workers / 60
                print(f"{dict(Counter(j.status for j in jobs))}  ready buffer ≈ {minutes:.1f} min", flush=True)
                last_report = now

            snapshot = resource_snapshot()
            # Exclude blocked filesystems from this scheduling pass so later
            # tasks on another volume can continue. Recheck after each dispatch.
            devices = {root: root.stat().st_dev for root in {job.root for job in jobs}}
            space = {device: shutil.disk_usage(root).free / 1024**3 for root, device in devices.items()}
            inflight = Counter(devices[w.job.root] for w in workers if w.job)

            def disk_fits(job):
                device = devices[job.root]
                return space[device] >= args.disk_reserve + (inflight[device] + 1) * args.job_disk

            pending = [j for j in pending if disk_fits(j)]
            ready = [j for j in ready if disk_fits(j)]
            roles = gpu_roles(gpus, pending, ready, workers, rates, max(1, max(prep_caps.values())))
            # A device that cannot fit a render worker may still prepare episodes.
            for gpu in gpus:
                if initial.gpus[gpu] < args.replay_vram and pending:
                    roles[gpu] = "prepare"
            if len(gpus) > 1 and ready and not any(roles[g] == "replay" and initial.gpus[g] >= args.replay_vram for g in gpus):
                eligible = [g for g in gpus if initial.gpus[g] >= args.replay_vram]
                roles[eligible[-1]] = "replay"

            queues = {"prepare": pending, "replay": ready}
            # Idle workers keep their Kit runtime when the next job uses the same config.
            # Close stdin to retire them gracefully before reusing their GPU for another role.
            for w in workers:
                queue = queues[roles[w.gpu]]
                if w.job is None and not w.retiring and (
                    w.stage != roles[w.gpu] or not queue or w.config != queue[0].config
                    or (w.stage == "prepare" and sum(x.gpu == w.gpu and not x.retiring for x in workers) > prep_caps[w.gpu])
                ):
                    w.process.stdin.close()
                    w.retiring = True
                    w.retire_started = now

            for gpu in sorted(gpus, key=lambda g: roles[g] != "replay"):
                stage = roles[gpu]
                queue = queues[stage]
                limit = 1 if stage == "replay" else prep_caps[gpu]
                for _ in range(limit):
                    if not queue:
                        break
                    job = next((j for j in queue if disk_fits(j)), None)
                    if job is None:
                        break
                    occupants = [w for w in workers if w.gpu == gpu]
                    if any(w.retiring or w.stage != stage for w in occupants):
                        break
                    w = next((w for w in occupants if not w.job and w.config == job.config), None)
                    if w is None:
                        starting = [other for other in workers if not other.warm]
                        reserved_replay = sum(role == "replay" and initial.gpus[g] >= args.replay_vram
                                              for g, role in roles.items())
                        if stage == "prepare" and sum(other.stage == "prepare" for other in workers) >= max(1, host_slots - reserved_replay):
                            break
                        if (len(occupants) >= limit or len(workers) + 1 > snapshot.cpus / args.worker_cpus
                                or snapshot.ram_gib < (len(starting) + 1) * args.worker_ram
                                or initial.ram_gib < (len(workers) + 1) * args.worker_ram
                                or initial.gpus[gpu] < (len(occupants) + 1) * vram[stage]
                                or snapshot.gpus.get(gpu, 0) < (1 + sum(s.gpu == gpu for s in starting)) * vram[stage]):
                            break
                        env = worker_environment(gpu, run / "runtime")
                        env["OMP_NUM_THREADS"] = str(args.worker_cpus)
                        env["MKL_NUM_THREADS"] = str(args.worker_cpus)
                        serial += 1
                        log = run / f"{serial:04d}-{stage}-gpu{gpu}.log"
                        with log.open("x") as stream:
                            process = subprocess.Popen(
                                [sys.executable, "-m", "memory_bench.scheduling.worker"],
                                stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, text=True,
                                env=env, start_new_session=True, pass_fds=lock_fds,
                            )
                        w = Worker(process, stage, gpu, job.config, Path(env["TMPDIR"]).parent, log)
                        workers.append(w)
                    queue.remove(job)
                    inflight[devices[job.root]] += 1
                    job.attempts += 1
                    job.status = "preparing" if stage == "prepare" else "rendering"
                    w.job, w.started = job, time.monotonic()
                    w.result = run / "results" / f"{job.task}-{job.instance}-{stage}-{job.attempts}.json"
                    command = dict(stage=stage, instance_id=job.instance, output_root=str(job.root), config=str(job.config),
                                   rgb=True, shard=str(job.shard), result=str(w.result))
                    db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                               (job.task, job.instance, stage, job.attempts, gpu, str(w.log), time.time()))
                    save(db, job, w)
                    try:
                        w.process.stdin.write(json.dumps(command) + "\n")
                        w.process.stdin.flush()
                    except BrokenPipeError:
                        pass  # poll() handles an early process exit through the normal retry path
            if not any(w.job or w.retiring for w in workers):
                raise RuntimeError("No job fits the current GPU, RAM, CPU or free disk budgets. "
                                   f"Visible GPUs (free GiB) {snapshot.gpus}, RAM {snapshot.ram_gib:.1f} GiB, "
                                   f"CPUs {snapshot.cpus:g}. Check --help or resume when resources are free.")
            time.sleep(2)
    finally:
        for w in workers:
            stop(w)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m memory_bench.scheduling", description=__doc__,
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("configs", nargs="*", type=Path, help="session configs in task priority order")
    parser.add_argument("--status", type=Path, metavar="RUN_DIR", help="read an existing run's progress database")
    parser.add_argument("--instances", metavar="FIRST-LAST", help="inclusive 1-based range, default all assigned instances")
    parser.add_argument("--gpus", type=int, nargs="+", help="allowed physical GPU IDs, default all visible")
    parser.add_argument("--plan", action="store_true", help="read-only inventory; no GPU required")
    parser.add_argument("--pack-to", type=Path, help="pack the published manifest into a new dataset")
    parser.add_argument("--min-per-task", type=int, default=1, help="minimum completed episodes for each task")
    parser.add_argument("--prep-per-gpu", type=int, default=4, help="upper limit on preparation processes per card")
    parser.add_argument("--worker-cpus", type=int, default=4, help="CPU capacity reserved per live worker")
    parser.add_argument("--worker-ram", type=float, default=12, help="RAM GiB reserved per worker")
    parser.add_argument("--prep-vram", type=float, default=6, help="VRAM GiB per prepare process")
    parser.add_argument("--replay-vram", type=float, default=8, help="VRAM GiB per replay process")
    parser.add_argument("--disk-reserve", type=float, default=8, help="free disk GiB to retain")
    parser.add_argument("--job-disk", type=float, default=2, help="additional disk GiB per in-flight job")
    parser.add_argument("--timeout-minutes", type=float, default=240, help="time limit per episode stage")
    parser.add_argument("--retries", type=int, default=1, help="extra attempts per failed stage in this run")
    args = parser.parse_args(argv)
    if args.status:
        uri = (args.status / "progress.sqlite3").resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            for task, status, count in db.execute("SELECT task,status,count(*) FROM jobs GROUP BY task,status ORDER BY task,status"):
                print(f"{task}  {status:10}  {count}")
            for row in db.execute("SELECT task,instance,status,gpu,log,error FROM jobs WHERE status IN ('preparing','rendering','failed','interrupted')"):
                print(*row)
        return
    if not args.configs:
        parser.error("supply at least one session config")
    args.configs = [p.absolute() for p in args.configs]
    if min(args.worker_cpus, args.prep_per_gpu, args.worker_ram, args.prep_vram, args.replay_vram,
           args.job_disk, args.timeout_minutes, args.min_per_task) <= 0 or min(args.disk_reserve, args.retries) < 0:
        parser.error("worker budgets, time limit and minimum count must be positive")
    selection = None
    if args.instances:
        try:
            first, last = map(int, args.instances.split("-"))
            selection = first, last
        except ValueError:
            parser.error("--instances requires FIRST-LAST")
    roots = [session_root(p, Session.load(p)) for p in args.configs]
    with ExitStack() as stack:
        locks = []
        if not args.plan:
            for root in sorted(set(roots)):
                # Acquire the same directory lock as the legacy parallel launcher.
                if not (root / "sessions").is_dir():
                    raise ValueError(f"Prepared sessions missing under {root}")
                directory = root / "parallel"
                directory.mkdir(exist_ok=True)
                fd = os.open(directory, os.O_RDONLY)
                stack.callback(os.close, fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError(f"Another launcher is using {root}") from None
                locks.append(fd)
        jobs = inventory(args.configs, selection)
        for config in args.configs:
            print(config, dict(Counter(j.status for j in jobs if j.config == config)), flush=True)
        if args.plan:
            return
        if args.pack_to and args.pack_to.exists():
            raise FileExistsError(f"Dataset destination already exists: {args.pack_to}")
        runs = roots[0] / "scheduling" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(dir=runs, prefix=time.strftime("%Y%m%d-%H%M%S-")))
        print(f"Run directory {run}", flush=True)
        with closing(sqlite3.connect(run / "progress.sqlite3")) as db:
            db.execute("CREATE TABLE jobs(task TEXT, instance INTEGER, status TEXT, frames INTEGER, attempts INTEGER, "
                       "shard TEXT, error TEXT, pid INTEGER, gpu INTEGER, log TEXT, PRIMARY KEY(task, instance))")
            for job in jobs:
                save(db, job)
            if any(j.status in ("prepare", "ready") for j in jobs):
                try:
                    run_pools(jobs, args, run, db, tuple(locks))
                finally:
                    db.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('preparing','rendering')")
                    db.commit()
        manifest = {}
        for config in args.configs:
            group = [j for j in jobs if j.config == config]
            done = [j for j in group if j.status == "done"]
            if any(j.status not in ("missing", "done") for j in group) or len(done) < args.min_per_task:
                raise RuntimeError(f"Incomplete task {config}: {dict(Counter(j.status for j in group))}. See {run}")
            manifest[done[0].task] = {f"episode_{j.instance-1:08d}": str(j.shard) for j in done}
        target = run / "manifest.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.replace(target)
        print(f"Pack manifest {target}", flush=True)
        if args.pack_to:
            from memory_bench.episodes.pack_dataset import pack_dataset
            args.pack_to.parent.mkdir(parents=True, exist_ok=True)
            shard_paths = {job.shard for job in jobs if job.status == "done"}
            estimate = sum(p.stat().st_size for shard in shard_paths for category in ("videos", "meta", "annotations", "data")
                           for p in (shard / category).rglob("*") if p.is_file())
            if shutil.disk_usage(args.pack_to.parent).free < estimate + args.disk_reserve * 1024**3:
                raise RuntimeError(f"Insufficient packing space in {args.pack_to.parent}; manifest saved at {target}")
            print(pack_dataset(roots[0], args.pack_to, manifest=target))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    main()
