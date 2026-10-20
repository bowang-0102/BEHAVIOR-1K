"""Collection restores the common robot start without resetting task progress."""

from copy import deepcopy
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import numpy as np
import pytest
import torch as th

from memory_bench.collection import teleop_utils
from memory_bench.collection.collect_session import SessionCollector
from memory_bench.utils.paths import SessionPaths
from memory_bench.utils.scene_state import read_json, robot_metadata, scene_objects


@pytest.fixture
def collection(tmp_path, monkeypatch):
    from memory_bench import navigation

    class Robot:
        name = "robot"
        model = "r1pro"
        action_dim = 6
        base_action_idx = th.tensor([0, 1, 2])
        trunk_action_idx = th.tensor([3])
        trunk_control_idx = th.tensor([0])
        arm_names = ["left"]
        arm_action_idx = {"left": th.tensor([4])}
        arm_control_idx = {"left": th.tensor([1])}
        gripper_action_idx = {"left": th.tensor([5])}
        gripper_control_idx = {"left": th.tensor([2])}
        control_limits = {"position": (-th.ones(3), th.ones(3))}

        def __init__(self):
            self.state = {
                "root_link": {"pos": [1, 2, 0], "ori": [0, 0, 0, 1]},
                "joint_pos": [0.1, 0.2, 0.8],
                "controller_groups": {"arm_left": {"goals": [0.2], "goal_set": True}},
            }
            self._ag_obj_in_hand = {"left": None}
            self.unstable = False

        def load_state(self, state, **kwargs):
            self.state = deepcopy(state)
            state.clear()  # Loading may mutate the supplied state; the frozen TRO must survive.

        def keep_still(self):
            pass

        def get_position_orientation(self):
            return th.tensor(self.state["root_link"]["pos"]), th.tensor(self.state["root_link"]["ori"])

        def get_joint_positions(self):
            return th.tensor(self.state["joint_pos"])

        def get_joint_velocities(self):
            return th.full((3,), 0.2 if self.unstable else 0.0)

        def get_linear_velocity(self):
            return th.zeros(3)

        def get_angular_velocity(self):
            return th.zeros(3)

    robot = Robot()
    mug = {"done": None}

    class Scene:
        robots = [robot]

        def save(self, **kwargs):
            return {"state": {"registry": {
                "object_registry": {"robot": deepcopy(robot.state), "mug": deepcopy(mug)},
                "system_registry": {},
            }}}

        def object_registry(self, key, name):
            return mug

    scene = Scene()
    initial = scene.save()
    initial["robot"] = robot_metadata(robot)
    segment_ids = ["key_1", "key_2", "query"]
    instance = {
        "instance_id": 1, "case_id": "A", "robot": initial["robot"],
        "bindings": {key: {"item": "mug"} for key in segment_ids},
        "validation": {"shared_initial_robot_state": True},
    }
    session = SimpleNamespace(
        session_id="test", key_ids=segment_ids[:-1], query_id="query",
        paths=SessionPaths(tmp_path, tmp_path, tmp_path), assets={},
        segments={key: {"role": "query_task" if key == "query" else "key_task", "activity": key}
                  for key in segment_ids},
        task=SimpleNamespace(
            check_entry=lambda *args, **kwargs: True,
            check_segment=lambda key, role, objects, **kwargs: objects["item"]["done"] == key,
            query_cabinets=[], query_events=lambda *args, **kwargs: ([], {}),
            instruction_for=lambda *args: "Task instruction",
            object_usage={key: {"used": ["item"], "read": ["item"], "write": ["item"], "protected": ["item"]}
                          for key in segment_ids},
        ),
    )
    monkeypatch.setattr(
        "memory_bench.collection.collect_session.load_instance_scene", lambda *args: (instance, initial)
    )
    instance_path = tmp_path / "instance.json"
    instance_path.write_text(json.dumps(instance))
    collector = SessionCollector(session, instance_path, tmp_path / "recordings" / "test" / "instance_0001")
    monkeypatch.setattr(collector, "_notify", lambda message: None)
    monkeypatch.setattr(collector, "_read_interface", lambda *args: None)
    env = SimpleNamespace(scene=scene, current_traj_history=[], is_recording=False,
                          colliding=False, change_world=False, traj_count=0,
                          checkpoint_states=[], checkpoint_step_idxs=[], saved=[], restoration_steps=0)

    def step(action):
        if env.change_world:
            mug["done"] = "unexpected"
        if env.is_recording:
            env.current_traj_history.append({"action": action.clone(), "state": deepcopy(scene.save()["state"])})
        else:
            env.restoration_steps += 1

    def begin_segment(path, entry, metadata):
        assert not env.is_recording
        env.recording_path = path
        env.current_traj_history = [{"state": deepcopy(scene.save()["state"])}]
        env.is_recording = True

    def finish_segment(*, aborted=False):
        assert env.is_recording
        path = env.recording_path
        if aborted:
            path = path.with_name(f"{path.stem}_aborted.hdf5")
        count = len(env.current_traj_history) - 1
        with h5py.File(path, "x") as stream:
            data = stream.create_group("data/demo_0")
            data.create_dataset("action", data=np.zeros((count, 6)))
            data.create_dataset("state_json", data=[json.dumps(item["state"]) for item in env.current_traj_history],
                                dtype=h5py.string_dtype())
        env.saved.append(path)
        env.current_traj_history = []
        env.is_recording = False
        return path, count

    env.step = step
    env.begin_segment = begin_segment
    env.finish_segment = finish_segment
    def update_checkpoint():
        env.checkpoint_states.append(deepcopy(scene.save()))
        env.checkpoint_step_idxs.append(len(env.current_traj_history))

    env.update_checkpoint = update_checkpoint
    server = SimpleNamespace(robot=robot, env=env, _waiting_to_resume=False, _session_stop_requested=False,
                             sync_session_control_state=Mock(), resume_control=Mock())
    server.pause = lambda: setattr(server, "_waiting_to_resume", True)
    collector.server = server
    sim = SimpleNamespace(physics_sim_view=SimpleNamespace(update_articulations_kinematic=Mock()),
                          sync_physx_to_fabric=Mock(), _refresh_state_caches=Mock())
    monkeypatch.setitem(sys.modules, "omnigibson", SimpleNamespace(sim=sim))
    monkeypatch.setitem(sys.modules, "omnigibson.utils.python_utils",
                        SimpleNamespace(recursively_convert_to_torch=lambda state: state))
    monkeypatch.setitem(sys.modules, "omnigibson.utils.config_utils", SimpleNamespace(TorchEncoder=json.JSONEncoder))
    monkeypatch.setitem(sys.modules, "omnigibson.utils.motion_planning_utils",
                        SimpleNamespace(detect_robot_collision_in_sim=lambda robot: env.colliding))
    monkeypatch.setitem(sys.modules, "omnigibson.object_states", SimpleNamespace(Open=object()))
    monkeypatch.setattr(navigation, "_yaw", lambda orientation: 0.0)
    collector.begin_attempt()
    server.sync_session_control_state.reset_mock()
    return collector, robot, mug


def test_multikey_saves_independent_files_before_restore_and_inherits_world(collection):
    collector, robot, mug = collection
    env = collector.server.env
    frozen = deepcopy(collector.scene_file)
    collector._start_segment()
    for segment_id in ("key_1", "key_2"):
        mug["done"] = segment_id
        robot.state["root_link"]["pos"] = [9, 8, 0]
        robot.state["joint_pos"] = [0.5, 0.6, 0.7]
        robot.state["controller_groups"]["arm_left"]["goals"] = [0.6]
        env.step(th.zeros(6))
        recorded_exit = deepcopy(env.current_traj_history[-1])
        collector._finish_active_segment()
        assert collector.active_segment is None
        assert collector.server._waiting_to_resume
        assert mug["done"] == segment_id
        assert collector.scene_file == frozen
        assert robot.state == scene_objects(frozen)["robot"]
        assert env.current_traj_history == []
        assert not env.is_recording
        with h5py.File(env.saved[-1], "r") as stream:
            assert list(stream["data"]) == ["demo_0"]
            assert stream["data/demo_0/action"].shape == (1, 6)
            assert json.loads(stream["data/demo_0/state_json"][-1]) == recorded_exit["state"]
        exit_state = read_json(collector._state_path(segment_id, "exit"))
        assert scene_objects(exit_state)["robot"]["root_link"]["pos"] == [9, 8, 0]
        assert collector.segments[segment_id]["frames"] == [0, 1]
        assert collector.transfers[-1]["validation"]["nonrobot_world_preserved"]
        collector._start_segment()
        next_id = collector.active_segment
        assert len(env.current_traj_history) == 1
        assert next_id not in collector.segments
        entry = read_json(collector._state_path(next_id, "entry"))
        assert scene_objects(entry)["mug"]["done"] == segment_id
        assert entry["robot"] == frozen["robot"]
    assert collector.active_segment == "query"
    assert collector.server.sync_session_control_state.call_count == 2


@pytest.mark.parametrize("failure", ["held_object", "collision", "world_change", "unstable"])
def test_restore_rejects_invalid_boundary(collection, failure):
    collector, robot, mug = collection
    env = collector.server.env
    before = teleop_utils.scene_snapshot(env.scene)
    if failure == "held_object":
        robot._ag_obj_in_hand["left"] = mug
    elif failure == "collision":
        env.colliding = True
    elif failure == "world_change":
        env.change_world = True
    else:
        robot.unstable = True
    message = {"held_object": "empty hands", "collision": "collision", "world_change": "nonrobot world state",
               "unstable": "did not settle"}[failure]
    with pytest.raises(RuntimeError, match=message):
        teleop_utils.restore_robot_start(collector.server, collector.scene_file, before)
    collector.server.sync_session_control_state.assert_not_called()
    assert not collector.segments
    if failure == "held_object":
        assert env.current_traj_history == []


def test_next_segment_waits_for_new_synced_command(collection, monkeypatch):
    collector, robot, mug = collection
    collector._start_segment()
    mug["done"] = "key_1"
    collector.server.env.step(th.zeros(6))
    collector._finish_active_segment()
    command = {"synced": False}
    generation = [10]
    collector.server.session_command_snapshot = lambda: (generation[0], command)
    monkeypatch.setattr(teleop_utils, "ready_command_is_synced", lambda server, cmd, **kwargs: cmd["synced"])
    collector.process()
    assert collector.active_segment is None
    collector.request_start()
    collector.process()
    assert collector.waiting_for_ready and collector.active_segment is None
    command["synced"] = True
    collector.process()
    assert collector.active_segment is None  # An old command cannot complete the handshake.
    generation[0] += 1
    command["synced"] = False
    collector.process()
    assert collector.active_segment is None
    command["synced"] = True
    collector.process()
    assert not collector.waiting_for_ready and collector.active_segment == "key_2"


@pytest.mark.parametrize("resume_existing", [False, True])
def test_synced_command_starts_or_resumes_without_replacing_entry(collection, monkeypatch, resume_existing):
    collector, robot, mug = collection
    if resume_existing:
        collector._start_segment()
        collector.server.env.step(th.zeros(6))
    entry_marks = deepcopy(collector.segments)
    collector.server.pause()
    collector.server.resume_control.side_effect = lambda: setattr(collector.server, "_waiting_to_resume", False)
    generation = [10]
    collector.server.session_command_snapshot = lambda: (generation[0], {})
    monkeypatch.setattr(teleop_utils, "ready_command_is_synced", lambda server, command, **kwargs: True)

    collector.request_start()
    collector.process()
    assert collector.waiting_for_ready
    assert collector.segments == entry_marks

    generation[0] += 1
    collector.process()
    assert not collector.waiting_for_ready
    assert collector.active_segment == "key_1"
    if resume_existing:
        assert collector.segments == entry_marks
    else:
        assert collector.server.env.is_recording
        assert len(collector.server.env.current_traj_history) == 1
        assert collector._state_path("key_1", "entry").exists()


@pytest.mark.parametrize("failure", ["moving", "unfinished", "wrong_cabinet"])
def test_failed_completion_preserves_active_segment_and_marks(collection, failure):
    collector, robot, mug = collection
    if failure == "wrong_cabinet":
        for segment_id in collector.session.key_ids:
            collector._start_segment()
            mug["done"] = segment_id
            collector.server.env.step(th.zeros(6))
            collector._finish_active_segment()
    collector._start_segment()
    segment_id = collector.active_segment
    mug["done"] = segment_id
    collector.server.env.step(th.zeros(6))
    if failure == "moving":
        robot.unstable = True
    elif failure == "unfinished":
        mug["done"] = None
    else:
        collector.invalid_reason = "wrong cabinet opened"
    marks = deepcopy(collector.segments)
    transfers = deepcopy(collector.transfers)

    collector._finish_active_segment()

    assert collector.active_segment == segment_id
    assert collector.segments == marks
    assert collector.transfers == transfers
    assert not collector._state_path(segment_id, "exit").exists()
    assert not collector.finished
    assert not collector.server._session_stop_requested


@pytest.mark.parametrize(
    "failure", [None, "base", "trunk", "arm_target", "grasp_command", "gripper_target", "held_object"]
)
def test_ready_command_matches_live_robot_before_control(collection, failure):
    collector, robot, mug = collection
    server = collector.server
    command = {"base": th.zeros(3), "trunk": th.zeros(1), "left_gripper": th.tensor([0.8])}
    targets = {"trunk": th.tensor([0.1]), "left": th.tensor([0.2]), "left_gripper": th.tensor([0.8])}
    server.session_neutral_targets = lambda command: targets
    server._grasp_action = {"left": 0.8}
    if failure in {"base", "trunk"}:
        command[failure][0] = 0.1
    elif failure == "arm_target":
        targets["left"][0] += 0.1
    elif failure == "grasp_command":
        command["left_gripper"][0] = 0.0
    elif failure == "gripper_target":
        targets["left_gripper"][0] += 0.1
    elif failure == "held_object":
        robot._ag_obj_in_hand["left"] = mug

    assert teleop_utils.ready_command_is_synced(server, command) is (failure is None)


def test_subtask_transition_clears_checkpoints_and_waits_for_start(collection):
    collector, robot, mug = collection
    env = collector.server.env
    collector._start_segment()
    first_entry = env.checkpoint_step_idxs[0]
    assert len(env.checkpoint_states) == 1
    mug["done"] = "key_1"
    env.step(th.zeros(6))
    env.update_checkpoint()

    collector.request_finish()
    collector.process()

    assert collector.active_segment is None
    assert collector.next_segment_index == 1
    assert collector.server._waiting_to_resume
    assert env.checkpoint_states == []
    assert env.checkpoint_step_idxs == []
    collector.process()
    assert collector.active_segment is None
    collector._start_segment()
    assert env.checkpoint_step_idxs == [len(env.current_traj_history)]
    assert env.checkpoint_step_idxs[0] == first_entry == 1


@pytest.mark.parametrize("failure", ["held_object", "collision"])
def test_finish_rejects_invalid_boundary_without_stopping(collection, failure):
    collector, robot, mug = collection
    collector._start_segment()
    if failure == "held_object":
        robot._ag_obj_in_hand["left"] = mug
    else:
        collector.server.env.colliding = True

    collector.request_finish()
    collector.process()

    assert collector.active_segment == "key_1"
    assert not collector.finished
    assert not collector.server._session_stop_requested


def test_resume_allows_a_held_object_without_relaxing_entry_checks(collection):
    collector, robot, mug = collection
    server = collector.server
    robot._ag_obj_in_hand["left"] = mug
    server._grasp_action = {"left": -1}
    command = {"base": th.zeros(3), "trunk": th.zeros(1), "left_gripper": th.tensor([-1.0])}
    server.session_neutral_targets = lambda command: {
        "trunk": th.tensor([0.1]), "left": th.tensor([0.2]), "left_gripper": th.tensor([-1.0])
    }

    assert teleop_utils.ready_command_is_synced(server, command, resume_existing=True)
    assert not teleop_utils.ready_command_is_synced(server, command)


@pytest.mark.parametrize("keep_forbidden", [False, True])
def test_rollback_discards_removed_query_events(collection, keep_forbidden):
    collector, robot, mug = collection
    collector.active_segment = "query"
    collector.query_open_state = {"cabinet": True}
    collector.session.task.query_events = lambda *args, **kwargs: ([], {"cabinet": False})
    collector.object_events = [
        {"frame": 0, "type": "cabinet_opened", "forbidden": keep_forbidden, "bddl_instance": "cabinet_1"},
        {"frame": 1, "type": "wrong_cabinet_opened", "forbidden": True, "bddl_instance": "cabinet_2"},
    ]
    collector.invalid_reason = "wrong cabinet opened: cabinet_2"
    collector.server.env.is_recording = True
    collector.server.env.current_traj_history = [{"state": {}}]
    collector.server.env.step(th.zeros(6))
    collector.finish_requested = True
    collector.awaiting_ready_generation = 10

    collector.after_rollback()

    assert len(collector.object_events) == 1
    assert collector.invalid_reason == ("wrong cabinet opened: cabinet_1" if keep_forbidden else None)
    assert collector.query_open_state == {"cabinet": False}
    assert not collector.finish_requested
    assert not collector.waiting_for_ready


def test_whole_task_restart_preserves_aborted_attempt(collection):
    collector, robot, mug = collection
    collector._start_segment()
    collector.abort("whole task restarted by operator")
    old_attempt = collector.attempt_path
    old_entry = collector._state_path("key_1", "entry")
    env = collector.server.env
    env.checkpoint_states.clear()
    env.checkpoint_step_idxs.clear()
    collector.server._rollback_checkpoint_idx = 3

    collector.begin_attempt()

    assert collector.state_dir.name == "attempt_0002"
    assert collector.active_segment is None
    assert collector.next_segment_index == 0
    assert not collector.finished
    assert collector.invalid_reason is None
    assert collector.server._waiting_to_resume
    assert collector.server._rollback_checkpoint_idx is None
    assert old_entry.exists()
    previous = read_json(old_attempt)
    assert previous["invalid_reason"] == "whole task restarted by operator"
    assert previous["status"] == "aborted"
    assert previous["partial"]["hdf5"].endswith("key_1_aborted.hdf5")
    assert read_json(collector.attempt_path)["status"] == "in_progress"


def test_complete_attempt_publishes_only_saved_files_without_marks(collection):
    collector, robot, mug = collection
    env = collector.server.env
    collector._start_segment()
    for segment_id in collector.segment_ids:
        mug["done"] = segment_id
        robot.state["root_link"]["pos"] = [9, 8, 0]
        env.step(th.zeros(6))
        collector._finish_active_segment()
        if not collector.finished:
            collector._start_segment()
    assert collector.finished and collector.server._session_stop_requested
    attempt = read_json(collector.attempt_path)
    assert attempt["status"] == "complete"
    assert list(attempt["segments"]) == collector.segment_ids
    assert all(record["frames"] == [0, 1] for record in attempt["segments"].values())
    assert len(attempt["transfers"]) == 2
    assert len(env.saved) == 3
    assert not list(collector.recording_dir.rglob("marks.*"))
    assert not env.is_recording


def test_subtask_save_failure_retains_buffer_and_world_for_retry(collection, monkeypatch):
    collector, robot, mug = collection
    collector._start_segment()
    env = collector.server.env
    mug["done"] = "key_1"
    robot.state["root_link"]["pos"] = [9, 8, 0]
    env.step(th.zeros(6))
    finish = env.finish_segment
    monkeypatch.setattr(env, "finish_segment", Mock(side_effect=OSError("disk full")))

    collector._finish_active_segment()

    assert collector.active_segment == "key_1"
    assert env.is_recording and len(env.current_traj_history) == 2
    assert robot.state["root_link"]["pos"] == [9, 8, 0]
    assert collector.server._waiting_to_resume
    assert not collector.segments
    monkeypatch.setattr(env, "finish_segment", finish)
    collector._finish_active_segment()
    assert collector.active_segment is None
    assert len(env.saved) == 1


def test_attempt_index_failure_retries_closed_file_before_changing_world(collection, monkeypatch):
    collector, robot, mug = collection
    collector._start_segment()
    env = collector.server.env
    mug["done"] = "key_1"
    robot.state["root_link"]["pos"] = [9, 8, 0]
    env.step(th.zeros(6))
    publish = collector._write_attempt
    monkeypatch.setattr(collector, "_write_attempt", Mock(side_effect=OSError("disk full")))
    collector._finish_active_segment()
    assert not env.is_recording
    assert collector.active_segment == "key_1"
    assert robot.state["root_link"]["pos"] == [9, 8, 0]
    assert len(env.saved) == 1
    collector.request_start()
    collector.process()
    assert not collector.waiting_for_ready
    monkeypatch.setattr(collector, "_write_attempt", publish)
    collector._finish_active_segment()
    assert collector.active_segment is None
    assert len(env.saved) == 1
