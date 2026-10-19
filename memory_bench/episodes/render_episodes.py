"""Render a composed world and export aligned pre-action observations to LeRobot."""

import argparse
import json
from copy import deepcopy

import h5py
import numpy as np

from memory_bench.session import Session
from memory_bench.utils.scene_state import file_hash, read_json, scene_objects, state_matches, write_json


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


def export_contract(robot, sensors, obs_mapping, proprio_indices, fps, modalities):
    """Record expectations from live sensors and robot indices before writing observations."""
    videos = {}
    for namespace, sensor in sensors:
        requested = sensor.modalities if namespace.startswith("external::") else modalities
        for modality in requested:
            if modality not in sensor.all_modalities or not ("rgb" in modality or "depth" in modality):
                continue
            if modality not in sensor.modalities:
                raise ValueError(f"Requested camera modality is missing: {namespace}/{modality}")
            key = obs_mapping[f"{namespace}::{modality}"]
            videos[key] = {
                "shape": [sensor.image_height, sensor.image_width, 3 if "rgb" in modality else 1],
                "fps": fps,
            }
    if not videos:
        raise ValueError("Export requires configured RGB/depth sensors")
    proprio = []
    for name, slots in proprio_indices.items():
        if not name.endswith(("_qpos", "_qvel")):
            continue
        group, kind = name.rsplit("_", 1)
        if group.startswith("arm_"):
            indices = robot.arm_control_idx[group.removeprefix("arm_")]
        elif group.startswith("gripper_"):
            indices = robot.gripper_control_idx[group.removeprefix("gripper_")]
        elif group == "base":
            indices = robot.base_control_idx
        elif group == "trunk":
            indices = robot.trunk_control_idx
        elif group == "camera":
            indices = robot.camera_control_idx
        elif group == "joint":
            indices = np.arange(len(robot.get_joint_positions()))
        else:
            raise ValueError(f"Unsupported proprioception joint group: {group}")
        item = {"name": name, "slots": [slots.start, slots.stop], "joints": indices.tolist(), "kind": kind}
        if group == "base" and kind == "qvel" and robot.is_holonomic_base:
            item["yaw_joint"] = int(robot.base_idx[5])
        proprio.append(item)
    return {"robot_name": robot.name, "proprio_dim": robot.proprioception_dim, "proprio": proprio, "videos": videos}


def _validate_scene_snapshot(snapshot, state, segment, inherited, robot_name, tolerance, context):
    objects = scene_objects(snapshot["scene"])
    if snapshot["bindings"] != segment["bindings"]:
        raise ValueError(f"{context} bindings mismatch: {segment['id']}")
    for name in inherited:
        if name not in objects or not state_matches(objects[name], scene_objects(state)[name], tolerance):
            raise ValueError(f"{context} inherited state mismatch: {segment['id']}/{name}")
    robot_state = scene_objects(state)[robot_name]
    expected_robot = {key: robot_state[key] for key in ("root_link", "joint_pos")}
    if not state_matches(objects[robot_name], expected_robot, tolerance):
        raise ValueError(f"{context} robot mismatch: {segment['id']}")


def validate_model_cameras(robot, asset_stage, rendering, vision_sensor_cls):
    """Check authored model cameras; live USD can also contain unregistered auxiliary cameras."""
    requested = robot._generate_sensor_config(custom_config=rendering["sensor_config"])
    expected = {}
    root = asset_stage.GetDefaultPrim()
    for link_name, link in robot.links.items():
        source_link = root.GetChild(link_name)
        if not source_link:
            continue
        camera_index = 0
        for prim in source_link.GetChildren():
            if prim.GetPrimTypeInfo().GetTypeName() != "Camera":
                continue
            path = f"{link.prim_path}/{prim.GetName()}"
            if robot._exclude_sensor_names is not None and any(name in path for name in robot._exclude_sensor_names):
                continue
            if robot._include_sensor_names is not None and not any(
                name in path for name in robot._include_sensor_names
            ):
                continue
            link_key = f"{link_name}:Camera:{camera_index}"
            settings = requested.get(link_key, requested["VisionSensor"])
            modalities = settings.get(
                "modalities", vision_sensor_cls.all_modalities.intersection(rendering["obs_modalities"])
            )
            if not modalities:
                continue
            name = f"{robot.name}:{link_key}"
            if name not in robot.sensors:
                raise ValueError(f"Robot model camera was not created: {name}")
            expected[name] = settings.get("sensor_kwargs", {})
            for axis in ("image_height", "image_width"):
                if axis in expected[name] and getattr(robot.sensors[name], axis) != expected[name][axis]:
                    raise ValueError(f"Camera configuration was not applied: {name}/{axis}")
            camera_index += 1
    if set(expected) != {name for name, sensor in robot.sensors.items() if isinstance(sensor, vision_sensor_cls)}:
        raise ValueError("Live robot cameras differ from configured model camera prims")
    return expected


def _render_observation(env, state, n_render_iterations):
    """Restore recorded kinematics after attachment creation, then render without advancing physics."""
    import torch as th
    import omnigibson as og
    from omnigibson.prims.entity_prim import EntityPrim
    from omnigibson.utils import transform_utils as T
    from omnigibson.utils.python_utils import recursively_convert_to_torch

    state = recursively_convert_to_torch(state)
    env.scene.load_state(state, serialized=False)
    registry = state["registry"]
    if "pos" in state:
        rec_pos, rec_ori = state["pos"].reshape(3), state["ori"].reshape(4)
        cur_pos, cur_ori = env.scene.get_position_orientation()
        if not (th.equal(rec_pos, cur_pos) and th.equal(rec_ori, cur_ori)):
            inv_pos, inv_ori = T.invert_pose_transform(rec_pos, rec_ori)
            rel_pos, rel_ori = T.pose_transform(cur_pos, cur_ori, inv_pos, inv_ori)
            registry = env.scene._rebase_registry_state_poses(registry, rel_pos, rel_ori)
    # AttachedTo._attach aligns the child while creating its joint. Restore only
    # kinematics afterward so its recorded pose and the loaded non-kin state coexist.
    for name, object_state in registry["object_registry"].items():
        obj = env.scene.object_registry("name", name)
        EntityPrim._load_state(obj, object_state)
    og.sim.physics_sim_view.update_articulations_kinematic()
    # The cached Fabric update may skip repeated writes at the same physics time.
    og.sim._sim_context._physx_fabric_interface.force_update(og.sim.get_physics_dt(), og.sim.current_time)
    og.sim._refresh_state_caches(dt=0.0)
    for obj in env.scene.objects:
        obj.update_visuals()
    # Hydra reads cached hierarchy matrices for cameras attached to articulated links.
    og.sim.fabric_hierarchy.update_world_xforms()
    proprio = env.scene.robots[0].get_proprioception()[0].clone().float()
    for _ in range(n_render_iterations):
        og.sim.render()
    observation, obs_info = env.get_obs()
    return observation, [{"obs_info": info} for info in obs_info], proprio


def render_episode(session, recipe, *, rgb_only=False):
    import torch as th
    import omnigibson as og
    import omnigibson.lazy as lazy
    from omnigibson.controllers import ControllerView, ControlType
    from omnigibson.envs.data_wrapper import _add_recorded_non_kin_states_to_scene_file
    from omnigibson.envs.lerobot_data_wrapper import LeRobotDataWrapper
    from omnigibson.eval.utils.eval_utils import PROPRIOCEPTION_INDICES
    from omnigibson.macros import gm
    from omnigibson.sensors.vision_sensor import VisionSensor

    validate_annotations(recipe)
    episode_path = session.paths.resolve(recipe["hdf5"])
    scene = read_json(session.paths.resolve(recipe["replay_scene"]))
    with gm.unlocked():
        gm.ENABLE_TRANSITION_RULES = False
        gm.VISUAL_ONLY = True
    with h5py.File(episode_path, "r") as stream:
        data, trajectory = stream["data"], stream["data"]["demo_0"]
        config = json.loads(data.attrs["config"])
        _add_recorded_non_kin_states_to_scene_file(scene, [scene])
        config["scene"].update(scene_file=scene, load_room_types=None, load_room_instances=None, include_robots=True)
        config["objects"] = []
        config["task"] = {"type": "DummyTask", "include_obs": False}
        config["env"].update(automatic_reset=False, flatten_obs_space=True)
        external_sensors = config["env"].get("external_sensors") or []
        for index, sensor in enumerate(external_sensors):
            sensor.setdefault("name", f"external_sensor{index}")
        disabled_viewers = [
            sensor["name"]
            for sensor in external_sensors
            if not sensor.get("include_in_obs", True) and not sensor.get("modalities")
        ]
        config["env"]["external_sensors"] = [
            sensor for sensor in external_sensors if sensor.get("include_in_obs", True) or sensor.get("modalities")
        ] or None
        if og.sim is not None:
            config.setdefault("render", {}).update(viewer_width=og.sim.viewer_width, viewer_height=og.sim.viewer_height)
        rendering = deepcopy(session.config["export"])
        if rgb_only:
            rendering["obs_modalities"] = [name for name in rendering["obs_modalities"] if "depth" not in name]
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
        if rgb_only and any("depth" in key.rsplit("::", 1)[-1] for key in env.observation_space):
            raise ValueError("RGB-only export conflicts with an explicit robot or external depth modality")
        robot = env.scene.robots[0]
        asset_stage = lazy.pxr.Usd.Stage.Open(robot.usd_path)
        expected_cameras = validate_model_cameras(robot, asset_stage, rendering, VisionSensor)
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
        if exporter.fps != recipe["fps"]:
            raise ValueError("Live export frequency differs from recipe")
        sensors = [(f"{robot.name}::{name}", sensor) for name, sensor in robot.sensors.items()]
        if env.external_sensors is not None:
            sensors.extend(
                (f"external::{name}", sensor)
                for name, sensor in env.external_sensors[0].items()
                if env._external_sensors_include_in_obs[name]
            )
        contract = export_contract(
            robot,
            [(name, sensor) for name, sensor in sensors if isinstance(sensor, VisionSensor)],
            exporter.obs_mapping,
            PROPRIOCEPTION_INDICES[proprio_model],
            recipe["fps"],
            rendering["obs_modalities"],
        )
        contract["source_sha256"] = file_hash(episode_path)
        contract["model_cameras"] = expected_cameras
        contract["rendering"] = rendering
        contract["replay_mode"] = "recorded_state_without_physics"
        contract["disabled_viewer_cameras"] = disabled_viewers
        physics_step_index = og.sim.current_time_step_index
        contract["physics_step_index"] = physics_step_index
        write_json(
            session.paths.output_root
            / "lerobot"
            / session.session_id
            / "export_contracts"
            / f"{recipe['episode_id']}.json",
            contract,
        )
        cursor = 0
        snapshots = {}
        for segment_index, segment in enumerate(recipe["annotations"]):
            print(f"Rendering {segment['id']}: frames {segment['frames']}", flush=True)
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
                observation, info, proprio = _render_observation(env, state, rendering["n_render_iterations"])
                if og.sim.current_time_step_index != physics_step_index:
                    raise ValueError(f"Visual replay advanced physics at frame {frame}")
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
                if frame == recipe["total_frames"] - 1:
                    path = (
                        session.paths.output_root
                        / "lerobot"
                        / session.session_id
                        / "segment_states"
                        / recipe["episode_id"]
                        / "last_observation.json"
                    )
                    write_json(
                        path,
                        {
                            "scene": env.scene.save(as_dict=True),
                            "bindings": segment["bindings"],
                            "frame": frame,
                            "segment_id": segment["id"],
                            "state_convention": "pre_action",
                        },
                    )
                    last_observation_snapshot = str(
                        path.relative_to(session.paths.output_root / "lerobot" / session.session_id)
                    )
                processed = exporter._process_obs(observation[0], info[0])
                processed["observation.state"] = proprio
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
                if cursor % 100 == 0:
                    print(f"Rendered {cursor}/{recipe['total_frames']} frames", flush=True)
        exporter.dataset.save_episode()
        exporter._validate_video_fps()
        exporter.close_dataset()
    annotation = deepcopy(recipe)
    annotation["last_observation_snapshot"] = last_observation_snapshot
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

    dataset_root = session.paths.output_root / "lerobot" / session.session_id
    dataset = LeRobotDataset(session.session_id, root=dataset_root)
    metadata = read_json(dataset_root / "meta" / "omnigibson.json")
    if not metadata["omnigibson_git_hash"]:
        raise ValueError("Export is missing the OmniGibson git hash")
    expected = sum(recipe["total_frames"] for recipe in recipes)
    if len(dataset) != expected or dataset.meta.total_episodes != len(recipes):
        raise ValueError("LeRobot episode/frame counts do not match recipes")
    if not dataset.meta.video_keys:
        raise ValueError("Export contains no videos")
    cursor = 0
    video_fps_probes = []
    last_observations = []
    for episode_index, recipe in enumerate(recipes):
        validate_annotations(recipe)
        annotation = read_json(dataset_root / "annotations" / f"{recipe['episode_id']}.json")
        validate_annotations(annotation)
        if (
            annotation["total_frames"] != recipe["total_frames"]
            or annotation["memory_spans"] != recipe["memory_spans"]
            or [
                {key: value for key, value in item.items() if key != "start_snapshot"}
                for item in annotation["annotations"]
            ]
            != recipe["annotations"]
        ):
            raise ValueError("Exported annotations differ from the recipe")
        contract = read_json(dataset_root / "export_contracts" / f"{recipe['episode_id']}.json")
        if file_hash(session.paths.resolve(recipe["hdf5"])) != contract["source_sha256"]:
            raise ValueError("Canonical episode changed after rendering")
        if set(dataset.meta.video_keys) != set(contract["videos"]):
            raise ValueError("Export video keys differ from the live sensor contract")
        if dataset.meta.fps != recipe["fps"]:
            raise ValueError("Dataset metadata FPS differs from recipe")
        for key, video in contract["videos"].items():
            camera = key.split(".", 2)[2]
            intrinsics = np.asarray(metadata["cam_intrinsics"].get(camera, []), dtype=float)
            if (
                intrinsics.shape != (3, 3)
                or not np.isfinite(intrinsics).all()
                or intrinsics[0, 0] <= 0
                or intrinsics[1, 1] <= 0
                or not np.isclose(intrinsics[2, 2], 1)
            ):
                raise ValueError(f"Missing or invalid camera intrinsics: {camera}")
            feature = dataset.meta.features[key]
            if list(feature["shape"]) != video["shape"]:
                raise ValueError(f"Video metadata shape mismatch: {key}")
            video_fps = (feature.get("info") or {}).get("video.fps")
            if video_fps is None:
                from lerobot.datasets.video_utils import get_video_info

                video_path = dataset_root / dataset.meta.get_video_file_path(episode_index, key)
                video_fps = get_video_info(video_path).get("video.fps")
                video_fps_probes.append(
                    {"episode_index": episode_index, "key": key, "path": str(video_path), "fps": video_fps}
                )
            if video_fps != video["fps"]:
                raise ValueError(f"Video FPS mismatch: {key}, got {video_fps}, expected {video['fps']}")
        boundaries = {item["frames"][0]: item for item in annotation["annotations"]}
        inherited = set()
        tolerance = deepcopy(session.config["validation"]["state_tolerances"])
        tolerance["ignored_fields"] = [*tolerance["ignored_fields"], "lin_vel", "ang_vel", "joint_vel"]
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
                state = json.loads(group["state_json"][frame])
                robot_state = scene_objects(state)[contract["robot_name"]]
                proprio = sample["observation.state"].numpy()
                if proprio.shape != (contract["proprio_dim"],):
                    raise ValueError(f"Proprioception shape mismatch at exported frame {cursor}")
                for item in contract["proprio"]:
                    field = "joint_pos" if item["kind"] == "qpos" else "joint_vel"
                    expected_values = np.asarray(robot_state[field])[item["joints"]]
                    if "yaw_joint" in item:
                        yaw = robot_state["joint_pos"][item["yaw_joint"]]
                        c, s = np.cos(yaw), np.sin(yaw)
                        x, y, z = expected_values
                        expected_values = np.asarray([c * x + s * y, -s * x + c * y, z])
                    actual = proprio[slice(*item["slots"])]
                    if not np.allclose(actual, expected_values, atol=1e-5, rtol=0):
                        raise ValueError(f"Proprioception mismatch for {item['name']} at exported frame {cursor}")
                for key, video in contract["videos"].items():
                    height, width, channels = video["shape"]
                    if key not in sample or tuple(sample[key].shape) != (channels, height, width):
                        raise ValueError(f"Video frame shape/missing mismatch: {key}/{cursor}")
                if frame in boundaries:
                    segment = boundaries[frame]
                    inherited.update(segment["objects"]["write"])
                    snapshot = read_json(dataset_root / segment["start_snapshot"])
                    _validate_scene_snapshot(
                        snapshot, state, segment, inherited, contract["robot_name"], tolerance, "Start snapshot"
                    )
                if frame == recipe["total_frames"] - 1:
                    last_segment = annotation["annotations"][-1]
                    snapshot = read_json(dataset_root / annotation["last_observation_snapshot"])
                    if (
                        snapshot["frame"] != frame
                        or snapshot["segment_id"] != last_segment["id"]
                        or snapshot["state_convention"] != "pre_action"
                    ):
                        raise ValueError("Last observation snapshot must describe pre-action state[N-1]")
                    _validate_scene_snapshot(
                        snapshot,
                        state,
                        last_segment,
                        inherited,
                        contract["robot_name"],
                        tolerance,
                        "Last observation snapshot",
                    )
                    last_observations.append(
                        {
                            "episode_id": recipe["episode_id"],
                            "frame": frame,
                            "snapshot": annotation["last_observation_snapshot"],
                        }
                    )
                cursor += 1
                if cursor % 1000 == 0:
                    print(f"Read back {cursor}/{expected} frames", flush=True)
    report = {
        "episodes": len(recipes),
        "frames": cursor,
        "videos": dataset.meta.video_keys,
        "video_fps_probes": video_fps_probes,
        "last_observation_snapshots": last_observations,
        "camera_intrinsics": sorted(metadata["cam_intrinsics"]),
        "omnigibson_git_hash": metadata["omnigibson_git_hash"],
        "readback": "passed",
    }
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
