"""Reconcile frozen session inputs, composed trajectories and published RGB shards."""

import json
from dataclasses import dataclass
from pathlib import Path

from memory_bench.session import Session


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read pipeline metadata {path}: {error}") from error


@dataclass
class Episode:
    config: Path
    root: Path
    task: str
    instance: int
    status: str
    shard: Path
    frames: int = 0
    attempts: int = 0
    error: str = ""


def session_root(config, session):
    # Preserve a prepared config's visible location, including a symlink to an
    # original YAML; also recognize a symlink elsewhere pointing into sessions/.
    for path in (config.absolute(), config.resolve()):
        if path.parent.parent.name == "sessions" and path.parent.name == session.session_id:
            return path.parents[2].resolve()
    return session.paths.output_root


def validate_shard(shard, session, episode_ids):
    """Check publication metadata and referenced modalities without decoding datasets."""
    from memory_bench.utils.episode.recipe import validate_annotations

    info = read(shard / "meta/info.json")
    if info["codebase_version"] != "v3.0" or info["total_episodes"] != len(episode_ids):
        raise ValueError(f"Inconsistent shard metadata: {shard}")
    features = info["features"]
    videos = [key for key, item in features.items() if item["dtype"] == "video"]
    if not videos or "action" not in features or "observation.state" not in features:
        raise ValueError(f"Missing RGB, action, or proprioception features: {shard}")
    if {p.stem for p in (shard / "annotations").glob("*.json")} != set(episode_ids):
        raise ValueError(f"Completion marker differs from annotation inventory: {shard}")
    omni = read(shard / "meta/omnigibson.json")
    if not omni["cam_intrinsics"] or not omni["omnigibson_git_hash"]:
        raise ValueError(f"Missing camera intrinsics or simulator revision: {shard}")
    frames = {}
    for index, name in enumerate(episode_ids):
        annotation = read(shard / "annotations" / f"{name}.json")
        validate_annotations(annotation)
        if (annotation["session_id"], annotation["episode_id"], annotation["episode_index"],
                annotation["instance_id"], annotation["task"], annotation["fps"]) != (
                session.session_id, name, index, int(name.removeprefix("episode_")) + 1,
                session.task.query_instruction, info["fps"]):
            raise ValueError(f"Invalid replay annotation for {name}: {shard}")
        frames[name] = annotation["total_frames"]
    if sum(frames.values()) != info["total_frames"]:
        raise ValueError(f"Annotation frame count differs from metadata: {shard}")
    directories = [("data", ".parquet"), ("meta/episodes", ".parquet")]
    directories.extend((f"videos/{key}", ".mp4") for key in videos)
    for directory, suffix in directories:
        files = sorted((shard / directory).glob(f"chunk-*/file-*{suffix}"))
        if not files or any(path.stat().st_size == 0 for path in files):
            raise ValueError(f"Missing or empty shard files: {shard / directory}")
    tasks_path = shard / "meta/tasks.parquet"
    if not tasks_path.is_file() or tasks_path.stat().st_size == 0:
        raise ValueError(f"Missing or empty shard file: {tasks_path}")
    return frames


def inventory(configs, selection):
    jobs = []
    tasks = set()
    for config in configs:
        config = Path(config).absolute()
        session = Session.load(config)
        task = session.session_id
        if task in tasks:
            raise ValueError(f"Supply one config per task: {task}")
        tasks.add(task)
        root = session_root(config, session)
        assignments = read(root / "sessions" / task / "variant_assignments.json")
        if assignments["session_id"] != task:
            raise ValueError(f"Prepared assignments belong to another task: {task}")
        ids = [item["instance_id"] for item in assignments["instances"]]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError(f"Invalid prepared instance IDs: {task}")
        if selection is not None:
            first, last = selection
            if not 1 <= first <= last <= len(ids):
                raise ValueError(f"Range must be within 1-{len(ids)} for {task}")
            ids = ids[first - 1:last]
        selected = {f"episode_{instance - 1:08d}" for instance in ids}
        shards = {}
        shard_frames = {}
        native = root / "scheduling" / "shards" / task
        roots = sorted(native.glob("episode_*"))
        roots += [root / "lerobot_shards" / task]
        roots += sorted((root / "parallel").glob(f"*/lerobot_shards/{task}"))
        for shard in roots:
            if not shard.is_dir():
                continue
            if shard.parent == native and shard.name not in selected:
                continue
            marker = shard / "complete.json"
            if not marker.exists() and shard.parent != native:
                continue
            completion = read(marker)
            if not isinstance(completion, dict):
                raise ValueError(f"Invalid completion marker: {marker}")
            episode_ids = completion.get("episode_ids")
            if (completion.get("session_id") != task or not isinstance(episode_ids, list)
                    or not episode_ids or any(not isinstance(name, str) for name in episode_ids)
                    or len(set(episode_ids)) != len(episode_ids)):
                raise ValueError(f"Invalid completion marker: {marker}")
            if shard.parent == native and episode_ids != [shard.name]:
                raise ValueError(f"Unexpected episode in {marker}")
            prepared = {f"episode_{i - 1:08d}" for i in range(1, len(assignments["instances"]) + 1)}
            if any(name not in prepared for name in episode_ids):
                raise ValueError(f"Completion marker has an unprepared episode: {marker}")
            # Packing consumes whole shards. Never assign just a selected subset,
            # nor combine overlapping legacy shards into an inexact manifest.
            if not set(episode_ids) <= selected or any(name in shards for name in episode_ids):
                continue
            info = read(shard / "meta/info.json")
            if not isinstance(info, dict) or not isinstance(info.get("features"), dict):
                raise ValueError(f"Invalid shard metadata: {shard / 'meta/info.json'}")
            if any("depth" in key for key in info["features"]):
                if shard.parent == native:
                    raise ValueError(f"Expected an RGB shard: {shard}")
                continue
            try:
                frames = validate_shard(shard, session, episode_ids)
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ValueError(f"Invalid completed shard {shard}: {error}") from error
            for name in episode_ids:
                shards[name] = shard
                shard_frames[name] = frames[name]

        for instance in ids:
            episode = f"episode_{instance - 1:08d}"
            job = Episode(config, root, task, instance, "prepare", native / episode)
            if episode in shards:
                job.status, job.shard, job.frames = "done", shards[episode], shard_frames[episode]
            else:
                path = root / "recipes" / task / f"{episode}.json"
                if path.is_file():
                    recipe = read(path)
                    if (recipe["session_id"], recipe["episode_id"], recipe["instance_id"]) != (task, episode, instance):
                        raise ValueError(f"Invalid recipe identity: {path}")
                    output = recipe.get("output")
                    if output:
                        files = [output["hdf5"], output["replay_scene"]]
                        if output.get("query_start"):
                            files.append(output["query_start"])
                        if all((root / name).is_file() for name in files):
                            job.status, job.frames = "ready", output["total_frames"]
                else:
                    directory = root / "recordings" / task / f"instance_{instance:04d}"
                    if not any(read(p)["status"] == "complete" for p in directory.glob("demo_*/manifest.json")):
                        job.status = "missing"
            jobs.append(job)
    return jobs
