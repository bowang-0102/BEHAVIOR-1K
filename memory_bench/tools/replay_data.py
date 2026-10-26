import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

import h5py
import numpy as np
from PIL import Image, ImageDraw

from memory_bench.session import Session
from memory_bench.utils.recordings import recording_metadata


def replay_config(metadata, resolution):
    """Use the file's frozen scene and robot, with RGB sensors for inspection."""
    config, scene = deepcopy(metadata["config"]), deepcopy(metadata["scene"])
    config["scene"].update(scene_file=scene, load_room_types=None, load_room_instances=None, include_robots=True)
    config["objects"] = []
    config["task"] = {"type": "DummyTask", "include_obs": False}
    config["env"].update(automatic_reset=False, flatten_obs_space=True)
    robot = config["robots"][0]
    sensor_config = {
        "VisionSensor": {"sensor_kwargs": {"image_height": resolution, "image_width": resolution}},
        "zed_link:Camera:0": {
            "modalities": ["rgb"],
            "sensor_kwargs": {"horizontal_aperture": 40.0, "image_height": resolution, "image_width": resolution},
        },
    }
    robot.update(
        obs_modalities=["rgb"], sensor_config=sensor_config, include_sensor_names=None, exclude_sensor_names=None,
    )
    # Cached scene robots are constructed from their own initializer, not just config["robots"].
    scene["objects_info"]["init_info"][robot["name"]]["args"].update(
        obs_modalities=["rgb"], sensor_config=deepcopy(sensor_config),
        include_sensor_names=None, exclude_sensor_names=None,
    )
    config["env"]["external_sensors"] = [
        {
            "sensor_type": "VisionSensor",
            "name": "inspection_camera",
            "relative_prim_path": f"/controllable__r1pro__{robot['name']}/base_link/inspection_camera",
            "modalities": ["rgb"],
            "sensor_kwargs": {"image_height": resolution, "image_width": resolution * 3, "horizontal_aperture": 40.0},
            "position": [-0.4, 0.0, 2.0],
            "orientation": [0.2706, -0.2706, -0.6533, 0.6533],
            "pose_frame": "parent",
        }
    ]
    return config


def inspection_frame(observation, robot_name, resolution, caption):
    """Label the three robot views and the wider external view with a source frame/time."""
    canvas = Image.new("RGB", (resolution * 3, resolution * 2))
    draw = ImageDraw.Draw(canvas)
    views = [
        (f"{robot_name}::{robot_name}:left_realsense_link:Camera:0::rgb", "Left wrist", 0, 0),
        (f"{robot_name}::{robot_name}:zed_link:Camera:0::rgb", "Head", resolution, 0),
        (f"{robot_name}::{robot_name}:right_realsense_link:Camera:0::rgb", "Right wrist", resolution * 2, 0),
        ("external::inspection_camera::rgb", "External", 0, resolution),
    ]
    for key, label, x, y in views:
        pixels = observation[key].cpu().numpy()
        view = Image.fromarray(pixels[..., :3])
        canvas.paste(view, (x, y))
        draw.rectangle((x, y, x + 90, y + 18), fill="black")
        draw.text((x + 4, y + 3), label, fill="white")
    draw.rectangle((0, resolution * 2 - 22, resolution * 3, resolution * 2), fill="black")
    draw.text((4, resolution * 2 - 18), caption, fill="white")
    return np.asarray(canvas)


def render_recording(path, metadata, video_path, *, resolution, n_render_iterations, start_frame, end_frame):
    """Decode recorded states and render them without advancing physics or replaying actions."""
    import torch as th
    import omnigibson as og
    from omnigibson.envs.data_wrapper import (
        _add_recorded_non_kin_states_to_scene_file,
        _align_scene_object_states_with_recorded_schema,
    )
    from omnigibson.eval.utils.obs_utils import create_video_writer, write_video
    from omnigibson.macros import gm

    from memory_bench.episodes.replay_episodes import _render_observation

    with gm.unlocked():
        gm.ENABLE_TRANSITION_RULES = False
        gm.VISUAL_ONLY = True
        gm.RENDER_VIEWER_CAMERA = False
    config = replay_config(metadata, resolution)
    scene = config["scene"]["scene_file"]
    _add_recorded_non_kin_states_to_scene_file(scene, [scene])
    env = og.Environment(configs=config)
    _align_scene_object_states_with_recorded_schema(env.scene, metadata["scene"])
    robot = env.scene.robots[0]
    robot.control_enabled = False
    env.load_observation_space()
    fps = int(metadata["interface"]["fps"])
    container, stream = create_video_writer(str(video_path), (resolution * 2, resolution * 3), rate=fps)
    state = deepcopy(metadata["scene"]["state"])
    previous_state = previous_frame = previous_episodes = previous_origin = None
    robot_names = {robot.name for robot in env.scene.robots}
    supported_grasping = all(
        not robot.is_manipulation or robot.grasping_mode in {"physical", "assisted"}
        for robot in env.scene.robots
    )
    assisted_names = {
        robot.name for robot in env.scene.robots if robot.is_manipulation and robot.grasping_mode == "assisted"
    }
    exit_path = path.with_name(f"{path.stem}_exit.json")
    try:
        with h5py.File(path, "r") as recording:
            trajectory = recording["data/demo_0"]
            json_states = trajectory.get("state_json")
            has_terminal = metadata["states"] > metadata["frames"]
            stop_frame = end_frame + int(end_frame == metadata["frames"] and has_terminal)
            # JSON states are complete; serialized recordings may need earlier filtered frames.
            for frame in range(start_frame if json_states is not None else 0, stop_frame):
                if json_states is not None:
                    state = json.loads(json_states[frame])
                else:
                    flat = th.as_tensor(trajectory["state"][frame, : int(trajectory["state_size"][frame])])
                    decoded, consumed = og.sim.deserialize(flat)
                    if consumed != len(flat):
                        raise ValueError(f"{path}: recorded state schema mismatch at frame {frame}")
                    state["registry"]["object_registry"].update(decoded[0]["registry"]["object_registry"])
                    state["registry"]["system_registry"].update(decoded[0]["registry"]["system_registry"])
                if frame < start_frame:
                    continue
                load_names = None
                if json_states is not None and supported_grasping:
                    episodes = env._current_episodes.clone()
                    origin = tuple(tuple(value.tolist()) for value in env.scene.get_position_orientation())
                    objects = state["registry"]["object_registry"]
                    if (
                        previous_state is not None and previous_frame == frame - 1
                        and th.equal(episodes, previous_episodes) and origin == previous_origin
                        and state.get("pos") == previous_state.get("pos")
                        and state.get("ori") == previous_state.get("ori")
                    ):
                        previous_objects = previous_state["registry"]["object_registry"]
                        # Active assisted grasping remains on the full path until verified.
                        inactive_grasps = all(
                            not objects[name].get("ag_obj_constraint_params")
                            and not previous_objects[name].get("ag_obj_constraint_params")
                            for name in assisted_names
                        )
                        if inactive_grasps and objects.keys() == previous_objects.keys() and all(
                            value.get("non_kin", {}).get("AttachedTo")
                            == previous_objects[name].get("non_kin", {}).get("AttachedTo")
                            for name, value in objects.items()
                        ):
                            load_names = robot_names | {
                                name for name, value in objects.items() if value != previous_objects[name]
                            }
                observation, _, _ = _render_observation(
                    env, deepcopy(state), max(10, n_render_iterations) if frame == start_frame else n_render_iterations,
                    load_names=load_names, repair_names=load_names,
                )
                if json_states is not None and supported_grasping:
                    # Cache raw JSON only after successful application; loading may mutate its copy.
                    previous_state, previous_frame = state, frame
                    previous_episodes, previous_origin = episodes, origin
                phase = "terminal state (post-action)" if frame == metadata["frames"] else "pre-action"
                image = inspection_frame(
                    observation[0], robot.name, resolution,
                    f"{path.stem} | frame {frame}/{metadata['frames']} | {frame / fps:.2f}s | {phase}",
                )
                write_video(image[np.newaxis], (container, stream))
                if frame % 100 == 0:
                    print(f"  Rendering frame {frame}/{end_frame}", flush=True)
            # Older recordings store only N pre-action states and keep the terminal state in a sidecar.
            if end_frame == metadata["frames"] and not has_terminal and exit_path.exists():
                terminal = json.loads(exit_path.read_text())["state"]
                observation, _, _ = _render_observation(env, terminal, n_render_iterations)
                image = inspection_frame(
                    observation[0], robot.name, resolution,
                    f"{path.stem} | frame {end_frame} | {end_frame / fps:.2f}s | exit snapshot (post-action)",
                )
                write_video(image[np.newaxis], (container, stream))
            Image.fromarray(image).save(video_path.with_suffix(".png"))
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="One raw recording or composed episode HDF5 (replays data/demo_0)")
    parser.add_argument("--config", type=Path, required=True, help="Session YAML")
    parser.add_argument("--output-dir", type=Path, help="Default: memory_bench/recordings/ in the repository")
    parser.add_argument("--check-only", action="store_true", help="Read recording metadata without starting Isaac Sim")
    parser.add_argument("--start-frame", type=int, default=0, help="First source frame to render (inclusive)")
    parser.add_argument("--end-frame", type=int, help="Last source frame to render (exclusive); default: all")
    parser.add_argument("--resolution", type=int, default=480, help="Per-view height; must be a positive even integer")
    parser.add_argument("--n-render-iterations", type=int, default=3)
    parser.add_argument("--overwrite", action="store_true", help="Replace existing inspection outputs")
    args = parser.parse_args()
    if args.resolution <= 0 or args.resolution % 2 or args.n_render_iterations < 1:
        parser.error("--resolution must be positive and even; --n-render-iterations must be positive")
    session = Session.load(args.config)
    path = args.input.expanduser().resolve()
    metadata = recording_metadata(path)
    end_frame = metadata["frames"] if args.end_frame is None else args.end_frame
    if not 0 <= args.start_frame < end_frame <= metadata["frames"]:
        parser.error(f"{path}: require 0 <= start-frame < end-frame <= {metadata['frames']}")
    fps = metadata["interface"]["fps"]
    exit_path = path.with_name(f"{path.stem}_exit.json")
    if metadata["states"] > metadata["frames"]:
        exit_source = "embedded HDF5 terminal state"
    elif exit_path.exists():
        exit_source = str(exit_path)
    else:
        exit_source = "MISSING; final action result unavailable"
    print(
        f"{path}\n  activity={metadata['activity']} | frames={metadata['frames']} | "
        f"fps={fps} | duration={metadata['frames'] / fps:.2f}s\n"
        f"  exit snapshot: {exit_source}",
        flush=True,
    )
    if args.check_only:
        print("Recording metadata loaded. Check motion quality in the rendered video.")
        return
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir else Path(__file__).resolve().parents[1] / "recordings"
    )
    video_path = output_dir / f"{path.stem}_replay.mp4"
    if not args.overwrite:
        for output in (video_path, video_path.with_suffix(".png")):
            if output.exists():
                raise FileExistsError(f"{output} exists; use --overwrite to replace inspection outputs")
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)
    os.environ.setdefault("OMNIGIBSON_HEADLESS", "1")
    import omnigibson as og

    try:
        video_path.parent.mkdir(parents=True, exist_ok=True)
        render_recording(
            path, metadata, video_path, resolution=args.resolution, n_render_iterations=args.n_render_iterations,
            start_frame=args.start_frame, end_frame=end_frame,
        )
        print(f"Saved {video_path}\nSaved {video_path.with_suffix('.png')}", flush=True)
    finally:
        og.shutdown()


if __name__ == "__main__":
    main()
