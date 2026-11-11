import json
import sys
from contextlib import nullcontext
from copy import deepcopy
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import h5py
import numpy as np
import pytest
import torch as th

from memory_bench.tests.helpers import make_scene_snapshot, write_recording
from memory_bench.utils.recording.reader import (
    FrameReader,
    RecordingContext,
    compatible_interfaces,
    read_recording_metadata,
)
from memory_bench.utils.recording.segments import check_frame
from memory_bench.utils.scene.state import scene_objects, write_json


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
    modern = read_recording_metadata(recording)
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
    legacy = read_recording_metadata(recording)
    assert compatible_interfaces(modern["interface"], legacy["interface"])
    assert legacy["scene"]["objects_info"]["init_info"]["robot_r1"]["args"]["model"] == "r1pro"


def test_recording_with_no_robot_type_or_model_is_rejected(recording):
    with h5py.File(recording, "r+") as stream:
        config = json.loads(stream["data"].attrs["config"])
        config["robots"][0].pop("model")
        stream["data"].attrs["config"] = json.dumps(config)
    with pytest.raises(ValueError, match="Missing robot interface field: type"):
        read_recording_metadata(recording)


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
        metadata = read_recording_metadata(recording)
        objects = metadata["scene"]["state"]["registry"]["object_registry"]
        assert objects == {"robot": {}, "window": window}
    else:
        with pytest.raises(ValueError, match="movable object window"):
            read_recording_metadata(recording)
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
    original = read_recording_metadata(recording)["interface"]
    changed = deepcopy(original)
    changed[field] = value
    assert not compatible_interfaces(original, changed)


def test_missing_terminal_state_only_blocks_final_frame(tmp_path):
    path = tmp_path / "source.hdf5"
    state = make_scene_snapshot({"robot": 0})
    write_recording(path, state, state, "test", terminal=False)
    with FrameReader(path) as reader:
        assert reader.state(0) == state["state"]
        assert reader.snapshot(0) == state
        assert reader.state(1) == state["state"]
        with pytest.raises(ValueError, match="terminal"):
            reader.state(2)
        with pytest.raises(ValueError, match="terminal"):
            reader.snapshot(2)


@pytest.mark.parametrize("count", [1, 4])
def test_rejects_invalid_recorded_state_count(tmp_path, count):
    path = tmp_path / "source.hdf5"
    state = make_scene_snapshot({"robot": 0})
    write_recording(path, state, state, "test")
    with h5py.File(path, "r+") as stream:
        group = stream["data/demo_0"]
        del group["state_json"]
        group.create_dataset("state_json", data=[json.dumps(state["state"])] * count)
    with pytest.raises(ValueError, match="pre-action states"):
        FrameReader(path)


def test_legacy_recording_schema_is_upgraded_without_changing_source(tmp_path):
    scene = make_scene_snapshot({"robot": 0})
    scene["objects_info"]["init_info"]["robot"].update(class_module="omnigibson.robots.r1pro", class_name="R1Pro")
    scene_objects(scene)["robot"]["controllers"] = {"base": {"goal_is_valid": True, "goal": {"target": [0, 0, 0]}}}
    path = tmp_path / "legacy.hdf5"
    write_recording(path, scene, scene, "test")
    original = path.read_bytes()
    metadata = read_recording_metadata(path)
    initializer = metadata["scene"]["objects_info"]["init_info"]["robot"]
    assert initializer["class_name"] == "Robot" and initializer["args"]["model"] == "r1pro"
    assert scene_objects(metadata["scene"])["robot"]["controller_groups"]["base"]["goal_set"]
    assert path.read_bytes() == original


def test_raw_reader_uses_real_terminal_and_ignores_rollback_branch(tmp_path):
    entry, exit_state = make_scene_snapshot({"robot": 0, "toy": 0}), make_scene_snapshot({"robot": 0, "toy": 1})
    source, terminal = tmp_path / "key_01.hdf5", tmp_path / "key_01_exit.json"
    write_recording(source, entry, exit_state, "test", terminal=False)
    write_json(terminal, exit_state)
    with h5py.File(source, "a") as stream:
        stream["data/demo_0"].create_group("rollbacks").create_dataset("failed", data=[99])
    before = source.read_bytes()
    with FrameReader(source, terminal=terminal) as reader:
        assert reader.state(0) == entry["state"]
        assert reader.state(2) == exit_state["state"]
        assert reader.snapshot(2) == exit_state
    assert source.read_bytes() == before
    assert {path.name for path in tmp_path.iterdir()} == {"key_01.hdf5", "key_01_exit.json"}


@pytest.mark.parametrize("change", ("state_order", "model"))
def test_raw_serialized_reader_reuses_schema_and_decodes_without_output(tmp_path, monkeypatch, change):
    scene = make_scene_snapshot({"robot": 0, "toy": 0})
    fake_og = ModuleType("omnigibson")
    fake_og.Environment = Mock(return_value=SimpleNamespace(scene=object()))
    fake_og.clear = Mock()
    fake_og.sim = SimpleNamespace(viewer_width=640, viewer_height=480,
                                  deserialize=Mock(return_value=([scene["state"]], 1)))
    wrapper = ModuleType("omnigibson.envs.data_wrapper")
    wrapper._add_recorded_non_kin_states_to_scene_file = lambda *_: None
    wrapper._align_scene_object_states_with_recorded_schema = lambda *_: None
    macros = ModuleType("omnigibson.macros")
    macros.gm = SimpleNamespace(unlocked=lambda: nullcontext())
    monkeypatch.setitem(sys.modules, "omnigibson", fake_og)
    monkeypatch.setitem(sys.modules, "omnigibson.envs.data_wrapper", wrapper)
    monkeypatch.setitem(sys.modules, "omnigibson.macros", macros)
    with RecordingContext() as context:
        for index, key in enumerate(("key_01", "query", "key_02")):
            source = tmp_path / f"{key}.hdf5"
            recorded_scene = deepcopy(scene)
            if index < 2:
                for initializer in recorded_scene["objects_info"]["init_info"].values():
                    initializer["args"]["expected_file_hash"] = f"recorded-asset-{index}"
            write_recording(source, recorded_scene, recorded_scene, key)
            with h5py.File(source, "a") as stream:
                group = stream["data/demo_0"]
                del group["state_json"]
                group.create_dataset("state", data=[[0.0], [0.0], [0.0]])
                group.create_dataset("state_size", data=[1, 1, 1])
            with FrameReader(source, context=context) as reader:
                assert reader.state(0) == scene["state"]
                assert reader.state(2) == scene["state"]
                assert reader.metadata["scene"]["objects_info"] == recorded_scene["objects_info"]
    assert fake_og.Environment.call_count == 1
    assert fake_og.Environment.call_args.kwargs["configs"]["scene"]["include_robots"] is True
    loaded_scene = fake_og.Environment.call_args.kwargs["configs"]["scene"]["scene_file"]
    assert loaded_scene["objects_info"]["init_info"]["robot"]["args"]["expected_file_hash"] == "recorded-asset-0"
    assert fake_og.clear.call_count == 1
    assert len(list(tmp_path.iterdir())) == 3

    with RecordingContext() as context:
        with FrameReader(tmp_path / "key_01.hdf5") as reader:
            metadata = reader.metadata
        first = {**metadata, "scene": make_scene_snapshot({"robot": 0, "toy": 0})}
        second = {**metadata, "scene": make_scene_snapshot({"robot": 0, "toy": 0})}
        if change == "state_order":
            scene_objects(first["scene"])["toy"]["non_kin"] = {"Open": {}, "Temperature": {}}
            scene_objects(second["scene"])["toy"]["non_kin"] = {"Temperature": {}, "Open": {}}
        else:
            second["scene"]["objects_info"]["init_info"]["toy"]["args"]["model"] = "different"
        context.prepare(first)
        context.prepare(second)
    assert fake_og.Environment.call_count == 3
    assert fake_og.clear.call_count == 3


def test_serialized_reader_carries_filtered_objects_and_can_rewind(tmp_path, monkeypatch):
    initial = make_scene_snapshot({"robot": 0, "toy": 0})
    source = tmp_path / "intervent_01.hdf5"
    write_recording(source, initial, initial, "test")
    with h5py.File(source, "a") as stream:
        group = stream["data/demo_0"]
        del group["state_json"]
        group.create_dataset("state", data=[[0.0], [1.0], [2.0]])
        group.create_dataset("state_size", data=[1, 1, 1])

    def deserialize(flat):
        index = int(flat[0])
        objects = (
            {"toy": scene_objects(make_scene_snapshot({"robot": 0, "toy": 7}))["toy"]}
            if index == 1 else {"robot": scene_objects(initial)["robot"]}
        )
        return [{"registry": {"object_registry": objects, "system_registry": {}}}], 1

    monkeypatch.setitem(sys.modules, "omnigibson", SimpleNamespace(sim=SimpleNamespace(deserialize=deserialize)))
    context = SimpleNamespace(prepare=lambda metadata: None)
    with FrameReader(source, context=context) as reader:
        assert scene_objects(reader.state(2))["toy"]["root_link"]["pos"][0] == 7
        assert scene_objects(reader.state(0))["toy"]["root_link"]["pos"][0] == 0
        assert scene_objects(reader.state(1))["toy"]["root_link"]["pos"][0] == 7


@pytest.mark.parametrize("role", ["intervent_task", "bridge_task", "key_task", "query_task"])
def test_relaxed_writes_still_protect_memory(role):
    baseline = make_scene_snapshot({"robot": 0, "item": 0, "cabinet": 0, "toy": 0})
    segment = {
        "identity": {"id": "segment"}, "task": {"role": role},
        "interface": {"robot_name": "robot"}, "objects": {"write": ["item"]},
    }
    changed = deepcopy(baseline)
    scene_objects(changed)["cabinet"]["root_link"]["pos"][0] = 1
    with pytest.raises(ValueError, match="memory"):
        check_frame(segment, changed, baseline, {"item", "cabinet"})
    changed = deepcopy(baseline)
    scene_objects(changed)["item"]["root_link"]["pos"][0] = 1
    if role in ("intervent_task", "bridge_task"):
        with pytest.raises(ValueError, match="memory"):
            check_frame(segment, changed, baseline, {"item", "cabinet"})
    else:
        assert check_frame(segment, changed, baseline, {"item", "cabinet"}) == {"item"}
    changed = deepcopy(baseline)
    scene_objects(changed)["toy"]["root_link"]["pos"][0] = 1
    if role == "bridge_task":
        with pytest.raises(ValueError, match="Undeclared state changes"):
            check_frame(segment, changed, baseline, {"item", "cabinet"})
    else:
        assert check_frame(segment, changed, baseline, {"item", "cabinet"}) == {"toy"}


@pytest.mark.parametrize("change", ["add_object", "remove_object", "system"])
def test_relaxed_writes_still_reject_topology_changes(change):
    baseline = make_scene_snapshot({"robot": 0, "toy": 0})
    segment = {
        "identity": {"id": "query"}, "task": {"role": "query_task"},
        "interface": {"robot_name": "robot"}, "objects": {"write": []},
    }
    changed = deepcopy(baseline)
    if change == "add_object":
        scene_objects(changed)["new"] = deepcopy(scene_objects(changed)["toy"])
    elif change == "remove_object":
        del scene_objects(changed)["toy"]
    else:
        changed["state"]["registry"]["system_registry"]["water"] = {}
    with pytest.raises(ValueError, match="topology or particle systems"):
        check_frame(segment, changed, baseline, set())


def test_sparse_segment_preserves_last_sleeping_and_nonphysical_states(sparse_segment_wrapper):
    wrapper, objects, sim = sparse_segment_wrapper
    registry = wrapper.env.scene.object_registry
    original_filter = registry._dump_filter
    objects["moving"].is_asleep = False
    wrapper.step(th.zeros(1))
    assert set(scene_objects(sim.deserialize(wrapper.current_traj_history[-1]["state"])[0][0])) == set(objects)

    objects["moving"].is_asleep = True
    objects["moving"].value = 2
    objects["cabinet"].kinematic_only = True
    objects["cabinet"].value = 3
    wrapper.env.scene.updated_state_objects.add(objects["cabinet"])
    wrapper.step(th.ones(1))
    recorded = scene_objects(sim.deserialize(wrapper.current_traj_history[-1]["state"])[0][0])
    assert recorded == {"robot": {"value": [0]}, "moving": {"value": [2]}, "cabinet": {"value": [3]}}
    assert registry._dump_filter is original_filter

    wrapper.env.scene.updated_state_objects.clear()
    wrapper.step(th.zeros(1))
    wrapper.step(th.zeros(1))
    recorded = scene_objects(sim.deserialize(wrapper.current_traj_history[-1]["state"])[0][0])
    assert recorded == {"robot": {"value": [0]}}
    assert registry._dump_filter is original_filter
    assert wrapper.current_traj_history[-1]["state_size"] == 3
    assert wrapper.max_state_size == 1 + 2 * len(objects)


def test_sparse_bridge_recording_round_trips_sleeping_objects(sparse_segment_wrapper, tmp_path, monkeypatch):
    from omnigibson.envs import Environment

    from memory_bench.utils.recording.reader import FrameReader
    from memory_bench.utils.recording.wrappers import SparseHDF5CollectionWrapper

    segment, objects, sim = sparse_segment_wrapper
    scene = segment.env.scene
    config = {
        "robots": [{"name": "robot", "type": "Robot", "model": "r1pro", "controller_config": {},
                    "action_normalize": False}],
        "scene": {"scene_model": "house"}, "task": {"activity_name": "bridge_navigation"},
        "env": {"action_frequency": 30, "rendering_frequency": 30, "physics_frequency": 120},
    }

    def make_scene_snapshot():
        return {
            "objects_info": {"init_info": {"robot": {"class_module": "omnigibson.robots.robot", "args": {}}}},
            "state": {"registry": {"object_registry": {
                name: {"value": [obj.value]} for name, obj in objects.items()
            }, "system_registry": {}}},
        }

    scene.save = lambda **kwargs: json.dumps(make_scene_snapshot())
    for obj in objects.values():
        obj.wake = lambda obj=obj: setattr(obj, "is_asleep", False)
    for name in ("add_callback_on_system_init", "add_callback_on_system_clear",
                 "add_callback_on_add_obj", "add_callback_on_remove_obj"):
        monkeypatch.setattr(sim, name, Mock(), raising=False)
    env = Mock(spec=Environment, num_envs=1, scene=scene, config=config, env_config=config["env"],
               task=SimpleNamespace(success=th.tensor([False])), step=segment.env.step, reset=segment.env.reset)
    path = tmp_path / "bridge.hdf5"
    wrapper = SparseHDF5CollectionWrapper(env, str(path), only_successes=False, viewport_camera_path=None)
    try:
        wrapper.reset()
        assert set(scene_objects(sim.deserialize(wrapper.current_traj_history[0]["state"])[0][0])) == set(objects)
        for obj in objects.values():
            obj.is_asleep = True
        objects["moving"].is_asleep = False
        wrapper.step(th.zeros(1))

        objects["moving"].value = 7
        objects["moving"].is_asleep = True
        objects["cabinet"].kinematic_only = True
        objects["cabinet"].value = 3
        scene.updated_state_objects.add(objects["cabinet"])
        wrapper.step(th.zeros(1))
        for _ in range(3):
            wrapper.step(th.zeros(1))
        assert wrapper.current_traj_history[-1]["state_size"] == 3
        # Boundary snapshots remain complete even after sparse step recording.
        assert set(scene_objects(sim.deserialize(sim.dump_state())[0][0])) == set(objects)
        terminal = path.with_name("bridge_exit.json")
        terminal.write_text(json.dumps(make_scene_snapshot()))
        wrapper.save_data()
    finally:
        if wrapper.hdf5_file.id.valid:
            wrapper.hdf5_file.close()

    with FrameReader(path, terminal=terminal, context=SimpleNamespace(prepare=Mock())) as reader:
        assert scene_objects(reader.state(0))["moving"] == {"value": [0]}
        for frame in range(2, 6):
            restored = scene_objects(reader.state(frame))
            assert set(restored) == set(objects)
            assert restored["moving"] == {"value": [7]}
            assert restored["cabinet"] == {"value": [3]}
            assert restored["idle"] == {"value": [0]}


def test_sparse_segment_restores_filter_after_serialization_failure(sparse_segment_wrapper, monkeypatch):
    wrapper, objects, sim = sparse_segment_wrapper
    wrapper.step(th.zeros(1))
    original_filter = wrapper.env.scene.object_registry._dump_filter
    original_active = wrapper._previous_active_objects.copy()
    objects["moving"].is_asleep = False
    dump = sim.dump_state
    monkeypatch.setattr(sim, "dump_state", Mock(side_effect=RuntimeError("serialization failed")))
    with pytest.raises(RuntimeError, match="serialization failed"):
        wrapper.step(th.zeros(1))
    assert wrapper.env.scene.object_registry._dump_filter is original_filter
    assert wrapper._previous_active_objects == original_active
    monkeypatch.setattr(sim, "dump_state", dump)
    wrapper.step(th.zeros(1))
    assert "moving" in scene_objects(sim.deserialize(wrapper.current_traj_history[-1]["state"])[0][0])


def test_sparse_segment_records_nonphysical_updates_during_simulation(sparse_segment_wrapper):
    wrapper, objects, sim = sparse_segment_wrapper
    wrapper.step(th.zeros(1))
    wrapper.step(th.zeros(1))
    cabinet = objects["cabinet"]
    cabinet.kinematic_only = True

    def update_state():
        cabinet.value = 4
        wrapper.env.scene.updated_state_objects.add(cabinet)

    wrapper.env.step_update = update_state
    wrapper.step(th.zeros(1))
    recorded = scene_objects(sim.deserialize(wrapper.current_traj_history[-1]["state"])[0][0])
    assert recorded == {"robot": {"value": [0]}, "cabinet": {"value": [4]}}


def test_sparse_segment_new_segment_and_rollback_restart_with_full_state(sparse_segment_wrapper, tmp_path):
    wrapper, objects, sim = sparse_segment_wrapper
    assert set(scene_objects(sim.deserialize(wrapper.current_traj_history[0]["state"])[0][0])) == set(objects)
    wrapper.step(th.zeros(1))
    wrapper.step(th.zeros(1))
    wrapper.update_checkpoint()
    assert set(scene_objects(sim.deserialize(wrapper.current_traj_history[-1]["state"])[0][0])) == set(objects)
    objects["idle"].value = 8
    objects["idle"].is_asleep = False
    wrapper.step(th.zeros(1))
    wrapper.rollback_to_checkpoint()
    assert objects["idle"].value == 0
    branch = wrapper.checkpoint_rollback_trajs[2][0]["step_data"]
    assert set(scene_objects(sim.deserialize(branch[0]["state"])[0][0])) == set(objects)
    wrapper.step(th.zeros(1))
    assert set(scene_objects(sim.deserialize(wrapper.current_traj_history[-1]["state"])[0][0])) == set(objects)
    wrapper.discard_segment()
    objects["idle"].value = 9
    wrapper.begin_segment(tmp_path / "next.hdf5", {}, {"activity_name": "next_activity"})
    assert scene_objects(sim.deserialize(wrapper.current_traj_history[0]["state"])[0][0])["idle"] == {"value": [9]}
    wrapper.step(th.zeros(1))
    assert set(scene_objects(sim.deserialize(wrapper.current_traj_history[-1]["state"])[0][0])) == set(objects)
    wrapper.discard_segment()
    wrapper.reset()
    assert wrapper._previous_active_objects is None


@pytest.mark.parametrize("terminal", [True, False])
def test_frame_reader_reconstructs_sparse_collection_and_backward_seek(sparse_segment_wrapper, tmp_path, terminal):
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
    history = wrapper.current_traj_history if terminal else wrapper.current_traj_history[:-1]
    with h5py.File(path, "w") as stream:
        data = stream.create_group("data")
        data.attrs.update(config=json.dumps(config), scene_file=json.dumps(initial))
        trajectory = data.create_group("demo_0")
        trajectory.create_dataset("action", data=np.zeros((len(wrapper.current_traj_history) - 1, 1)))
        states = np.zeros((len(history), wrapper.max_state_size))
        for index, row in enumerate(history):
            states[index, :row["state_size"]] = row["state"].numpy()
        trajectory.create_dataset("state", data=states)
        trajectory.create_dataset("state_size", data=[row["state_size"] for row in history])

    context = SimpleNamespace(prepare=Mock())
    with FrameReader(path, context=context) as reader:
        if not terminal:
            with pytest.raises(ValueError, match="terminal"):
                reader.state(5)
        final = reader.state(5 if terminal else 4)
        assert final["registry"]["object_registry"] == {
            "robot": {"value": [0]}, "moving": {"value": [2]}, "cabinet": {"value": [0]}, "idle": {"value": [0]},
        }
        # A renderer may mutate a returned snapshot; seeking must retain its own carried state.
        final["registry"]["object_registry"]["moving"]["value"][0] = 99
        assert reader.state(2)["registry"]["object_registry"]["moving"] == {"value": [1]}
        assert reader.state(4)["registry"]["object_registry"]["moving"] == {"value": [2]}
        assert reader.state(0) == initial["state"]

