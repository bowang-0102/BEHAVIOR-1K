"""Check hardware retargeting with a fake arm, without opening serial devices."""

from unittest.mock import Mock

import numpy as np
import pytest
import torch as th

from gello.agents.bimanual_agent import BimanualAgent, MotorFeedbackConfig, R1PRO_CONFIG


@pytest.mark.parametrize("waiting_during_restore", [False, True])
def test_scene_restore_retargets_hardware_while_already_paused(waiting_during_restore):
    arm = Mock()
    arm.get_joint_state.return_value = np.zeros(18)
    agent = BimanualAgent(R1PRO_CONFIG, arm, motor_feedback_type=MotorFeedbackConfig.NONE)
    obs = {
        "waiting_to_resume": True,
        "control_sync_generation": 1,
        "arm_left_joint_positions": th.zeros(7),
        "arm_right_joint_positions": th.zeros(7),
    }
    agent.act(obs)
    original_target = arm.set_reset_qpos.call_args.args[0].copy()
    agent.act(obs)
    assert arm.reset.call_count == 1

    obs["control_sync_generation"] = 2
    obs["waiting_to_resume"] = waiting_during_restore
    obs["arm_left_joint_positions"] = th.ones(7) * 0.2
    obs["arm_right_joint_positions"] = th.ones(7) * -0.2
    agent.act(obs)
    restored_target = arm.set_reset_qpos.call_args.args[0]
    assert arm.reset.call_count == 2
    assert not np.array_equal(restored_target, original_target)
    np.testing.assert_allclose(restored_target[:9], 0.2)
    np.testing.assert_allclose(restored_target[9:], -0.2)

    agent.act(obs)
    assert arm.reset.call_count == 2
    obs["waiting_to_resume"] = False
    agent.act(obs)
    arm.start.assert_called_once_with()
    assert not agent._waiting_to_resume
