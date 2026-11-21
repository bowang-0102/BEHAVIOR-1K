"""Execute pipeline jobs from stdin JSON lines, publishing status files atomically."""

import json
import os
import random
import shutil
import sys
import traceback
from pathlib import Path

import h5py

from memory_bench.episodes.build_episodes import build_episode
from memory_bench.episodes.compose_episodes import compose_episode
from memory_bench.episodes.replay_episodes import replay_episode
from memory_bench.session import Session, SessionPaths
from memory_bench.utils.episode.recipe import require_composed_files
from memory_bench.utils.scene.state import read_json, write_json_atomic


def execute(job):
    """Prepare one canonical episode or publish one private replay shard."""
    session = Session.load(job["config"])
    session.config["paths"]["output_root"] = str(Path(job["output_root"]).resolve())
    session.paths = SessionPaths(
        **session.config["paths"], session_id=session.session_id, scene_model=session.scene_model,
    )
    instance_id = job["instance_id"]
    if not 1 <= instance_id <= session.instance_count:
        raise ValueError(f"instance_id must be between 1 and {session.instance_count}")
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.omnigibson_data_root)
    episode_id = f"episode_{instance_id - 1:08d}"
    recipe_path = session.paths.recipes_dir / f"{episode_id}.json"
    recipe = read_json(recipe_path) if recipe_path.is_file() else None
    if recipe is not None and (recipe["session_id"], recipe["instance_id"], recipe["episode_id"]) != (
        session.session_id, instance_id, episode_id
    ):
        raise ValueError(f"Recipe identity differs from its session or filename: {recipe_path}")

    if job["stage"] == "prepare":
        backup = session.paths.episodes_dir / f".{episode_id}.previous"
        if recipe is not None and "output" in recipe:
            try:
                require_composed_files(session, recipe)
            except FileNotFoundError:
                # A crash can publish episode files before the composed recipe, or lose
                # one of the companion files. Recompose from the saved raw sources.
                recipe.pop("output")
                write_json_atomic(recipe_path, recipe)
            else:
                if backup.exists():
                    shutil.rmtree(backup)
                return {"status": "ready", "frames": recipe["output"]["total_frames"]}

        if recipe is None:
            # Keep completed bridges, but remove interrupted recordings and orphan
            # boundary snapshots: the bridge recorder refuses all existing outputs.
            directory = session.paths.bridges_dir / episode_id
            stems = {path.name.split(".")[0].removesuffix("_entry").removesuffix("_exit")
                     for path in directory.glob("bridge_*")}
            for stem in sorted(stems):
                path = directory / f"{stem}.hdf5"
                complete = False
                try:
                    with h5py.File(path, "r") as stream:
                        bridge = json.loads(stream["data"].attrs["memory_bench_bridge"])
                    states = bridge["states"]
                    complete = all((session.paths.resolve(states["directory"]) / states[key]).is_file()
                                   for key in ("entry", "exit"))
                except (OSError, KeyError, ValueError, TypeError):
                    pass
                if not complete:
                    for suffix in (".hdf5", "_entry.json", "_exit.json"):
                        (directory / f"{stem}{suffix}").unlink(missing_ok=True)

            # Match build_episodes --instance-range, including earlier random draws
            # and scanning complete demos instead of requiring composition.demo_id.
            session.composition["demo_id"] = None
            rng = random.Random(session.composition["order_seed"])
            for _ in range(instance_id):
                order = rng.choice(session.composition["task_orders"])
            built = build_episode(session, instance_id, order)
            if built is None:
                return {"status": "missing"}
            recipe = read_json(built)

        # Preserve pre-existing final files until the regenerated recipe commits.
        # Leftovers from a later interrupted retry can be regenerated from the recipe.
        for suffix in (".hdf5", "_scene.json", "_query_start.json"):
            path = session.paths.episodes_dir / f"{episode_id}{suffix}"
            if path.exists():
                backup.mkdir(exist_ok=True)
                if (backup / path.name).exists():
                    path.unlink()
                else:
                    path.rename(backup / path.name)
        recipe = read_json(compose_episode(session, recipe))
        require_composed_files(session, recipe)
        if backup.exists():
            shutil.rmtree(backup)
        return {"status": "ready", "frames": recipe["output"]["total_frames"]}

    if job["stage"] != "replay":
        raise ValueError(f"Unknown pipeline stage: {job['stage']}")
    if recipe is None or "output" not in recipe:
        return {"status": "missing"}
    require_composed_files(session, recipe)
    shard = Path(job["shard"])
    completion = {"session_id": session.session_id, "episode_ids": [episode_id]}
    if shard.exists():
        if read_json(shard / "complete.json") != completion:
            raise ValueError(f"Existing shard belongs to a different episode: {shard}")
        return {"status": "done"}

    # The existing exporter uses session_id as its dataset basename. A private
    # sibling container lets it retain that convention before atomic publication.
    temporary = shard.parent / f".{shard.name}.tmp"
    if temporary.exists():
        shutil.rmtree(temporary)
    session.paths.export_dir = temporary / session.session_id
    if job["rgb"]:
        session.export["obs_modalities"] = ["proprio", "rgb"]
    frames = replay_episode(session, recipe)
    if frames != recipe["output"]["total_frames"]:
        raise ValueError(f"Replay frame count differs from recipe: {frames}")
    write_json_atomic(session.paths.export_dir / "complete.json", completion)
    session.paths.export_dir.rename(shard)
    temporary.rmdir()
    return {"status": "done"}


def main():
    failed = False
    try:
        for line in sys.stdin:
            job = json.loads(line)
            try:
                # Clear the previous scene before loading another episode; the Kit
                # application stays alive and reuses its GPU allocation.
                og = sys.modules.get("omnigibson")
                if og is not None and og.sim is not None:
                    og.clear()
                result = execute(job)
            except (Exception, SystemExit) as error:
                traceback.print_exc()
                result = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
                failed = True
            write_json_atomic(Path(job["result"]), result)
            if failed:
                break
    finally:
        og = sys.modules.get("omnigibson")
        if og is not None and og.app is not None:
            if failed:
                og.app._app.post_quit(1)
            og.shutdown()
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
