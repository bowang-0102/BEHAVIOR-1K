from contextlib import nullcontext
from copy import deepcopy
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import numpy as np
import pytest
import torch as th

from memory_bench.episodes.compose_episodes import compose_trajectory
from memory_bench.tests.test_collection import sparse_segment_wrapper as sparse_segment_wrapper
from memory_bench.tests.test_pipeline import material as material
from memory_bench.tools.replay_data import inspection_frame, main, render_recording, replay_config
from memory_bench.utils.recordings import FrameReader, recording_metadata
from memory_bench.utils.paths import SessionPaths
from memory_bench.utils.scene_state import read_json
from memory_bench.utils.segment_manifest import mapped_snapshot


def test_frame_reader_reconstructs_sparse_collection_and_backward_seek(sparse_segment_wrapper, tmp_path):
    wrapper, objects, _ = sparse_segment_wrapper
    wrapper.step(th.zeros(1))
    objects["moving"].is_asleep = False
    objects["moving"].value = 1
    wrapper.step(th.zeros(1))
    objects["moving"].is_asleep = True
    objects["moving"].value = 2
    wrapper.step(th.zeros(1))
    wrapper.step(th.zeros(1))
    wrapper.step(th.zeros(1))
    config = {
        "robots": [{"name": "robot", "type": "Robot", "model": "r1pro", "controller_config": {},
                    "action_normalize": False}],
        "scene": {"scene_model": "house"}, "task": {"activity_name": "test_activity"},
        "env": {"action_frequency": 30, "rendering_frequency": 30, "physics_frequency": 120},
    }
    initial = {"objects_info": {"init_info": {"robot": {"class_module": "omnigibson.robots.robot", "args": {}}}},
               "state": {"registry": {"object_registry": {name: {"value": [0]} for name in objects},
                                      "system_registry": {}}}}
    path = tmp_path / "sparse.hdf5"
    history = wrapper.current_traj_history
    with h5py.File(path, "w") as stream:
        data = stream.create_group("data")
        data.attrs.update(config=json.dumps(config), scene_file=json.dumps(initial))
        trajectory = data.create_group("demo_0")
        trajectory.create_dataset("action", data=np.zeros((len(history) - 1, 1)))
        states = np.zeros((len(history), wrapper.max_state_size))
        for index, row in enumerate(history):
            states[index, :row["state_size"]] = row["state"].numpy()
        trajectory.create_dataset("state", data=states)
        trajectory.create_dataset("state_size", data=[row["state_size"] for row in history])

    context = SimpleNamespace(prepare=Mock())
    with FrameReader(path, context=context) as reader:
        final = reader.state(5)
        assert final["registry"]["object_registry"] == {
            "robot": {"value": [0]}, "moving": {"value": [2]}, "cabinet": {"value": [0]}, "idle": {"value": [0]},
        }
        # A renderer may mutate a returned snapshot; seeking must retain its own carried state.
        final["registry"]["object_registry"]["moving"]["value"][0] = 99
        assert reader.state(2)["registry"]["object_registry"]["moving"] == {"value": [1]}
        assert reader.state(4)["registry"]["object_registry"]["moving"] == {"value": [2]}
        assert reader.state(0) == initial["state"]


@pytest.fixture(params=["serialized", "state_json", "state_json_with_exit"])
def recording(tmp_path, request):
    path = tmp_path / ("key_01.hdf5" if request.param == "serialized" else "episode_00000000.hdf5")
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
    if request.param != "serialized":
        with h5py.File(path, "r+") as stream:
            stream["data"].attrs["format"] = "memory_bench.state_json.v1"
            scene["robot"] = {"name": "robot"}
            stream["data"].attrs.update(config=json.dumps(config), scene_file=json.dumps(scene))
            trajectory = stream["data/demo_0"]
            del trajectory["state"]
            del trajectory["state_size"]
            states = []
            for value in range(4):
                state = deepcopy(scene["state"])
                state["registry"]["object_registry"]["robot"]["value"] = [value]
                states.append(json.dumps(state))
            trajectory.create_dataset("state_json", data=states, dtype=h5py.string_dtype())
    if request.param == "state_json_with_exit":
        terminal["state"]["registry"]["object_registry"]["robot"]["value"] = [99]
    if request.param != "state_json":
        path.with_name(f"{path.stem}_exit.json").write_text(json.dumps(terminal))
    session = SimpleNamespace(
        key_ids=["key_1"], query_id="query", scene_model="house",
        segments={"key_1": {"activity": "key_activity"}, "query": {"activity": "query_activity"}},
        paths=SessionPaths(tmp_path / "data", tmp_path / "raw", tmp_path / "output", "replay", "house"),
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
    assert config["robots"][0]["name"] == "robot"
    robot_args = config["scene"]["scene_file"]["objects_info"]["init_info"]["robot"]["args"]
    assert robot_args["obs_modalities"] == ["rgb"]
    assert robot_args["sensor_config"] == config["robots"][0]["sensor_config"]
    with h5py.File(path, "r") as stream:
        source_robot = json.loads(stream["data"].attrs["config"])["robots"][0]["name"]
        assert source_robot == "robot"


def test_check_only_does_not_import_simulator(recording, monkeypatch, capsys):
    path, session = recording
    monkeypatch.setenv("OMNIGIBSON_DATA_PATH", "previous-data")
    monkeypatch.setenv("OMNIGIBSON_TASK_INSTANCES_DATASET", "previous-instances")
    monkeypatch.setattr("memory_bench.tools.replay_data.Session.load", lambda _: session)
    monkeypatch.setitem(sys.modules, "omnigibson", None)
    monkeypatch.setattr(sys, "argv", ["replay_data", str(path), "--config", "session.yaml", "--check-only"])
    main()
    output = capsys.readouterr().out
    assert "Recording metadata loaded" in output
    assert "MISSING" not in output
    metadata = recording_metadata(path)
    assert ("embedded HDF5 terminal state" in output) == (metadata["states"] > metadata["frames"])
    assert os.environ["OMNIGIBSON_DATA_PATH"] == "previous-data"
    assert os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] == "previous-instances"
    assert not (path.parent / "replay").exists()


def test_composed_episode_uses_its_scene_robot(material):
    session, _, _, segments = material
    segment = segments["intervent_1"]
    scene = read_json(session.paths.resolve(segment["states"]["entry"]))
    scene = mapped_snapshot(scene, "robot", "episode_robot")
    recipe = compose_trajectory(session, scene, [segment], set(), "episode_00000000", {"bindings": {}})
    path = session.paths.resolve(recipe["hdf5"])
    metadata = recording_metadata(path)
    assert metadata["interface"]["robot_name"] == "episode_robot"
    assert metadata["states"] == metadata["frames"] + 1
    assert replay_config(metadata, 64)["robots"][0]["name"] == "episode_robot"
    with h5py.File(path, "r") as stream:
        assert json.loads(stream["data"].attrs["config"])["robots"][0]["name"] == "episode_robot"
        terminal = json.loads(stream["data/demo_0/state_json"][-1])
        assert "episode_robot" in terminal["registry"]["object_registry"]
    with h5py.File(session.paths.resolve(segment["source"]["hdf5"]), "r") as stream:
        assert json.loads(stream["data"].attrs["config"])["robots"][0]["name"] == "robot"


@pytest.mark.parametrize("start,end,expected", [(0, 3, [0, 1, 2, 3]), (1, 2, [1]), (1, 3, [1, 2, 3])])
@pytest.mark.parametrize("change", [
    "ordinary", "tiny_motion", "velocity", "joint", "non_kin", "attachment", "recorded_origin",
    "live_origin", "reset", "assisted", "assisted_active", "sticky", "robot_unchanged",
])
def test_render_uses_each_pre_action_state_and_real_exit(recording, monkeypatch, start, end, expected, change):
    path, _ = recording
    metadata = recording_metadata(path)
    is_json = metadata["states"] > metadata["frames"]
    if is_json:
        with h5py.File(path, "r+") as recording_file:
            states = recording_file["data/demo_0/state_json"]
            for frame in range(len(states)):
                state = json.loads(states[frame])
                state.update(pos=[0, 0, 0], ori=[0, 0, 0, 1])
                objects = state["registry"]["object_registry"]
                if change == "robot_unchanged":
                    objects["robot"]["value"] = [0]
                objects["cupboard"] = {"value": [0]}
                baking_soda = objects["baking_soda"] = {
                    "root_link": {"pos": [0, 0, 0], "lin_vel": [0, 0, 0]}, "joint_pos": [0],
                    "non_kin": {"Temperature": {"temperature": 23}, "AttachedTo": {"attached_obj_uuid": -1}},
                }
                if frame >= 2:
                    if change == "tiny_motion":
                        baking_soda["root_link"]["pos"][0] = 1e-12
                    elif change == "velocity":
                        baking_soda["root_link"]["lin_vel"][0] = 1
                    elif change == "joint":
                        baking_soda["joint_pos"][0] = 1
                    elif change == "non_kin":
                        baking_soda["non_kin"]["Temperature"]["temperature"] = 24
                    elif change == "attachment":
                        baking_soda["non_kin"]["AttachedTo"]["attached_obj_uuid"] = 123
                    elif change == "recorded_origin":
                        state["pos"][0] = 10
                    elif change == "assisted_active":
                        objects["robot"]["ag_obj_constraint_params"] = {"left": {"target_obj": "baking_soda"}}
                    else:
                        baking_soda["value"] = [1]
                states[frame] = json.dumps(state)
    mode = "assisted" if change.startswith("assisted") else "sticky" if change == "sticky" else "physical"
    origin = th.zeros(3)
    scene = SimpleNamespace(
        robots=[SimpleNamespace(name="robot", is_manipulation=True, grasping_mode=mode)],
        get_position_orientation=lambda: (origin, th.tensor([0, 0, 0, 1])),
    )
    env = SimpleNamespace(scene=scene, load_observation_space=Mock(), _current_episodes=th.zeros(1))
    decoded_values, rendered_values, encoded = [], [], []
    restored = []

    def deserialize(flat):
        value = int(flat[0])
        decoded_values.append(value)
        return [{"registry": {"object_registry": {"robot": {"value": [value]}}, "system_registry": {}}}], 1

    def render_observation(env, state, iterations, *, load_names=None, repair_names=None):
        assert load_names == repair_names
        restored.append(load_names)
        value = state["registry"]["object_registry"]["robot"]["value"][0]
        rendered_values.append(value)
        if value == 1 and change == "reset":
            env._current_episodes += 1
        if value == 1 and change == "live_origin":
            origin[0] += 10
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
    monkeypatch.setattr("memory_bench.episodes.replay_episodes._render_observation", render_observation)
    frame_builder = Mock(wraps=inspection_frame)
    monkeypatch.setattr("memory_bench.tools.replay_data.inspection_frame", frame_builder)
    output = path.parent / "inspection.mp4"
    render_recording(path, metadata, output, resolution=64, n_render_iterations=3, start_frame=start, end_frame=end)
    assert decoded_values == ([] if metadata["states"] > metadata["frames"] else list(range(end)))
    assert rendered_values == ([0] * len(expected) if is_json and change == "robot_unchanged" else expected)
    for index, (frame, names) in enumerate(zip(expected, restored)):
        if (
            not is_json or index == 0 or change == "sticky"
            or (frame == 2 and change in {"attachment", "recorded_origin", "live_origin", "reset"})
            or (frame >= 2 and change == "assisted_active")
        ):
            assert names is None
        else:
            changed_baking_soda = frame == 2 and change not in {"recorded_origin", "assisted_active"}
            assert names == ({"robot", "baking_soda"} if changed_baking_soda else {"robot"})
    assert len(encoded) == len(expected) and encoded[0].shape == (128, 192, 3)
    assert output.with_suffix(".png").exists()
    captions = [call.args[3] for call in frame_builder.call_args_list]
    assert all("pre-action" in caption for caption in captions[:end - start])
    assert ("post-action" in captions[-1]) == (end == metadata["frames"])
    container.close.assert_called_once()
    assert recording_metadata(path)["frames"] == 3
