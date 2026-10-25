from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import sys


DIRECTORY = Path(__file__).resolve().parent
REPOSITORY = DIRECTORY.parents[2]
sys.path.insert(0, str(REPOSITORY))
sys.dont_write_bytecode = True

# Parameters: previous task exit -> next task entry.
CONFIG = REPOSITORY / "memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml"
RAW_DATA = Path("/data/bwang/datasets/2026-challenge-rawdata")
FROM_SEGMENT, FROM_HDF5, FROM_DEMO = "intervent_1", RAW_DATA / "task-0000/episode_00000010.hdf5", 0
TO_SEGMENT, TO_HDF5, TO_DEMO = "intervent_2", RAW_DATA / "task-0022/episode_00220020.hdf5", 0
GPU = 6
RESOLUTION = 240
OUTPUT_DIR = DIRECTORY / "output"
OUTPUT = OUTPUT_DIR / "navigation.mp4"


def main():
    if OUTPUT.exists():
        raise FileExistsError(f"{OUTPUT} already exists; delete it or change OUTPUT before running again")

    os.environ["OMNIGIBSON_HEADLESS"] = "1"
    os.environ["OMNIGIBSON_GPU_ID"] = str(GPU)

    from memory_bench.session import Session
    from memory_bench.utils.recordings import FrameReader, inspect_source, recording_metadata
    from memory_bench.utils.scene_state import merge_scene_assets, write_json
    from memory_bench.utils.segment_manifest import compatible_interfaces, mapped_snapshot

    session = Session.load(CONFIG)
    source = inspect_source(FROM_HDF5, session.segments[FROM_SEGMENT], session.scene_model, demo=FROM_DEMO)
    target = inspect_source(TO_HDF5, session.segments[TO_SEGMENT], session.scene_model, demo=TO_DEMO)
    if not source["has_terminal_state"]:
        raise ValueError("The start recording must contain a real post-action terminal state")
    if not compatible_interfaces(source["interface"], target["interface"]):
        raise ValueError("The recordings have incompatible robot interfaces")
    if set(source["used"]) & set(target["used"]):
        raise ValueError("The two tasks use overlapping assets")
    print(f"Start: {FROM_HDF5}/demo_{FROM_DEMO} exit", flush=True)
    print(f"Target: {TO_HDF5}/demo_{TO_DEMO} entry", flush=True)

    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("This navigation test requires CUDA and Isaac Sim in the behavior environment")
    torch.cuda.set_device(GPU)
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)

    import omnigibson as og
    from omnigibson.macros import gm

    from memory_bench.episodes.generate_bridges import generate_bridge
    from memory_bench.tools.replay_data import render_recording

    with gm.unlocked():
        gm.RENDER_VIEWER_CAMERA = False

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        session.paths = replace(session.paths, output_root=OUTPUT_DIR)

        print("Loading the start and target states", flush=True)
        with FrameReader(FROM_HDF5, FROM_DEMO) as reader:
            robot_name = source["interface"]["robot_name"]
            reader.metadata["scene"]["objects_info"]["init_info"][robot_name]["args"]["obs_modalities"] = []
            previous_exit = reader.snapshot(source["frames"][1])
        with FrameReader(TO_HDF5, TO_DEMO) as reader:
            robot_name = target["interface"]["robot_name"]
            reader.metadata["scene"]["objects_info"]["init_info"][robot_name]["args"]["obs_modalities"] = []
            next_entry = reader.snapshot(0)
        next_entry = mapped_snapshot(next_entry, target["interface"]["robot_name"], previous_exit["robot"]["name"])
        world = merge_scene_assets(previous_exit, next_entry, target["used"])

        print("Stow arms -> navigate -> restore joints", flush=True)
        trajectory = OUTPUT_DIR / "bridge.hdf5"
        manifest = generate_bridge(session, deepcopy(world), deepcopy(world), next_entry, trajectory)
        write_json(OUTPUT_DIR / "navigation_report.json", manifest)

        print("Rendering navigation video", flush=True)
        metadata = recording_metadata(trajectory)
        render_recording(
            trajectory, metadata, OUTPUT, resolution=RESOLUTION,
            n_render_iterations=int(session.export["n_render_iterations"]),
            start_frame=0, end_frame=metadata["frames"],
        )
        print(f"Saved: {OUTPUT}", flush=True)
    finally:
        if og.app is not None:
            og.shutdown()


if __name__ == "__main__":
    main()
