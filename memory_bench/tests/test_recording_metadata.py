from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from memory_bench.utils.recordings import recording_metadata
from memory_bench.utils.segment_manifest import compatible_interfaces


@pytest.fixture
def recording(tmp_path):
    path = tmp_path / "episode.hdf5"
    config = {
        "robots": [{
            "name": "robot", "model": "r1pro", "action_normalize": False,
            "controller_config": {"arm_left": {"name": "JointController", "motor_type": "position"}},
        }],
        "scene": {"scene_model": "house_double_floor_upper"},
        "task": {"activity_name": "organizing_art_supplies"},
        "env": {"action_frequency": 30, "rendering_frequency": 30, "physics_frequency": 120},
    }
    scene = {
        "objects_info": {"init_info": {"robot": {
            "class_module": "omnigibson.robots.robot", "class_name": "Robot", "args": {"model": "r1pro"},
        }}},
        "state": {"registry": {"object_registry": {"robot": {}}, "system_registry": {}}},
    }
    with h5py.File(path, "w") as stream:
        data = stream.create_group("data")
        data.attrs.update(config=json.dumps(config), scene_file=json.dumps(scene))
        trajectory = data.create_group("demo_0")
        trajectory.create_dataset("action", data=np.zeros((2, 26)))
        trajectory.create_dataset("state", data=np.zeros((3, 1)))
    return path


def test_model_only_recording_matches_legacy_robot_interface_without_rewriting_source(recording):
    modern = recording_metadata(recording)
    assert "type" not in modern["config"]["robots"][0]
    with h5py.File(recording, "r+") as stream:
        config = json.loads(stream["data"].attrs["config"])
        assert "type" not in config["robots"][0]
        config["robots"][0].pop("model")
        config["robots"][0].update(type="R1Pro", name="robot_r1")
        scene = json.loads(stream["data"].attrs["scene_file"])
        initializer = scene["objects_info"]["init_info"].pop("robot")
        initializer.update(class_module="omnigibson.robots.r1pro", class_name="R1Pro")
        initializer["args"].pop("model")
        scene["objects_info"]["init_info"]["robot_r1"] = initializer
        scene["state"]["registry"]["object_registry"]["robot_r1"] = (
            scene["state"]["registry"]["object_registry"].pop("robot")
        )
        stream["data"].attrs.update(config=json.dumps(config), scene_file=json.dumps(scene))
    legacy = recording_metadata(recording)
    assert compatible_interfaces(modern["interface"], legacy["interface"])
    assert legacy["scene"]["objects_info"]["init_info"]["robot_r1"]["args"]["model"] == "r1pro"


def test_recording_with_no_robot_type_or_model_is_rejected(recording):
    with h5py.File(recording, "r+") as stream:
        config = json.loads(stream["data"].attrs["config"])
        config["robots"][0].pop("model")
        stream["data"].attrs["config"] = json.dumps(config)
    with pytest.raises(ValueError, match="Missing robot interface field: type"):
        recording_metadata(recording)


@pytest.mark.parametrize("fixed_base", [True, False])
def test_partial_recording_restores_only_missing_fixed_states(recording, tmp_path, monkeypatch, fixed_base):
    monkeypatch.setitem(sys.modules, "omnigibson.macros", SimpleNamespace(gm=SimpleNamespace(DATA_PATH=tmp_path)))
    with h5py.File(recording, "r+") as stream:
        config = json.loads(stream["data"].attrs["config"])
        config["scene"]["scene_instance"] = "original_template"
        scene = json.loads(stream["data"].attrs["scene_file"])
        scene["objects_info"]["init_info"]["window"] = {"args": {"fixed_base": fixed_base}}
        stream["data"].attrs.update(config=json.dumps(config), scene_file=json.dumps(scene))
    original = recording.read_bytes()
    window = {"root_link": {"pos": [1, 2, 3], "ori": [0, 0, 0, 1]}, "non_kin": {}}
    template = tmp_path / "2026-challenge-task-instances/scenes/house_double_floor_upper/json/original_template.json"
    template.parent.mkdir(parents=True)
    template.write_text(json.dumps({"state": {"registry": {"object_registry": {
        "robot": {"unexpected": True}, "window": window,
    }}}}))
    if fixed_base:
        metadata = recording_metadata(recording)
        objects = metadata["scene"]["state"]["registry"]["object_registry"]
        assert objects == {"robot": {}, "window": window}
    else:
        with pytest.raises(ValueError, match="movable object window"):
            recording_metadata(recording)
    assert recording.read_bytes() == original


@pytest.mark.parametrize("field,value", [
    ("robot_type", "r1"),
    ("controller_config", {"arm_left": {"name": "JointController", "motor_type": "velocity"}}),
    ("action_normalize", True),
    ("action_dim", 25),
    ("action_frequency", 20),
    ("fps", 20),
    ("physics_frequency", 60),
])
def test_robot_interface_changes_remain_incompatible(recording, field, value):
    original = recording_metadata(recording)["interface"]
    changed = deepcopy(original)
    changed[field] = value
    assert not compatible_interfaces(original, changed)
