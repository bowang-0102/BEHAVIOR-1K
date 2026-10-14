"""Render a composed world and export aligned pre-action observations to LeRobot."""

import argparse
import json
from copy import deepcopy

import h5py
import numpy as np

from memory_bench.session import Session
from memory_bench.utils.state import read_json, write_json


def validate_annotations(recipe):
    cursor = 0
    ids = set()
    for segment in recipe["annotations"]:
        start, end = segment["frames"]
        if start != cursor or end <= start or segment["id"] in ids:
            raise ValueError("Annotations must cover every frame once, contiguously")
        cursor = end
        ids.add(segment["id"])
    if cursor != recipe["total_frames"]:
        raise ValueError("Annotation length does not match episode")
    spans = recipe["memory_spans"]
    for span in spans:
        if not 0 < span["key_end_frame"] <= span["query_start_frame"] < cursor:
            raise ValueError("Invalid memory span")


def render_episode(session, recipe):
    import torch as th
    import omnigibson as og
    from omnigibson.controllers import ControllerView, ControlType
    from omnigibson.envs.data_wrapper import _add_recorded_non_kin_states_to_scene_file
    from omnigibson.envs.lerobot_data_wrapper import LeRobotDataWrapper
    from omnigibson.eval.utils.eval_utils import PROPRIOCEPTION_INDICES
    from omnigibson.macros import gm
    from omnigibson.utils.python_utils import recursively_convert_to_torch

    validate_annotations(recipe)
    episode_path = session.paths.resolve(recipe["hdf5"])
    scene = read_json(session.paths.resolve(recipe["replay_scene"]))
    gm.ENABLE_TRANSITION_RULES = False
    gm.VISUAL_ONLY = True
    with h5py.File(episode_path, "r") as stream:
        data, trajectory = stream["data"], stream["data"]["demo_0"]
        config = json.loads(data.attrs["config"])
        _add_recorded_non_kin_states_to_scene_file(scene, [scene])
        config["scene"].update(scene_file=scene, load_room_types=None, load_room_instances=None)
        config["objects"] = []
        config["task"] = {"type": "DummyTask", "include_obs": False}
        config["env"].update(automatic_reset=False, flatten_obs_space=True)
        rendering = session.config["export"]
        proprio_model = {key.lower(): key for key in PROPRIOCEPTION_INDICES}[recipe["interface"]["robot_type"]]
        for robot in config["robots"]:
            robot["name"] = recipe["interface"]["robot_name"]
            robot["obs_modalities"] = rendering["obs_modalities"]
            robot["sensor_config"] = rendering["sensor_config"]
            robot["proprio_obs"] = list(PROPRIOCEPTION_INDICES[proprio_model])
            # Robots cached in the scene are constructed from their initializer,
            # so the environment's robots config does not override their sensors.
            scene["objects_info"]["init_info"][robot["name"]]["args"].update(
                obs_modalities=robot["obs_modalities"],
                sensor_config=robot["sensor_config"],
                proprio_obs=robot["proprio_obs"],
            )
        env = og.Environment(configs=config)
        env.load_observation_space()
        robot = env.scene.robots[0]
        robot.control_enabled = False
        for controller in robot.controllers.values():
            for dof in ControllerView.get_dof_idx(controller[0]).tolist():
                robot.joints[robot.dof_names_ordered[dof]].set_control_type(
                    control_type=ControlType.EFFORT,
                    kp=None,
                    kd=None,
                )
        exporter = LeRobotDataWrapper(
            env=env,
            output_path=session.session_id,
            root_dir=str(session.paths.output_root / "lerobot"),
            overwrite=False,
            only_successes=False,
            flush_every_n_traj=1,
            robot_type=proprio_model,
            task_name=session.session_id,
        )
        expected_index = int(recipe["episode_id"].removeprefix("episode_"))
        if exporter.dataset.meta.total_episodes != expected_index:
            raise ValueError("Export episodes in consecutive ID order; existing dataset has a different length")
        cursor = 0
        snapshots = {}
        for segment_index, segment in enumerate(recipe["annotations"]):
            names = [*segment["objects"]["write"], robot.name]
            for frame in range(*segment["frames"]):
                state = json.loads(trajectory["state_json"][frame])
                frame_names = set(names)
                if frame == segment["frames"][0] and segment_index:
                    frame_names.update(recipe["annotations"][segment_index - 1]["objects"]["write"])
                state["registry"]["object_registry"] = {
                    name: state["registry"]["object_registry"][name] for name in frame_names
                }
                state["registry"]["system_registry"] = {}
                env.scene.load_state(recursively_convert_to_torch(state), serialized=False)
                for obj in env.scene.objects:
                    obj.keep_still()
                env.step(
                    th.zeros(robot.action_dim),
                    n_render_iterations=rendering["n_render_iterations"],
                )
                for _ in range(rendering["n_render_iterations"]):
                    og.sim.render()
                observation, info = env.get_obs()
                if frame == segment["frames"][0]:
                    path = (
                        session.paths.output_root
                        / "lerobot"
                        / session.session_id
                        / "segment_states"
                        / recipe["episode_id"]
                        / f"seg_{segment_index:02d}.json"
                    )
                    write_json(path, {"scene": env.scene.save(as_dict=True), "bindings": segment["bindings"]})
                    snapshots[segment["id"]] = str(
                        path.relative_to(session.paths.output_root / "lerobot" / session.session_id)
                    )
                processed = exporter._process_obs(observation[0], info[0])
                exporter.dataset.add_frame(
                    {
                        **processed,
                        "action": th.as_tensor(trajectory["action"][frame]),
                        "next.reward": th.tensor([trajectory["reward"][frame]]),
                        "next.terminated": th.tensor([trajectory["terminated"][frame]]),
                        "next.truncated": th.tensor([trajectory["truncated"][frame]]),
                        "task": trajectory["instruction"].asstr()[frame],
                    }
                )
                cursor += 1
        exporter.dataset.save_episode()
        exporter._validate_video_fps()
        exporter.close_dataset()
    annotation = deepcopy(recipe)
    for item in annotation["annotations"]:
        item["start_snapshot"] = snapshots[item["id"]]
    annotation_path = (
        session.paths.output_root / "lerobot" / session.session_id / "annotations" / f"{recipe['episode_id']}.json"
    )
    write_json(annotation_path, annotation)
    og.clear()
    return cursor


def readback(session, recipes):
    from lerobot.datasets import LeRobotDataset

    dataset = LeRobotDataset(session.session_id, root=session.paths.output_root / "lerobot" / session.session_id)
    expected = sum(recipe["total_frames"] for recipe in recipes)
    if len(dataset) != expected or dataset.meta.total_episodes != len(recipes):
        raise ValueError("LeRobot episode/frame counts do not match recipes")
    if not dataset.meta.video_keys:
        raise ValueError("Export contains no videos")
    cursor = 0
    for episode_index, recipe in enumerate(recipes):
        validate_annotations(recipe)
        with h5py.File(session.paths.resolve(recipe["hdf5"]), "r") as stream:
            group = stream["data"]["demo_0"]
            for frame in range(recipe["total_frames"]):
                sample = dataset[cursor]
                if int(sample["episode_index"]) != episode_index:
                    raise ValueError("Episode indices are not aligned")
                if not np.allclose(sample["action"].numpy(), group["action"][frame], atol=1e-6, rtol=0):
                    raise ValueError(f"Action mismatch at exported frame {cursor}")
                if sample["task"] != group["instruction"].asstr()[frame]:
                    raise ValueError(f"Instruction mismatch at exported frame {cursor}")
                if abs(float(sample["timestamp"]) - frame / recipe["fps"]) > 1e-4:
                    raise ValueError(f"Timestamp mismatch at exported frame {cursor}")
                for key in dataset.meta.video_keys:
                    if sample[key].numel() == 0:
                        raise ValueError(f"Missing video frame: {key}/{cursor}")
                cursor += 1
    report = {"episodes": len(recipes), "frames": cursor, "videos": dataset.meta.video_keys, "readback": "passed"}
    write_json(session.paths.output_root / "reports" / session.session_id / "export_readback.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--readback-only", action="store_true")
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    index = read_json(session.paths.output_root / "sessions" / session.session_id / "source_pairings.json")
    if len(index) != session.instance_count:
        raise ValueError("Formal episode count does not match session")
    recipes = sorted(
        (read_json(session.paths.resolve(item["recipe"])) for item in index), key=lambda recipe: recipe["episode_id"]
    )
    dataset_root = session.paths.output_root / "lerobot" / session.session_id
    if not args.readback_only:
        if dataset_root.exists():
            raise FileExistsError(f"Refusing to overwrite an existing export: {dataset_root}")
        for recipe in recipes:
            render_episode(session, recipe)
    print(json.dumps(readback(session, recipes), indent=2))


if __name__ == "__main__":
    main()
