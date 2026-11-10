import json

import h5py
import numpy as np

from memory_bench.utils.scene.state import read_json, write_json


def make_scene_snapshot(positions):
    objects = {
        name: {"root_link": {"pos": [value, 0, 0], "ori": [0, 0, 0, 1]}, "non_kin": {}}
        for name, value in positions.items()
    }
    return {
        "robot": {
            "name": "robot",
            "position": [positions["robot"], 0, 0],
            "orientation": [0, 0, 0, 1],
            "joint_positions": [],
            "controlled_joint_indices": [],
        },
        "state": {
            "pos": [0, 0, 0],
            "ori": [0, 0, 0, 1],
            "registry": {"object_registry": objects, "system_registry": {}},
        },
        "objects_info": {
            "init_info": {
                name: {
                    "class_module": "omnigibson.robots.robot"
                    if name == "robot"
                    else "omnigibson.objects.dataset_object",
                    "class_name": "Robot" if name == "robot" else "DatasetObject",
                    "args": {
                        "name": name,
                        "category": name,
                        "model": "test",
                        "scale": [1, 1, 1],
                        "fixed_base": False,
                        "in_rooms": ["kitchen_0"],
                    },
                }
                for name in positions
            }
        },
        "metadata": {"task": {"inst_to_name": {}}},
    }


def write_recording(path, entry, exit_state, activity, terminal=True):
    config = {
        "scene": {"scene_model": "house_double_floor_lower"},
        "env": {"action_frequency": 30, "rendering_frequency": 30, "physics_frequency": 120},
        "robots": [
            {
                "type": "R1Pro",
                "name": "robot",
                "controller_config": {"base": {"name": "test"}},
                "action_normalize": False,
            }
        ],
        "task": {"activity_name": activity},
    }
    interface = {
        "robot_type": "r1pro",
        "robot_name": "robot",
        "controller_config": config["robots"][0]["controller_config"],
        "action_normalize": False,
        "action_dim": 23,
        "action_frequency": 30,
        "fps": 30,
        "physics_frequency": 120,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "x") as stream:
        data = stream.create_group("data")
        data.attrs["config"] = json.dumps(config)
        data.attrs["scene_file"] = json.dumps(entry)
        group = data.create_group("demo_0")
        group.attrs["transitions"] = "{}"
        group.attrs["num_samples"] = 2
        group.create_dataset("action", data=np.full((2, 23), 0.25, dtype=np.float32))
        for field in ("reward", "terminated", "truncated"):
            group.create_dataset(field, data=np.zeros(2))
        states = [entry["state"], entry["state"], exit_state["state"]] if terminal else [entry["state"], entry["state"]]
        group.create_dataset("state_json", data=[json.dumps(state) for state in states], dtype=h5py.string_dtype())
    return interface


def freeze_intervent_sources(session, instances):
    root = session.paths.session_dir
    sources = {}
    for instance_id, refs in instances.items():
        sources[instance_id] = {}
        for key, value in refs.items():
            manifest = read_json(session.paths.resolve(value)) if isinstance(value, str) else value
            source = manifest["source"] if "source" in manifest else manifest
            sources[instance_id][key] = {name: source[name] for name in ("hdf5", "demo")}
    path = root / "intervent_assignments.json"
    write_json(path, {"session_id": session.session_id, "instances": sources})
    return path

