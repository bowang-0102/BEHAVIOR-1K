"""Check gripper synchronization without connecting physical JoyCons."""

from unittest.mock import Mock

import pytest
import torch as th

from gello.agents import joycon_agent


@pytest.fixture
def joycon(monkeypatch, tmp_path):
    controllers = []
    for side in ("left", "right"):
        controller = Mock()
        for method in (
            "get_stick_left_horizontal", "get_stick_left_vertical",
            "get_stick_right_horizontal", "get_stick_right_vertical",
            "get_button_l_stick", "get_button_r_stick",
            "get_button_up", "get_button_down", "get_button_zl", "get_button_zr",
            "get_button_minus", "get_button_plus", "get_button_x", "get_button_y",
            "get_button_b", "get_button_a", "get_button_capture", "get_button_home",
            "get_button_left", "get_button_right",
        ):
            getattr(controller, method).return_value = 0
        controllers.append(controller)
        (tmp_path / f"joycon_calibration_{side}.yaml").write_text(
            "joystick:\n  horizontal:\n    limits: [-1, 1]\n  vertical:\n    limits: [-1, 1]\n"
        )
    monkeypatch.setattr(joycon_agent, "get_L_id", lambda: (1, 2, "left"))
    monkeypatch.setattr(joycon_agent, "get_R_id", lambda: (1, 2, "right"))
    monkeypatch.setattr(joycon_agent, "RumbleJoyCon", Mock(side_effect=controllers))
    agent = joycon_agent.JoyconAgent(str(tmp_path), enable_rumble=False)
    obs = {
        "control_sync_generation": 1,
        "control_sync_gripper_actions": {"left": 1, "right": -1},
    }
    return agent, obs


def test_grippers_sync_on_start_and_repeated_scene_restores(joycon):
    agent, obs = joycon
    agent.gripper_info["-"]["status"] = -1
    agent.gripper_info["+"]["status"] = -1
    for generation, targets in enumerate(
        ({"left": 1, "right": -1}, {"left": -1, "right": 1}, {"left": 1, "right": 1}), start=1
    ):
        obs["control_sync_generation"] = generation
        obs["control_sync_gripper_actions"] = targets
        for arm, target in targets.items():
            agent.gripper_info[arm]["status"] = -target
            agent.gripper_info[arm]["cooldown"] = 5

        action = agent.act(obs)

        th.testing.assert_close(action[5:7], th.tensor([targets["left"], targets["right"]], dtype=th.float32))
        th.testing.assert_close(action[7:9], th.tensor([-1.0, -1.0]))


@pytest.mark.parametrize("held_during_restore", [False, True])
def test_sync_preserves_toggles_and_requires_new_press_after_held_button(joycon, held_during_restore):
    agent, obs = joycon
    agent.jc_left.get_button_zl.return_value = held_during_restore
    agent.jc_right.get_button_zr.return_value = held_during_restore

    th.testing.assert_close(agent.act(obs)[5:7], th.tensor([1.0, -1.0]))
    for _ in range(agent.max_gripper_cooldown + 1):
        th.testing.assert_close(agent.act(obs)[5:7], th.tensor([1.0, -1.0]))

    agent.jc_left.get_button_zl.return_value = False
    agent.jc_right.get_button_zr.return_value = False
    agent.act(obs)
    agent.jc_left.get_button_zl.return_value = True
    agent.jc_right.get_button_zr.return_value = True
    th.testing.assert_close(agent.act(obs)[5:7], th.tensor([-1.0, 1.0]))
    # The same sync generation must not overwrite the operator's new choice.
    for _ in range(agent.max_gripper_cooldown + 1):
        th.testing.assert_close(agent.act(obs)[5:7], th.tensor([-1.0, 1.0]))

    obs["control_sync_generation"] += 1
    th.testing.assert_close(agent.act(obs)[5:7], th.tensor([1.0, -1.0]))
