"""Exercise real HDF5 buffers and rollback without starting Kit or hardware."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import pytest
import torch as th

from memory_bench.collection import recording_wrapper
from omnigibson.envs.env_wrapper import EnvironmentWrapper


@pytest.fixture
def recorder(monkeypatch):
    wrapper = recording_wrapper.SegmentRecordingWrapper.__new__(recording_wrapper.SegmentRecordingWrapper)
    counter = th.tensor([12])
    world = {"value": 12.0}
    config = {
        "env": {"action_frequency": 30, "rendering_frequency": 30, "physics_frequency": 120},
        "task": {"type": "DummyTask"},
        "robots": [{"model": "R1Pro", "name": "robot"}],
    }
    scene = SimpleNamespace(
        save=lambda **kwargs: dict(world),
        restore=lambda state: world.update(state),
    )
    env = SimpleNamespace(scene=scene, config=config, episode_steps=counter, _current_steps=counter)

    def step(action, **kwargs):
        counter[0] += 1
        world["value"] += float(action[0])
        return [{}], th.zeros(1), th.tensor([False]), th.tensor([False]), [{}]

    env.step = step
    EnvironmentWrapper.__init__(wrapper, env)
    wrapper.compression = {}
    wrapper.hdf5_file = None
    wrapper._enable_dump_filters = False
    wrapper._fps = 30
    wrapper._clear_segment()
    wrapper.disable_dump_filters = Mock()
    wrapper._optimize_sim_for_data_collection = Mock()
    monkeypatch.setattr(recording_wrapper.og, "sim", SimpleNamespace(
        dump_state=lambda **kwargs: th.tensor([world["value"]]),
        viewer_camera=SimpleNamespace(active_camera_path="camera"),
    ))
    monkeypatch.setattr(recording_wrapper, "get_omnigibson_git_hash", lambda: "test")
    return wrapper, world, counter


def begin(wrapper, path, activity="key_activity"):
    wrapper.begin_segment(path, {"state": "entry"}, {
        "activity_name": activity, "session_id": "test", "instance_id": 1,
        "case_id": "A", "segment_id": "key_1", "attempt": "attempt_0001",
        "interface": {"action_dim": 1},
    })


def test_independent_files_keep_world_and_local_rollback(recorder, tmp_path):
    wrapper, world, counter = recorder
    wrapper.step(th.tensor([1.0]))  # Robot preparation is not recorded.
    assert world["value"] == 13
    assert wrapper.current_traj_history == []
    first = tmp_path / "key_1.hdf5"
    begin(wrapper, first)
    wrapper.step(th.tensor([1.0]))
    wrapper.update_checkpoint()
    wrapper.step(th.tensor([5.0]))
    wrapper.current_transitions = {1: {"systems": {"add": ["water"]}}}

    wrapper.rollback_to_checkpoint()

    assert counter.item() == 14  # Preserve steps before this file's initial state.
    assert world["value"] == 14
    assert wrapper.step_count == 1
    assert wrapper.current_transitions == {}
    wrapper.step(th.tensor([2.0]))
    assert wrapper.finish_segment() == (first, 2)
    assert not wrapper.is_recording
    assert wrapper.current_traj_history == []
    assert wrapper.checkpoint_states == []
    with h5py.File(first) as stream:
        assert list(stream["data"]) == ["demo_0"]
        demo = stream["data/demo_0"]
        assert demo["action"][:, 0].tolist() == [1, 2]
        assert demo["state"][:, 0].tolist() == [13, 14]
        assert demo["rollbacks/step_1-0/action"][:, 0].tolist() == [5]
        assert demo["rollbacks/step_1-0/state"][:, 0].tolist() == [14]
        assert json.loads(demo.attrs["transitions"]) == {}
        assert json.loads(stream["data"].attrs["config"])["task"]["activity_name"] == "key_activity"
        assert stream["data"].attrs["collection_complete"]
    saved_bytes = first.read_bytes()
    wrapper.step(th.tensor([0.0]))
    second = tmp_path / "query.hdf5"
    begin(wrapper, second, "query_activity")
    assert wrapper.current_traj_history[0]["state"].item() == 16
    assert wrapper.step_count == 0
    assert wrapper.traj_count == 0
    wrapper.step(th.tensor([3.0]))
    wrapper.finish_segment()
    with h5py.File(second) as stream:
        assert stream["data/demo_0/state"][:, 0].tolist() == [16]
        assert stream["data/demo_0/action"][:, 0].tolist() == [3]
    assert world["value"] == 19
    assert first.read_bytes() == saved_bytes


def test_failed_write_keeps_accepted_and_rollback_buffers_for_retry(recorder, tmp_path, monkeypatch):
    wrapper, world, counter = recorder
    path = tmp_path / "key_1.hdf5"
    begin(wrapper, path)
    wrapper.update_checkpoint()
    wrapper.step(th.tensor([5.0]))
    wrapper.rollback_to_checkpoint()
    wrapper.step(th.tensor([2.0]))
    original = wrapper.current_traj_history
    rollbacks = wrapper.checkpoint_rollback_trajs
    flush = wrapper.flush_current_file
    monkeypatch.setattr(wrapper, "flush_current_file", Mock(side_effect=OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        wrapper.finish_segment()

    assert wrapper.hdf5_file is None
    assert wrapper.is_recording
    assert wrapper.current_traj_history is original
    assert wrapper.checkpoint_rollback_trajs is rollbacks
    assert world["value"] == 14
    assert not path.exists()
    monkeypatch.setattr(wrapper, "flush_current_file", flush)
    assert wrapper.finish_segment() == (path, 1)
    with h5py.File(path) as stream:
        assert stream["data/demo_0/action"][:, 0].tolist() == [2]
        assert stream["data/demo_0/rollbacks/step_0-0/action"][:, 0].tolist() == [5]


def test_empty_completed_recording_rejected_but_abort_is_preserved(recorder, tmp_path):
    wrapper, _, _ = recorder
    path = tmp_path / "key_1.hdf5"
    begin(wrapper, path)
    with pytest.raises(ValueError, match="at least one action"):
        wrapper.finish_segment()
    with pytest.raises(RuntimeError, match="finish or abort"):
        wrapper.save_data()
    with pytest.raises(RuntimeError, match="before resetting"):
        wrapper.reset()
    aborted, count = wrapper.finish_segment(aborted=True)
    assert count == 0
    assert aborted.name == "key_1_aborted.hdf5"
    assert not path.exists()
    with h5py.File(aborted) as stream:
        assert not stream["data"].attrs["collection_complete"]
    wrapper.save_data()
