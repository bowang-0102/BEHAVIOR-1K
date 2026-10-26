"""Check startup guidance without connecting hardware or running the control loop."""

from unittest.mock import Mock

import pytest

from joylo.scripts import run_joylo


@pytest.mark.parametrize("collection", [False, True])
def test_startup_help_matches_the_server_mode(monkeypatch, capsys, collection):
    env = Mock()
    env.get_obs.return_value = {"session_collection": collection}
    agent = Mock()
    agent.act.side_effect = KeyboardInterrupt
    monkeypatch.setattr(run_joylo, "RobotEnv", Mock(return_value=env))
    monkeypatch.setattr(run_joylo, "PrintRobot", Mock())
    monkeypatch.setattr(run_joylo, "DynamixelArmAgent", Mock())
    monkeypatch.setattr(run_joylo, "BimanualAgent", Mock(return_value=agent))

    with pytest.raises(KeyboardInterrupt):
        run_joylo.main(run_joylo.Args(mock=True, gello_port="unused", use_joycons=False))

    output = capsys.readouterr().out
    assert "Hardware connected. Follow the simulator prompts." in output
    assert "Time passed" not in output and "Start 🚀" not in output
    if collection:
        assert "Right: Start or resume recording." in output
        assert "Left: Check and save the current subtask." in output
        assert "Home: Load the next instance." in output and "R: Rerecord this instance." in output
        assert "gripper lights" not in output and "Home reset" not in output
    else:
        assert "Left/Right gripper lights | Home reset" in output
        assert "next instance" not in output
