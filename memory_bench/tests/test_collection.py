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
from memory_bench.processing import make_segments
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
    collector = SessionCollector(session, tmp_path / "instance.json", tmp_path / "recording.hdf5")
    monkeypatch.setattr(collector, "_notify", lambda message: None)
    env = SimpleNamespace(scene=scene, current_traj_history=[{"state": deepcopy(initial["state"])}],
                          colliding=False, change_world=False)

    def step(action):
        if env.change_world:
            mug["done"] = "unexpected"
        env.current_traj_history.append({"action": action.clone(), "state": deepcopy(scene.save()["state"])})

    env.step = step
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
    return collector, robot, mug


def test_multikey_restoration_keeps_exit_frame_and_inherits_world(collection):
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
        exit_frame = len(env.current_traj_history) - 1
        recorded_exit = deepcopy(env.current_traj_history[-1])
        collector._finish_active_segment()
        assert collector.active_segment is None
        assert collector.server._waiting_to_resume
        assert mug["done"] == segment_id
        assert collector.scene_file == frozen
        assert robot.state == scene_objects(frozen)["robot"]
        assert len(env.current_traj_history) - 1 == exit_frame + 1
        assert env.current_traj_history[exit_frame]["state"] == recorded_exit["state"]
        exit_state = read_json(collector._state_path(segment_id, "exit"))
        assert scene_objects(exit_state)["robot"]["root_link"]["pos"] == [9, 8, 0]
        transfer = list(collector.transfers.values())[-1]
        assert transfer["kind"] == "robot_restore"
        assert transfer["restore_frames"] == [exit_frame, exit_frame + 1]
        collector._start_segment()
        next_id = collector.active_segment
        assert collector.segments[next_id]["frames"][0] == exit_frame + 1
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
        assert len(env.current_traj_history) == 1


def test_next_segment_waits_for_new_synced_command(collection, monkeypatch):
    collector, robot, mug = collection
    collector._start_segment()
    mug["done"] = "key_1"
    collector.server.env.step(th.zeros(6))
    collector._finish_active_segment()
    command = {"synced": False}
    generation = [10]
    collector.server.session_command_snapshot = lambda: (generation[0], command)
    monkeypatch.setattr(teleop_utils, "ready_command_is_synced", lambda server, cmd: cmd["synced"])
    collector.process()
    assert collector.active_segment is None
    collector.advance_requested = True
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
    monkeypatch.setattr(teleop_utils, "ready_command_is_synced", lambda server, command: True)

    collector.advance_requested = True
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
        assert collector.segments["key_1"]["frames"] == [0, None]


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


def test_segment_cut_discards_robot_restore_and_checks_world(collection, monkeypatch, tmp_path):
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
    normalized = tmp_path / "normalized.hdf5"
    with h5py.File(normalized, "w") as stream:
        group = stream.create_group("data/demo_0")
        group.create_dataset("action", data=np.zeros((len(env.current_traj_history) - 1, 6)))
        group.create_dataset("state_json", data=[json.dumps(item["state"]) for item in env.current_traj_history],
                             dtype=h5py.string_dtype())
    instance_path = tmp_path / "instance.json"
    instance_path.write_text(json.dumps(collector.instance))
    monkeypatch.setattr(make_segments, "recording_metadata", lambda *args: {"interface": {"robot_name": "robot"}})
    manifests = []
    monkeypatch.setattr(make_segments, "make_segment", lambda *args: manifests.append(args))
    make_segments.make_joint_segments(collector.session, collector.marks_path, normalized, instance_path)
    assert [args[3]["frames"] for args in manifests] == [[0, 1], [2, 3], [4, 5]]
    # A nonrobot change during the discarded interval must still reject the recording.
    with h5py.File(normalized, "r+") as stream:
        state = json.loads(stream["data/demo_0/state_json"][2])
        state["registry"]["object_registry"]["mug"]["done"] = "unexpected"
        stream["data/demo_0/state_json"][2] = json.dumps(state)
    with pytest.raises(ValueError, match="Discarded transfer"):
        make_segments.make_joint_segments(collector.session, collector.marks_path, normalized, instance_path)
