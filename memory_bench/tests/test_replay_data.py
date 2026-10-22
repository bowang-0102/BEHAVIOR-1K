"""Raw recording inspection preserves source states and includes the real terminal snapshot."""

from contextlib import nullcontext
from copy import deepcopy
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import numpy as np
import pytest
import torch as th

from memory_bench.tools.replay_data import main, render_recording, replay_config
from memory_bench.utils.recordings import recording_metadata


@pytest.fixture
def recording(tmp_path):
    path = tmp_path / "key_01.hdf5"
    config = {
        "robots": [{
            "name": "robot", "type": "Robot", "model": "r1pro", "controller_config": {}, "action_normalize": True,
        }],
        "scene": {"scene_model": "house", "load_room_instances": ["kitchen_0"]},
        "task": {"type": "DummyTask", "activity_name": "key_activity"},
        "env": {"action_frequency": 30, "rendering_frequency": 30, "physics_frequency": 120},
    }
    scene = {
        "objects_info": {"init_info": {"robot": {"class_module": "omnigibson.robots.robot", "args": {}}}},
        "state": {"registry": {"object_registry": {"robot": {"value": [0]}}, "system_registry": {}}},
    }
    with h5py.File(path, "w") as stream:
        data = stream.create_group("data")
        data.attrs.update(config=json.dumps(config), scene_file=json.dumps(scene), collection_complete=True)
        trajectory = data.create_group("demo_0")
        trajectory.attrs.update(num_samples=3, transitions="{}")
        trajectory.create_group("init_metadata")
        trajectory.create_group("rollbacks").create_group("step_1-0")
        for key, value in {
            "action": np.zeros((3, 2)), "state": np.arange(3).reshape(3, 1), "state_size": np.ones(3),
            "reward": np.zeros(3), "terminated": np.zeros(3), "truncated": np.zeros(3),
        }.items():
            trajectory.create_dataset(key, data=value)
    terminal = deepcopy(scene)
    terminal["state"]["registry"]["object_registry"]["robot"]["value"] = [3]
    path.with_name("key_01_exit.json").write_text(json.dumps(terminal))
    session = SimpleNamespace(
        key_ids=["key_1"], query_id="query", scene_model="house",
        segments={"key_1": {"activity": "key_activity"}, "query": {"activity": "query_activity"}},
        paths=SimpleNamespace(activate=Mock()),
    )
    return path, session


def test_inspection_and_config_use_recorded_scene_without_mutating_source(recording):
    path, _ = recording
    metadata = recording_metadata(path)
    original = deepcopy(metadata)
    config = replay_config(metadata, 64)
    assert metadata == original
    assert metadata["frames"] == 3
    assert config["scene"]["load_room_instances"] is None
    assert config["task"] == {"type": "DummyTask", "include_obs": False}
    robot_args = config["scene"]["scene_file"]["objects_info"]["init_info"]["robot"]["args"]
    assert robot_args["obs_modalities"] == ["rgb"]
    assert robot_args["sensor_config"] == config["robots"][0]["sensor_config"]


def test_check_only_does_not_import_simulator(recording, monkeypatch, capsys):
    path, session = recording
    monkeypatch.setattr("memory_bench.tools.replay_data.Session.load", lambda _: session)
    monkeypatch.setitem(sys.modules, "omnigibson", None)
    monkeypatch.setattr(sys, "argv", ["replay_data", str(path), "--config", "session.yaml", "--check-only"])
    main()
    assert "Recording metadata loaded" in capsys.readouterr().out
    assert not (path.parent / "replay").exists()


@pytest.mark.parametrize("start,end,expected", [(0, 3, [0, 1, 2, 3]), (1, 2, [1])])
def test_render_uses_each_pre_action_state_and_real_exit(recording, monkeypatch, start, end, expected):
    path, _ = recording
    metadata = recording_metadata(path)
    scene = SimpleNamespace(robots=[SimpleNamespace(name="robot")])
    env = SimpleNamespace(scene=scene, load_observation_space=Mock())
    decoded_values, rendered_values, encoded = [], [], []

    def deserialize(flat):
        value = int(flat[0])
        decoded_values.append(value)
        return [{"registry": {"object_registry": {"robot": {"value": [value]}}, "system_registry": {}}}], 1

    def render_observation(env, state, iterations):
        value = state["registry"]["object_registry"]["robot"]["value"][0]
        rendered_values.append(value)
        keys = [
            "robot::robot:left_realsense_link:Camera:0::rgb", "robot::robot:zed_link:Camera:0::rgb",
            "robot::robot:right_realsense_link:Camera:0::rgb", "external::inspection_camera::rgb",
        ]
        obs = {key: th.full((64, 192 if key.startswith("external") else 64, 3), value, dtype=th.uint8) for key in keys}
        # State loading can mutate its input; subsequent frames must retain carried state.
        state.clear()
        return [obs], [], None

    og = SimpleNamespace(
        sim=SimpleNamespace(deserialize=deserialize), Environment=Mock(return_value=env),
    )
    monkeypatch.setitem(sys.modules, "omnigibson", og)
    monkeypatch.setitem(sys.modules, "omnigibson.envs.data_wrapper", SimpleNamespace(
        _add_recorded_non_kin_states_to_scene_file=Mock(), _align_scene_object_states_with_recorded_schema=Mock(),
    ))
    monkeypatch.setitem(sys.modules, "omnigibson.macros", SimpleNamespace(gm=SimpleNamespace(unlocked=nullcontext)))
    container, stream = SimpleNamespace(close=Mock(), mux=Mock()), SimpleNamespace(encode=lambda: [])
    monkeypatch.setitem(sys.modules, "omnigibson.eval.utils.obs_utils", SimpleNamespace(
        create_video_writer=lambda *args, **kwargs: (container, stream),
        write_video=lambda images, writer: encoded.append(images[0].copy()),
    ))
    monkeypatch.setattr("memory_bench.episodes.render_episodes._render_observation", render_observation)
    output = path.parent / "inspection.mp4"
    render_recording(path, metadata, output, resolution=64, n_render_iterations=3, start_frame=start, end_frame=end)
    assert decoded_values == list(range(end))
    assert rendered_values == expected
    assert len(encoded) == len(expected) and encoded[0].shape == (128, 192, 3)
    assert output.with_suffix(".png").exists()
    container.close.assert_called_once()
    assert recording_metadata(path)["frames"] == 3
