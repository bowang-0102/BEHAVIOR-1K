"""Read recording metadata and frame states, and inspect task assets."""

import json
from copy import deepcopy
from pathlib import Path

import h5py

from memory_bench.utils.scene_state import read_json, robot_metadata, scene_objects


def recording_metadata(path, demo=0):
    with h5py.File(path, "r") as stream:
        data = stream["data"]
        config = json.loads(data.attrs["config"])
        scene = json.loads(data.attrs["scene_file"])
        trajectory = data[f"demo_{demo}"]
        robot = config["robots"][0]
        if len(config["robots"]) != 1:
            raise ValueError("Memory Bench currently requires one robot")
        for name in ("type", "controller_config", "action_normalize"):
            if name not in robot:
                raise ValueError(f"Missing robot interface field: {name}")
        if config["scene"]["scene_model"] is None:
            raise ValueError("Missing scene model")
        model = robot["model"] if robot["type"] == "Robot" else robot["type"]
        # The 2026 recordings predate the generic Robot and batched controller schema.
        initializer = scene["objects_info"]["init_info"][robot["name"]]
        if initializer["class_module"] == "omnigibson.robots.r1pro":
            initializer.update(class_module="omnigibson.robots.robot", class_name="Robot")
            initializer["args"]["model"] = "r1pro"
        robot_state = scene["state"]["registry"]["object_registry"][robot["name"]]
        if "controllers" in robot_state:
            robot_state["controller_groups"] = {
                name: {
                    **{key: value for key, value in state.items() if key not in ("goal_is_valid", "goal")},
                    "goal_set": state["goal_is_valid"],
                    "goals": state["goal"],
                }
                for name, state in robot_state.pop("controllers").items()
            }
        interface = {
            "robot_type": model.lower(),
            "robot_name": robot["name"],
            "controller_config": robot["controller_config"],
            "action_normalize": robot["action_normalize"],
            "action_dim": trajectory["action"].shape[1],
            "action_frequency": config["env"]["action_frequency"],
            "fps": config["env"]["rendering_frequency"],
            "physics_frequency": config["env"]["physics_frequency"],
        }
        if interface["action_frequency"] != interface["fps"]:
            raise ValueError("One action per rendered frame is required")
        transitions = json.loads(trajectory.attrs.get("transitions", "{}"))
        if any(transitions.values()):
            raise ValueError("Runtime object/system changes are not supported in the first pipeline")
        if "init_metadata" in trajectory and len(trajectory["init_metadata"]):
            raise ValueError("Per-demo object attribute overrides need an explicit asset merge contract")
        if scene["state"]["registry"]["system_registry"]:
            raise ValueError("Particle systems require a separate task-specific merge contract")
        if trajectory["action"].shape[0] == 0:
            raise ValueError("Recording contains no actions")
        return {
            "config": config,
            "scene": scene,
            "interface": interface,
            "frames": trajectory["action"].shape[0],
            "states": trajectory["state_json"].shape[0] if "state_json" in trajectory else trajectory["state"].shape[0],
            "activity": config["task"].get("activity_name"),
        }


def task_assets(scene, robot_name, extra=()):
    bindings = scene["metadata"]["task"]["inst_to_name"]
    public = {name for key, name in bindings.items() if key.startswith(("agent.", "floor."))}
    names = sorted((set(bindings.values()) | set(extra)) - public - {robot_name})
    return names, bindings


def inspect_source(path, segment, expected_scene, extra=(), demo=0):
    metadata = recording_metadata(path, demo)
    if metadata["activity"] != segment["activity"] or metadata["config"]["scene"]["scene_model"] != expected_scene:
        raise ValueError("Source activity or scene does not match session")
    names, bindings = task_assets(metadata["scene"], metadata["interface"]["robot_name"], extra)
    init = metadata["scene"]["objects_info"]["init_info"]
    rooms = set()
    for name in names:
        declared = init[name]["args"]["in_rooms"]
        rooms.update([declared] if isinstance(declared, str) else declared)
    if rooms != {segment["room_id"]}:
        raise ValueError(f"Actual task rooms {sorted(rooms)} do not match {segment['room_id']}")
    return {
        "hdf5": str(Path(path).resolve()),
        "demo": demo,
        "frames": [0, metadata["frames"]],
        "interface": metadata["interface"],
        "used": names,
        "bindings": bindings,
        "has_terminal_state": metadata["states"] > metadata["frames"],
    }


class RecordingContext:
    """Own one recorded-schema environment while decoding serialized source states."""

    def __init__(self):
        self.env = None
        self.schema = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.env is not None:
            import omnigibson as og

            og.clear()
            self.env = None
            self.schema = None

    def prepare(self, metadata):
        import omnigibson as og
        from omnigibson.envs.data_wrapper import (
            _add_recorded_non_kin_states_to_scene_file,
            _align_scene_object_states_with_recorded_schema,
        )
        from omnigibson.macros import gm

        schema = (
            metadata["scene"]["objects_info"], metadata["config"]["robots"], metadata["config"]["scene"]["scene_model"],
            tuple((name, tuple(state), tuple(state.get("non_kin", {})))
                  for name, state in scene_objects(metadata["scene"]).items()),
        )
        if self.env is not None and schema == self.schema:
            return
        self.close()
        config, scene = deepcopy(metadata["config"]), deepcopy(metadata["scene"])
        _add_recorded_non_kin_states_to_scene_file(scene, [scene])
        config["scene"].update(scene_file=scene, load_room_types=None, load_room_instances=None)
        config["objects"] = []
        config["task"].update(online_object_sampling=False, use_presampled_robot_pose=False)
        config["env"].update(automatic_reset=False, external_sensors=None)
        if og.sim is not None:
            config.setdefault("render", {}).update(viewer_width=og.sim.viewer_width, viewer_height=og.sim.viewer_height)
        for robot in config["robots"]:
            robot["obs_modalities"] = []
        with gm.unlocked():
            gm.ENABLE_TRANSITION_RULES = False
            gm.VISUAL_ONLY = False
            gm.USE_GPU_DYNAMICS = False
            gm.ENABLE_OBJECT_STATES = True
        self.env = og.Environment(configs=config)
        _align_scene_object_states_with_recorded_schema(self.env.scene, metadata["scene"])
        self.schema = deepcopy(schema)


class FrameReader:
    """Read raw actions and pre-action states, with a real post-action terminal state."""

    def __init__(self, path, demo=0, terminal=None, context=None):
        self.metadata = recording_metadata(path, demo)
        self.terminal = terminal
        self.context = RecordingContext() if context is None else context
        self.owns_context = context is None
        self.stream = h5py.File(path, "r")
        self.group = self.stream["data"][f"demo_{demo}"]
        self.serialized = "state_json" not in self.group
        self.states = self.group["state"] if self.serialized else self.group["state_json"]
        self.current = deepcopy(self.metadata["scene"]["state"])
        self.frame = -1
        count = len(self.group["action"])
        if len(self.states) not in (count, count + 1) or (len(self.states) == count and terminal is None):
            self.stream.close()
            raise ValueError("Recording needs N pre-action states and a real post-action terminal state")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def state(self, frame):
        if frame == len(self.states):
            return deepcopy(read_json(self.terminal)["state"])
        if not self.serialized:
            return json.loads(self.states[frame])
        import torch as th
        import omnigibson as og

        self.context.prepare(self.metadata)
        if frame < self.frame:
            self.current = deepcopy(self.metadata["scene"]["state"])
            self.frame = -1
        # Dump filters omit sleeping objects. Carry their last recorded states forward.
        for index in range(self.frame + 1, frame + 1):
            flat = th.as_tensor(self.states[index, : int(self.group["state_size"][index])])
            decoded, consumed = og.sim.deserialize(flat)
            if consumed != len(flat):
                raise ValueError(f"Recorded state schema mismatch at frame {index}")
            scene_objects(self.current).update(decoded[0]["registry"]["object_registry"])
            self.current["registry"]["system_registry"].update(decoded[0]["registry"]["system_registry"])
        self.frame = frame
        return json.loads(json.dumps(self.current, default=lambda value: value.tolist()))

    def snapshot(self, frame):
        if frame == len(self.group["action"]) and self.terminal is not None:
            return read_json(self.terminal)
        scene = deepcopy(self.metadata["scene"])
        scene["state"] = self.state(frame)
        if self.serialized:
            from omnigibson.utils.python_utils import recursively_convert_to_torch

            self.context.env.scene.load_state(recursively_convert_to_torch(deepcopy(scene["state"])), serialized=False)
            scene["robot"] = robot_metadata(self.context.env.scene.robots[0])
        else:
            robot = scene["robot"]
            state = scene_objects(scene)[robot["name"]]
            robot.update(position=state["root_link"]["pos"], orientation=state["root_link"]["ori"])
            if "joint_pos" in state:
                robot["joint_positions"] = state["joint_pos"]
        return scene

    def close(self):
        self.stream.close()
        if self.owns_context:
            self.context.close()
