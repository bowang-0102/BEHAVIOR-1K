import argparse
import json
import os
import sys
import traceback
from copy import deepcopy

import h5py

from memory_bench.session import Session
from memory_bench.utils.episode.recipe import (
    episode_annotations,
    load_instance_recipe,
    require_composed_files,
    validate_annotations,
)
from memory_bench.utils.recording.reader import read_recording_metadata
from memory_bench.utils.scene.light_utils import find_toggle_lights, sync_light_visibility_from_toggles
from memory_bench.utils.scene.state import read_json, write_json, write_json_atomic


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


def _render_observation(env, state, n_render_iterations, *, load_names=None, repair_names=None, lights=None):
    """Restore recorded kinematics after attachment creation, then render without advancing physics."""
    import omnigibson as og
    import torch as th
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
    sync_light_visibility_from_toggles(env.scene, lights=lights)

    # Hydra reads cached hierarchy matrices for cameras attached to articulated links.
    og.sim.fabric_hierarchy.update_world_xforms()

    # Sample proprioception from the restored state and refresh images without stepping physics.
    robot = env.scene.robots[0]
    proprio, proprio_info = robot.get_proprioception()
    proprio = proprio.clone().float()
    for _ in range(n_render_iterations):
        og.sim.render()
    observation, obs_info = env.get_obs(proprioception={(0, robot.name): (proprio, proprio_info)})
    return observation, [{"obs_info": info} for info in obs_info], proprio


def _replay_rows(columns, start, stop):
    """Read bounded blocks of pre-action rows without including the terminal state."""
    block_size = 16
    for first in range(start, stop, block_size):
        end = min(first + block_size, stop)
        block = [column[first:end] for column in columns]
        for offset in range(end - first):
            yield tuple(values[offset] for values in block)


def replay_episode(session, recipe):
    """Render recorded pre-action states and export observations with their original actions."""
    import omnigibson as og
    import omnigibson.lazy as lazy
    import torch as th
    from omnigibson.controllers import ControllerView, ControlType
    from omnigibson.envs.data_wrapper import _add_recorded_non_kin_states_to_scene_file
    from omnigibson.envs.lerobot_data_wrapper import LeRobotDataWrapper
    from omnigibson.eval.utils.eval_utils import PROPRIOCEPTION_INDICES
    from omnigibson.macros import gm

    annotation = episode_annotations(recipe, 0)
    validate_annotations(annotation)
    if annotation["task"] != session.task.query_instruction:
        raise ValueError("Recipe Query instruction differs from the registered MB task")
    output = recipe["output"]
    episode_path = session.paths.resolve(output["hdf5"])
    scene = read_json(session.paths.resolve(output["replay_scene"]))
    interface = read_recording_metadata(episode_path)["interface"]

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
        config["env"]["external_sensors"] = [
            sensor for sensor in external_sensors if sensor.get("include_in_obs", True) or sensor.get("modalities")
        ] or None
        if og.sim is not None:
            config.setdefault("render", {}).update(viewer_width=og.sim.viewer_width, viewer_height=og.sim.viewer_height)

        # Apply export sensors and the standard proprioception layout to the recorded robot.
        rendering = deepcopy(session.export)
        proprio_model = {key.lower(): key for key in PROPRIOCEPTION_INDICES}[interface["robot_type"]]
        for robot in config["robots"]:
            robot["name"] = interface["robot_name"]
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
        has_depth = any("depth" in key.rsplit("::", 1)[-1] for key in env.observation_space)
        if not any("depth" in modality for modality in rendering["obs_modalities"]) and has_depth:
            raise ValueError("Camera depth modalities conflict with session.export.obs_modalities")
        robot = env.scene.robots[0]
        asset_stage = lazy.pxr.Usd.Stage.Open(robot.usd_path)
        validate_model_cameras(robot, asset_stage, rendering)

        # Disable controllers and joint drives so they cannot alter the restored joint poses.
        robot.control_enabled = False
        for controller in robot.controllers.values():
            for dof in ControllerView.get_dof_idx(controller[0]).tolist():
                robot.joints[robot.dof_names_ordered[dof]].set_control_type(
                    control_type=ControlType.EFFORT,
                    kp=None,
                    kd=None,
                )

        # LeRobot assigns consecutive indices even when replaying a single source instance.
        exporter = LeRobotDataWrapper(
            env=env,
            output_path=session.session_id,
            root_dir=str(session.paths.export_dir.parent),
            overwrite=False,
            only_successes=False,
            flush_every_n_traj=1,
            robot_type=proprio_model,
            task_name=annotation["task"],
        )
        episode_index = exporter.dataset.meta.total_episodes
        if exporter.fps != output["fps"]:
            raise ValueError("Live export frequency differs from recipe")

        physics_step_index = og.sim.current_time_step_index
        # Replay keeps the same scene objects and light prims throughout this episode.
        lights = find_toggle_lights(env.scene)
        columns = [trajectory[key] for key in ("state_json", "action", "reward", "terminated", "truncated")]
        cursor = 0
        for segment_index, segment in enumerate(recipe["segments"]):
            print(f"Rendering {segment['id']}: frames {segment['frames']}", flush=True)
            names = [*segment["objects"]["write"], robot.name]
            segment_names = set(names)
            first_names = set(names)
            if segment_index:
                # Carry the previous segment's final writes into this first frame only.
                first_names.update(recipe["segments"][segment_index - 1]["objects"]["write"])
            start, stop = segment["frames"]
            for frame, row in enumerate(_replay_rows(columns, start, stop), start):
                state_json, action, reward, terminated, truncated = row
                # Restore this segment's writes and robot; other objects retain their inherited state.
                state = json.loads(state_json)
                frame_names = first_names if frame == start else segment_names
                state["registry"]["object_registry"] = {
                    name: state["registry"]["object_registry"][name] for name in frame_names
                }
                state["registry"]["system_registry"] = {}

                observation, info, proprio = _render_observation(
                    env, state, rendering["n_render_iterations"], lights=lights,
                )
                if og.sim.current_time_step_index != physics_step_index:
                    raise ValueError(f"Visual replay advanced physics at frame {frame}")

                # Every frame belongs to the complete MB task; segment instructions stay in annotations.
                processed = exporter._process_obs(observation[0], info[0], include_sensor_poses=has_depth)
                processed["observation.state"] = proprio
                exporter.dataset.add_frame(
                    {
                        **processed,
                        "action": th.as_tensor(action),
                        "next.reward": th.tensor([reward]),
                        "next.terminated": th.tensor([bool(terminated)], dtype=th.bool),
                        "next.truncated": th.tensor([bool(truncated)], dtype=th.bool),
                        "task": annotation["task"],
                    }
                )
                cursor += 1
                if cursor % 100 == 0:
                    print(f"Rendered {cursor}/{output['total_frames']} frames", flush=True)

        exporter.dataset.save_episode()
        exporter._validate_video_fps()
        exporter.close_dataset()

    # Training labels remain usable when only the LeRobot directory is distributed.
    annotation["episode_index"] = episode_index
    annotation_path = session.paths.export_dir / "annotations" / f"{recipe['episode_id']}.json"
    write_json(annotation_path, annotation)
    return cursor


def _completed_episode_ids(session):
    root = session.paths.export_dir
    if not root.exists():
        return []
    # An annotation is written after LeRobot finalizes the corresponding episode.
    # An interrupted write must not cause a new episode to reuse an existing index.
    info = read_json(root / "meta" / "info.json")
    episode_ids = {}
    for path in (root / "annotations").glob("episode_*.json"):
        annotation = read_json(path)
        index = annotation["episode_index"]
        if (annotation["session_id"] != session.session_id or annotation["episode_id"] != path.stem
                or index in episode_ids):
            raise ValueError(f"Invalid replay annotation: {path}")
        episode_ids[index] = path.stem
    indices = range(info["total_episodes"])
    if not episode_ids or set(episode_ids) != set(indices):
        print(f"[Replay] Incomplete export: expected {len(indices)} finalized annotation(s) at indices "
              f"0..{len(indices) - 1}, found {sorted(episode_ids)} in {root}", file=sys.stderr, flush=True)
        raise SystemExit(os.EX_DATAERR)
    return [episode_ids[index] for index in indices]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--instance-id", type=int, help="Override composition.instance_id (1-based)")
    selection.add_argument("--instance-range", metavar="FIRST-LAST", help="Select inclusive instance IDs")
    parser.add_argument(
        "--skip-instance", type=int, action="append", default=[], help="Omit a failed instance on range retry",
    )
    parser.add_argument("--rgb", action="store_true", help="Export RGB without depth; keep proprioception")
    args = parser.parse_args()

    session = Session.load(args.config)
    count = session.instance_count
    first, last = 1, count
    if args.instance_range is not None:
        try:
            first, last = map(int, args.instance_range.split("-"))
        except ValueError:
            parser.error("--instance-range expects FIRST-LAST, for example 1-50")
        if not 1 <= first <= last <= count:
            parser.error(f"--instance-range must be within 1-{count} in ascending order")
    else:
        selected = args.instance_id if args.instance_id is not None else session.composition["instance_id"]
        if selected is not None:
            if not 1 <= selected <= count:
                parser.error(f"instance_id must be between 1 and {count}")
            first = last = selected
    if args.skip_instance and args.instance_range is None:
        parser.error("--skip-instance requires --instance-range")
    selected_ids = [i for i in range(first, last + 1) if i not in args.skip_instance]
    if args.rgb:
        session.export["obs_modalities"] = ["proprio", "rgb"]
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.omnigibson_data_root)

    dataset_root = session.paths.export_dir
    marker = dataset_root / "complete.json"
    range_mode = args.instance_range is not None
    completed = []
    invalid_recipes = []
    if not range_mode:
        recipes = [
            read_json(session.paths.recipes_dir / f"episode_{instance_id - 1:08d}.json")
            for instance_id in selected_ids
        ]
        if dataset_root.exists():
            raise FileExistsError(f"Refusing to overwrite an existing export: {dataset_root}")
    else:
        try:
            completed = _completed_episode_ids(session)
        except (FileNotFoundError, json.JSONDecodeError) as error:
            # The batch launcher can quarantine its private shard and rebuild from the recipes.
            print(f"[Replay] Incomplete export: {error}", file=sys.stderr, flush=True)
            raise SystemExit(os.EX_DATAERR) from error
        existing = set(completed)
        recipes = []
        for instance_id in selected_ids:
            try:
                recipe = load_instance_recipe(session, instance_id)
                if recipe is None or recipe["episode_id"] in existing or "output" not in recipe:
                    continue
                if recipe["session_id"] != session.session_id:
                    raise ValueError(f"Recipe belongs to a different session: {recipe['episode_id']}")
                require_composed_files(session, recipe)
            except FileNotFoundError as error:
                print(f"[Replay] Skipping {error}", file=sys.stderr, flush=True)
                continue
            except (OSError, ValueError, KeyError, TypeError) as error:
                print(f"[Replay] Invalid recipe for instance {instance_id}: {error}", file=sys.stderr, flush=True)
                invalid_recipes.append(instance_id)
                continue
            recipes.append(recipe)
        if completed:
            completion = {"session_id": session.session_id, "episode_ids": completed}
            if not marker.is_file() or read_json(marker) != completion:
                write_json_atomic(marker, completion)
        if not recipes:
            if invalid_recipes:
                raise RuntimeError(f"Invalid replay recipes for instances: {invalid_recipes}")
            print("[Replay] No composed episodes need rendering in the selected range.", flush=True)
            return

    import omnigibson as og

    try:
        for index, recipe in enumerate(recipes):
            if range_mode:
                print(f"[START] instance {recipe['instance_id']}", flush=True)
            frames = replay_episode(session, recipe)
            completed.append(recipe["episode_id"])
            if range_mode:
                write_json_atomic(marker, {"session_id": session.session_id, "episode_ids": completed})
            print(f"Exported {recipe['episode_id']}: {frames} frames -> {dataset_root}", flush=True)
            if index + 1 < len(recipes):
                og.clear()
            if range_mode:
                print(f"[END] instance {recipe['instance_id']}", flush=True)
        if not range_mode:
            write_json(marker, {"session_id": session.session_id, "episode_ids": completed})
        if invalid_recipes:
            raise RuntimeError(f"Invalid replay recipes for instances: {invalid_recipes}")
    except Exception:
        # Kit fast shutdown can exit before Python displays the unhandled exception.
        traceback.print_exc()
        sys.stderr.flush()
        if og.app is not None:
            og.app._app.post_quit(1)
        raise
    finally:
        if og.app is not None:
            og.shutdown()


if __name__ == "__main__":
    main()
