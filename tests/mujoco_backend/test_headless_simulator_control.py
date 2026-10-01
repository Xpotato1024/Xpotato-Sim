from __future__ import annotations

from xpotato_sim.runtime.execution.command_routes import project_joint_position_command

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

import pytest

from tests.support.mujoco_doubles import NoOpMuJoCoSimulator
from xpotato_sim.schemas import JointCommand, MotionCommand


def test_headless_simulator_reports_generic_model_joint_contract_mismatch() -> None:
    simulator = build_fast_arm_simulator()
    command = MotionCommand(timestamp_s=1.0, joint=JointCommand(joint_angles_rad=(0.1, 0.2, 0.3)))

    simulator.apply_joint_position_command(project_joint_position_command(command))
    simulator.record_motion_command_envelope(command)

    with pytest.raises(ValueError, match="model qpos contract"):
        simulator.step(1.0 / 60.0)


def test_noop_mujoco_simulator_still_retains_and_steps() -> None:
    simulator = NoOpMuJoCoSimulator()
    command = MotionCommand(timestamp_s=1.0, joint=JointCommand())

    simulator.record_motion_command_envelope(command)
    simulator.step(0.5)
    state = simulator.snapshot()

    assert simulator.last_command == command
    assert state.frame_index == 1
    assert state.time_s == 0.5
