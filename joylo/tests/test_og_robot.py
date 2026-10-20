"""Exercise session control synchronization without launching the simulator."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import pytest
import torch as th

from joylo.gello.robots import og_robot
from memory_bench.collection.teleop_utils import ready_command_is_synced
from omnigibson.controllers import MultiFingerGripperController
from omnigibson.envs.env_wrapper import EnvironmentWrapper
from omnigibson.utils import backend_utils


@pytest.fixture
def session_server(monkeypatch):
    # Kit normally initializes the controller arithmetic backend during startup.
    for name, value in vars(backend_utils._ComputeNumpyBackend).items():
        if not name.startswith("__"):
            monkeypatch.setattr(backend_utils._compute_backend, name, value, raising=False)
    server = og_robot.OGRobotServer.__new__(og_robot.OGRobotServer)
    server._teleop_config = og_robot.ROBOT_CONFIGS["r1pro"]
    config = server._teleop_config
    joint_positions = th.zeros(28)
    joint_positions[6:10] = (config.torso_upright + config.torso_downward) / 2
    joint_positions[8] += 0.1
    joint_positions[10:24] = th.linspace(-0.2, 0.2, 14)
    joint_positions[24:] = 0.05
    low, high = -th.ones(28) * 3, th.ones(28) * 3
    low[24:], high[24:] = 0.0, 0.05
    server.robot = SimpleNamespace(
        arm_names=["left", "right"],
        action_dim=23,
        trunk_control_idx=th.arange(6, 10),
        arm_control_idx={"left": th.arange(10, 17), "right": th.arange(17, 24)},
        gripper_control_idx={"left": th.arange(24, 26), "right": th.arange(26, 28)},
        base_action_idx=th.arange(3),
        trunk_action_idx=th.arange(3, 7),
        arm_action_idx={"left": th.arange(7, 14), "right": th.arange(15, 22)},
        gripper_action_idx={"left": th.tensor([14]), "right": th.tensor([22])},
        control_limits={"position": (low, high)},
        controllers={"gripper_left": ("left", 0), "gripper_right": ("right", 0)},
        get_joint_positions=lambda: joint_positions.clone(),
        _ag_obj_in_hand={"left": None, "right": None},
    )
    grippers = {
        arm: MultiFingerGripperController(
            control_freq=30,
            motor_type="position",
            mode="smooth",
            control_limits={"position": (low, high), "has_limit": th.ones(28, dtype=th.bool)},
            dof_idx=indices,
        )
        for arm, indices in server.robot.gripper_control_idx.items()
    }
    monkeypatch.setattr(og_robot.ControllerView, "_controller_groups", grippers)
    monkeypatch.setattr(
        og_robot.ControllerView,
        "get_goal",
        lambda group, index: {"target": joint_positions[server.robot.gripper_control_idx[group]].mean().reshape(1)},
    )
    monkeypatch.setattr(og_robot.og, "sim", SimpleNamespace(get_sim_step_dt=lambda: 1 / 30))
    monkeypatch.setattr(og_robot, "USE_VERTICAL_VISUALIZERS", False)
    monkeypatch.setattr(og_robot, "SIMPLIFIED_TRUNK_CONTROL", True)
    server._session_collector = object()
    server._joint_command_lock = threading.Lock()
    server._joint_command_generation = 0
    server._joint_cmd = {
        "left": th.zeros(7),
        "right": th.zeros(7),
        "left_gripper": th.ones(1),
        "right_gripper": th.ones(1),
        "base": th.ones(3),
        "trunk": th.ones(2),
    }
    server._grasp_action = {arm: 1 for arm in server.robot.arm_names}
    server._gripper_action_signal_detectors = {
        arm: og_robot.utils.SignalChangeDetector() for arm in server.robot.arm_names
    }
    server._arm_shoulder_directions = {"left": -1.0, "right": 1.0}
    server._arm_joint_limits = {
        arm: {"lower": low[indices], "upper": high[indices]}
        for arm, indices in server.robot.arm_control_idx.items()
    }
    server._trunk_tilt_limits = {"lower": -3.0, "upper": 3.0}
    server._in_cooldown = False
    server._reset_max_arm_delta = 0.01
    server.is_bimanual_mobile = True
    server.ghosting = False
    return server, joint_positions


@pytest.mark.parametrize("finger_position, expected_grasp", [(0.05, 1), (1e-6, -1)])
def test_session_sync_matches_restored_pose(session_server, finger_position, expected_grasp):
    server, joint_positions = session_server
    joint_positions[24:] = finger_position

    server.sync_session_control_state()
    _, command = server.session_command_snapshot()

    assert th.equal(command["base"], th.zeros(3))
    assert th.equal(command["trunk"], th.zeros(2))
    for arm in server.robot.arm_names:
        assert command[f"{arm}_gripper"].shape == (1,)
        assert command[f"{arm}_gripper"].dtype == joint_positions.dtype
        assert command[f"{arm}_gripper"].item() == expected_grasp
        assert server._grasp_action[arm] == expected_grasp
        assert not server._gripper_action_signal_detectors[arm].process_sample(expected_grasp)
    assert ready_command_is_synced(server, command)


@pytest.mark.parametrize("cooldown", [False, True])
def test_first_session_action_preserves_restored_arm_pose(session_server, cooldown):
    server, joint_positions = session_server
    server._in_cooldown = cooldown
    server.sync_session_control_state()

    action = server.get_action()

    th.testing.assert_close(action[server.robot.base_action_idx], th.zeros(3))
    th.testing.assert_close(action[server.robot.trunk_action_idx], joint_positions[server.robot.trunk_control_idx])
    for arm in server.robot.arm_names:
        th.testing.assert_close(
            action[server.robot.arm_action_idx[arm]], joint_positions[server.robot.arm_control_idx[arm]]
        )
        assert action[server.robot.gripper_action_idx[arm]].item() == 1


def test_tilted_arm_targets_respect_joint_limits(session_server):
    server, _ = session_server
    server.sync_session_control_state()
    server._joint_cmd["left"][0] = 10.0
    server._joint_cmd["right"][0] = -10.0

    action = server.get_action()

    assert action[server.robot.arm_action_idx["left"]][0].item() == 3.0
    assert action[server.robot.arm_action_idx["right"]][0].item() == -3.0


@pytest.mark.parametrize("moving_part", ["trunk", "empty_gripper"])
def test_resume_can_continue_pending_targets_while_new_entry_stays_strict(session_server, moving_part):
    server, joint_positions = session_server
    server.sync_session_control_state()
    if moving_part == "trunk":
        server._current_trunk_translate += 0.2
    else:
        joint_positions[24:] = 0.025  # Frozen halfway toward the unchanged open goal.
    _, command = server.session_command_snapshot()

    assert not ready_command_is_synced(server, command)
    assert ready_command_is_synced(server, command, resume_existing=True)
    pending = server.session_neutral_targets(command)
    action = server.get_action()
    th.testing.assert_close(action[server.robot.trunk_action_idx], pending["trunk"])
    for arm in server.robot.arm_names:
        assert action[server.robot.gripper_action_idx[arm]].item() == 1
    # A resume still requires neutral base/trunk input and the matching arm pose.
    for key in ("base", "trunk", "left"):
        wrong_command = {name: value.clone() for name, value in command.items()}
        wrong_command[key][0] += 0.1
        assert not ready_command_is_synced(server, wrong_command, resume_existing=True)


@pytest.fixture
def button_server(session_server):
    server, _ = session_server
    server._session_collector = SimpleNamespace(
        active_segment="key_1", waiting_for_ready=False,
        request_start=Mock(), request_finish=Mock(), cancel_ready=Mock(),
        after_rollback=Mock(), abort=Mock(),
    )
    server._recording_path = "recording.hdf5"
    server._waiting_to_resume = False
    server._rollback_checkpoint_idx = None
    server._session_stop_requested = False
    server.event_queue = []
    server.env = SimpleNamespace(
        checkpoint_states=[{}], update_checkpoint=Mock(), rollback_to_checkpoint=Mock()
    )
    server._button_toggled_state = {key: False for key in ("x", "y", "a", "b", "left", "right")}
    for key in ("x", "y", "a", "b", "capture", "home", "left", "right"):
        server._joint_cmd[f"button_{key}"] = th.zeros(1)
    server.task_relevant_objects = []
    server.task_irrelevant_objects = []
    server.object_beacons = {}
    server.reset = Mock()
    server.stop = Mock()
    return server


@pytest.mark.parametrize("button, callback", [("left", "request_finish"), ("right", "request_start")])
def test_session_arrows_dispatch_once_per_press(button_server, button, callback):
    server = button_server
    server._joint_cmd[f"button_{button}"][0] = 1
    server._process_button_inputs()
    server._process_button_inputs()
    getattr(server._session_collector, callback).assert_called_once_with()


def test_session_joycon_checkpoint_and_rollback(button_server, monkeypatch):
    server = button_server
    monkeypatch.setattr(og_robot.utils, "optimize_sim_settings", lambda **kwargs: None)
    server._joint_cmd["button_x"][0] = 1
    server._process_button_inputs()
    server.env.update_checkpoint.assert_called_once_with()
    server._joint_cmd["button_x"][0] = 0
    server._joint_cmd["button_y"][0] = 1
    server._process_button_inputs()
    assert server._rollback_checkpoint_idx == 1
    server._joint_cmd["button_y"][0] = 0
    server._joint_cmd["button_x"][0] = 1
    server._process_button_inputs()
    server.env.rollback_to_checkpoint.assert_called_once_with(index=-1)
    server._session_collector.after_rollback.assert_called_once_with()
    assert server._waiting_to_resume


def test_session_joycon_a_works_with_dummy_task(button_server):
    server = button_server
    obj = SimpleNamespace(visible=True)
    server.task_irrelevant_objects = [obj]
    server._joint_cmd["button_a"][0] = 1
    server._process_button_inputs()
    assert not obj.visible


@pytest.mark.parametrize("button", ["home", "capture"])
def test_session_global_joycon_controls(button_server, button):
    server = button_server
    server._joint_cmd[f"button_{button}"][0] = 1
    server._process_button_inputs()
    if button == "home":
        server.reset.assert_called_once_with()
    else:
        server._session_collector.abort.assert_called_once_with()
        assert server._session_stop_requested


def test_session_keyboard_controls_and_repeat(button_server, monkeypatch):
    server = button_server
    callbacks = []
    inputs = SimpleNamespace(R="R", X="X", P="P", ESCAPE="Esc")
    input_interface = SimpleNamespace(
        subscribe_to_keyboard_events=lambda keyboard, callback: callbacks.append(callback)
    )
    monkeypatch.setattr(og_robot, "lazy", SimpleNamespace(
        carb=SimpleNamespace(input=SimpleNamespace(
            KeyboardInput=inputs, KeyboardEventType=SimpleNamespace(KEY_PRESS="press"),
            acquire_input_interface=lambda: input_interface,
        )),
        omni=SimpleNamespace(appwindow=SimpleNamespace(
            get_default_app_window=lambda: SimpleNamespace(get_keyboard=lambda: object())
        )),
    ))
    server._setup_keyboard_handlers()
    for key, kind in (("R", "press"), ("R", "repeat"), ("X", "press"), ("P", "press"), ("Esc", "press")):
        callbacks[0](SimpleNamespace(input=key, type=kind))
    server.reset.assert_called_once_with()
    server._session_collector.request_start.assert_called_once_with()
    assert server._waiting_to_resume
    server._session_collector.abort.assert_called_once_with()
    assert server._session_stop_requested


def test_rollback_restores_counter_and_serializes_failed_branch(monkeypatch, tmp_path):
    wrapper = og_robot.HDF5CollectionWrapper.__new__(og_robot.HDF5CollectionWrapper)
    counter = th.tensor([6])
    env = SimpleNamespace(
        scene=SimpleNamespace(restore=Mock()), _current_steps=counter, episode_steps=counter
    )
    EnvironmentWrapper.__init__(wrapper, env)
    wrapper.checkpoint_states = [{"frame": 2}]
    wrapper.checkpoint_step_idxs = [3]
    wrapper.current_traj_history = [
        {"state": th.tensor([float(i)]), "state_size": 1, **({"action": th.tensor([float(i)])} if i else {})}
        for i in range(7)
    ]
    wrapper.step_count = 6
    wrapper._recording_start_step = 0
    wrapper.current_transitions = {1: "keep", 2: "remove", 4: "remove"}
    wrapper.checkpoint_rollback_trajs = {}
    wrapper._optimize_sim_for_data_collection = Mock()
    monkeypatch.setattr(og_robot.og, "sim", SimpleNamespace(viewer_camera=SimpleNamespace(active_camera_path="camera")))

    wrapper.rollback_to_checkpoint()

    assert counter.item() == 2
    assert len(wrapper.current_traj_history) == 3
    assert wrapper.step_count == 2
    assert wrapper.current_transitions == {1: "keep"}
    assert list(wrapper.checkpoint_rollback_trajs) == [2]
    assert wrapper.checkpoint_rollback_trajs[2][0]["transitions"] == {2: "remove", 4: "remove"}
    assert "action" not in wrapper.checkpoint_rollback_trajs[2][0]["step_data"][0]

    # A second rollback without intervening actions must still be saveable.
    wrapper.rollback_to_checkpoint()
    assert len(wrapper.checkpoint_rollback_trajs[2]) == 1
    wrapper.max_state_size = 1
    wrapper.compression = {}
    wrapper.init_metadata = {}
    with h5py.File(tmp_path / "rollback.hdf5", "w") as stream:
        wrapper.hdf5_file = stream
        group = wrapper._process_traj_to_hdf5(wrapper.current_traj_history, "demo_0")
        wrapper._postprocess_traj_group(group)
        branch = group["rollbacks/step_2-0"]
        assert branch.attrs["num_samples"] == 4
        assert branch["action"][:, 0].tolist() == [3, 4, 5, 6]
        assert branch["state"][:, 0].tolist() == [2, 3, 4, 5]


def test_transition_immediately_after_checkpoint_uses_scalar_action_index():
    wrapper = og_robot.HDF5CollectionWrapper.__new__(og_robot.HDF5CollectionWrapper)
    counter = th.tensor([2])
    EnvironmentWrapper.__init__(wrapper, SimpleNamespace(episode_steps=counter))
    wrapper.checkpoint_step_idxs = [3]
    wrapper._recording_start_step = 0
    wrapper.current_transitions = {}
    wrapper.add_transition_info(SimpleNamespace(name="water"), add=True)
    counter[0] = 3
    assert list(wrapper.current_transitions) == [2]
    assert wrapper.current_transitions[2]["systems"]["add"] == ["water"]


def test_whole_task_reset_clears_rollback_before_next_attempt(button_server):
    server = button_server
    collector = server._session_collector
    collector.server = server
    calls = []
    collector.abort.side_effect = lambda reason: calls.append("archive")
    collector.begin_attempt = Mock(side_effect=lambda: calls.append("begin"))
    server.env.reset = Mock(side_effect=lambda: calls.append("reset"))
    server.robot.reset_joint_pos = server.robot.get_joint_positions()
    server.robot.base_control_idx = th.tensor([0, 1, 5])
    server.robot.keep_still = Mock()
    server._needs_initial_file_update = False
    server.instance_id = None
    server._rollback_checkpoint_idx = 1

    og_robot.OGRobotServer.reset(server)

    assert calls == ["archive", "reset", "begin"]
    assert server._rollback_checkpoint_idx is None
    assert server._waiting_to_resume
