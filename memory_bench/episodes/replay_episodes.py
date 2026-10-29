import argparse
import json
import os
from copy import deepcopy

import h5py
import numpy as np

from memory_bench.session import Session
from memory_bench.utils.scene_state import read_json, write_json


def validate_annotations(recipe):
    """Check contiguous action ranges and memory spans before rendering any frames."""
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
    # External cameras use their own modalities; robot cameras follow the export configuration.
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

    # Map proprioception slots to recorded joints for independent readback validation.
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
            # Readback uses yaw to express recorded base velocities in the robot frame.
            item["yaw_joint"] = int(robot.base_idx[5])
        proprio.append(item)

    return {"robot_name": robot.name, "proprio_dim": robot.proprioception_dim, "proprio": proprio, "videos": videos}


def validate_model_cameras(robot, asset_stage, rendering):
    """Check authored model cameras; live USD can also contain unregistered auxiliary cameras."""
    from omnigibson.sensors.vision_sensor import VisionSensor

    requested = robot._generate_sensor_config(custom_config=rendering["sensor_config"])
    expected = {}
    root = asset_stage.GetDefaultPrim()

    # Enumerate cameras in the source asset, applying the robot's sensor selection rules.
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
                "modalities", VisionSensor.all_modalities.intersection(rendering["obs_modalities"])
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

    if set(expected) != {name for name, sensor in robot.sensors.items() if isinstance(sensor, VisionSensor)}:
        raise ValueError("Live robot cameras differ from configured model camera prims")
    return expected


def _render_observation(env, state, n_render_iterations, *, load_names=None, repair_names=None):
    """Restore recorded kinematics after attachment creation, then render without advancing physics."""
    import torch as th
    import omnigibson as og
    from omnigibson.prims.entity_prim import EntityPrim
    from omnigibson.utils import transform_utils as T
    from omnigibson.utils.python_utils import recursively_convert_to_torch

    state = recursively_convert_to_torch(state)
    object_registry = env.scene.object_registry
    if load_names is None:
        env.scene.load_state(state, serialized=False)
    else:
        # Keep the complete state: missing registry entries can trigger keep_still().
        previous_filter = object_registry._load_filter
        object_registry.set_load_filter(lambda obj: obj.name in load_names and previous_filter(obj))
        try:
            env.scene.load_state(state, serialized=False)
        finally:
            object_registry.set_load_filter(previous_filter)

    # Express recorded object poses in the live scene's coordinate frame before repairing them.
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
        if repair_names is not None and name not in repair_names:
            continue
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

    # Sample proprioception from the restored state and refresh images without stepping physics.
    proprio = env.scene.robots[0].get_proprioception()[0].clone().float()
    for _ in range(n_render_iterations):
        og.sim.render()
    observation, obs_info = env.get_obs()
    return observation, [{"obs_info": info} for info in obs_info], proprio


def replay_episode(session, recipe):
    """Render recorded pre-action states and export observations with their original actions."""
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

    # Recorded states determine the world throughout replay.
    with gm.unlocked():
        gm.ENABLE_TRANSITION_RULES = False
        gm.VISUAL_ONLY = True

    with h5py.File(episode_path, "r") as stream:
        # Reuse the recorded scene and robot configuration with a task-free rendering environment.
        data, trajectory = stream["data"], stream["data"]["demo_0"]
        config = json.loads(data.attrs["config"])
        _add_recorded_non_kin_states_to_scene_file(scene, [scene])
        config["scene"].update(scene_file=scene, load_room_types=None, load_room_instances=None, include_robots=True)
        config["objects"] = []
        config["task"] = {"type": "DummyTask", "include_obs": False}
        config["env"].update(automatic_reset=False, flatten_obs_space=True)

        # Omit viewer-only cameras that contribute no observations or requested modalities.
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

        # Apply export sensors and the standard proprioception layout to the recorded robot.
        rendering = deepcopy(session.export)
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
        if not any("depth" in modality for modality in rendering["obs_modalities"]) and any(
            "depth" in key.rsplit("::", 1)[-1] for key in env.observation_space
        ):
            raise ValueError("Camera depth modalities conflict with session.export.obs_modalities")
        robot = env.scene.robots[0]
        asset_stage = lazy.pxr.Usd.Stage.Open(robot.usd_path)
        expected_cameras = validate_model_cameras(robot, asset_stage, rendering)

        # Disable controllers and joint drives so they cannot alter the restored joint poses.
        robot.control_enabled = False
        for controller in robot.controllers.values():
            for dof in ControllerView.get_dof_idx(controller[0]).tolist():
                robot.joints[robot.dof_names_ordered[dof]].set_control_type(
                    control_type=ControlType.EFFORT,
                    kp=None,
                    kd=None,
                )

        # Append episodes in recipe ID order to keep LeRobot indices aligned with source episodes.
        exporter = LeRobotDataWrapper(
            env=env,
            output_path=session.session_id,
            root_dir=str(session.paths.export_dir.parent),
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

        # Persist live sensor expectations and the physics step used to guard visual replay.
        contract = export_contract(
            robot,
            [(name, sensor) for name, sensor in sensors if isinstance(sensor, VisionSensor)],
            exporter.obs_mapping,
            PROPRIOCEPTION_INDICES[proprio_model],
            recipe["fps"],
            rendering["obs_modalities"],
        )
        contract["model_cameras"] = expected_cameras
        contract["rendering"] = rendering
        contract["replay_mode"] = "recorded_state_without_physics"
        contract["disabled_viewer_cameras"] = disabled_viewers
        physics_step_index = og.sim.current_time_step_index
        contract["physics_step_index"] = physics_step_index
        write_json(session.paths.export_dir / "export_contracts" / f"{recipe['episode_id']}.json", contract)

        cursor = 0
        snapshots = {}
        snapshot_dir = session.paths.export_dir / "segment_states" / recipe["episode_id"]
        for segment_index, segment in enumerate(recipe["annotations"]):
            print(f"Rendering {segment['id']}: frames {segment['frames']}", flush=True)
            names = [*segment["objects"]["write"], robot.name]
            for frame in range(*segment["frames"]):
                # Restore this segment's writes and robot; other objects retain their inherited state.
                state = json.loads(trajectory["state_json"][frame])
                frame_names = set(names)
                if frame == segment["frames"][0] and segment_index:
                    # Carry the previous segment's final writes into the new segment's first frame.
                    frame_names.update(recipe["annotations"][segment_index - 1]["objects"]["write"])
                state["registry"]["object_registry"] = {
                    name: state["registry"]["object_registry"][name] for name in frame_names
                }
                state["registry"]["system_registry"] = {}

                observation, info, proprio = _render_observation(env, state, rendering["n_render_iterations"])
                if og.sim.current_time_step_index != physics_step_index:
                    raise ValueError(f"Visual replay advanced physics at frame {frame}")

                # Save the rendered entry world and bindings for each segment.
                if frame == segment["frames"][0]:
                    path = snapshot_dir / f"seg_{segment_index:02d}.json"
                    write_json(path, {"scene": env.scene.save(as_dict=True), "bindings": segment["bindings"]})
                    snapshots[segment["id"]] = str(path.relative_to(session.paths.output_root))

                # The last exported observation is state[N-1], immediately before action[N-1].
                if frame == recipe["total_frames"] - 1:
                    path = snapshot_dir / "last_observation.json"
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
                    last_observation_snapshot = str(path.relative_to(session.paths.output_root))

                # Pair the rendered pre-action observation with the original action and task labels.
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

    # Keep rendered snapshot references alongside the recipe's source and frame annotations.
    annotation = deepcopy(recipe)
    annotation["last_observation_snapshot"] = last_observation_snapshot
    for item in annotation["annotations"]:
        item["start_snapshot"] = snapshots[item["id"]]
    annotation_path = session.paths.export_dir / "annotations" / f"{recipe['episode_id']}.json"
    write_json(annotation_path, annotation)
    og.clear()
    return cursor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--rgb", action="store_true", help="Export RGB without depth; keep proprioception")
    args = parser.parse_args()

    session = Session.load(args.config)
    if args.rgb:
        session.export["obs_modalities"] = ["proprio", "rgb"]
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)

    # Load every prepared episode in order and require a fresh export directory.
    recipes = [
        read_json(session.paths.recipes_dir / f"episode_{index:08d}.json")
        for index in range(session.instance_count)
    ]
    dataset_root = session.paths.export_dir
    if dataset_root.exists():
        raise FileExistsError(f"Refusing to overwrite an existing export: {dataset_root}")

    for recipe in recipes:
        replay_episode(session, recipe)


if __name__ == "__main__":
    main()
