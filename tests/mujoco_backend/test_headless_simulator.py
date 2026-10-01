from __future__ import annotations

from xpotato_sim.runtime.execution.command_routes import project_joint_position_command

from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

import mujoco
import pytest

from xpotato_sim.schemas import (
    JointCommand,
    JointPositionCommand,
    MotionCommand,
    MuJoCoState,
    TargetCommand,
)


def test_headless_simulator_from_default_fast_arm_loads_scene() -> None:
    simulator = build_fast_arm_simulator()

    assert simulator.model_path.name == "scene.xml"
    assert simulator.snapshot().qpos == pytest.approx(
        tuple(simulator.model.key(FAST_ARM_ROBOT_PROFILE.initial_keyframe_name).qpos)
    )


def test_headless_simulator_reset_restores_canonical_keyframe_state() -> None:
    simulator = build_fast_arm_simulator()
    simulator.apply_qpos_command(
        JointCommand(joint_angles_rad=(0.1, -0.2, 0.3, -0.4))
    )
    simulator.step(1.0 / 60.0)

    simulator.reset()
    state = simulator.snapshot()

    assert state.qpos == pytest.approx(
        tuple(simulator.model.key(FAST_ARM_ROBOT_PROFILE.initial_keyframe_name).qpos)
    )
    assert state.qvel == pytest.approx(tuple(0.0 for _ in state.qvel))
    assert state.frame_index == 0
    assert state.time_s == pytest.approx(0.0)
    assert simulator.last_command is None


def test_headless_simulator_keeps_command_and_advances_frame_index_with_mj_step() -> None:
    simulator = build_fast_arm_simulator()
    command = MotionCommand(timestamp_s=1.0, joint=JointCommand())

    with pytest.raises(ValueError, match="at least one joint angle"):
        project_joint_position_command(command)
    simulator.record_motion_command_envelope(command)
    simulator.step(1.0 / 60.0)
    state = simulator.snapshot()

    assert isinstance(state, MuJoCoState)
    assert state.frame_index == 1
    assert state.time_s > 0.0
    assert any(site.name == "tip" for site in state.sites)
    assert any(body.name == "base_link" for body in state.bodies)


def test_headless_simulator_reflects_joint_command_into_qpos() -> None:
    simulator = build_fast_arm_simulator()
    command = MotionCommand(
        timestamp_s=1.0,
        joint=JointCommand(joint_angles_rad=(0.1, -0.2, 0.3, -0.4)),
    )

    simulator.apply_joint_position_command(project_joint_position_command(command))
    simulator.record_motion_command_envelope(command)
    simulator.step(1.0 / 60.0)
    state = simulator.snapshot()

    assert state.qpos[:4] == pytest.approx((0.1, -0.2, 0.3, -0.4))
    assert state.frame_index == 1
    assert state.time_s > 0.0


def test_headless_simulator_position_commands_clear_stale_velocity_before_mj_step() -> None:
    simulator = build_fast_arm_simulator()
    bad_qacc_warning = int(mujoco.mjtWarning.mjWARN_BADQACC)
    command = MotionCommand(
        timestamp_s=1.0,
        joint=JointCommand(joint_angles_rad=(0.1, -0.2, 0.3, -0.4)),
    )

    simulator.apply_joint_position_command(project_joint_position_command(command))
    simulator.record_motion_command_envelope(command)
    for _ in range(7):
        simulator.step(1.0 / 60.0)

    assert int(simulator.data.warning.number[bad_qacc_warning]) == 0
    assert simulator.data.time == pytest.approx(7.0 / 60.0)
    assert tuple(simulator.data.qvel) == pytest.approx((0.0,) * len(simulator.data.qvel))


def test_headless_simulator_typed_joint_position_command_preserves_qpos_and_qvel() -> None:
    simulator = build_fast_arm_simulator()
    command = JointPositionCommand(
        timestamp_s=1.0,
        joint_angles_rad=(0.1, -0.2, 0.3, -0.4),
    )

    simulator.apply_joint_position_command(command)
    simulator.step(1.0 / 60.0)
    state = simulator.snapshot()

    assert simulator.last_joint_position_command is command
    assert simulator.last_command is None
    assert state.qpos[:4] == pytest.approx(command.joint_angles_rad)
    assert state.qvel == pytest.approx((0.0,) * len(state.qvel))


def test_headless_simulator_rejects_non_positive_dt_s() -> None:
    simulator = build_fast_arm_simulator()

    with pytest.raises(ValueError, match="dt_s must be positive"):
        simulator.step(0.0)

    with pytest.raises(ValueError, match="dt_s must be positive"):
        simulator.step(-1.0 / 60.0)


def test_headless_simulator_rejects_target_commands_explicitly() -> None:
    simulator = build_fast_arm_simulator()
    command = MotionCommand(timestamp_s=1.0, target=TargetCommand())
    before = simulator.snapshot()
    with pytest.raises(TypeError, match="JointPositionCommand"):
        simulator.apply_joint_position_command(command)
    with pytest.raises(ValueError, match="requires MotionCommand.joint"):
        project_joint_position_command(command)
    assert simulator.snapshot() == before


def test_headless_simulator_rejects_unsupported_joint_velocity_shape() -> None:
    simulator = build_fast_arm_simulator()
    command = MotionCommand(
        timestamp_s=1.0,
        joint=JointCommand(
            joint_angles_rad=(0.1, -0.2, 0.3, -0.4),
            joint_velocities_rad_s=(0.0, 0.0, 0.0, 0.0),
        ),
    )

    before = simulator.snapshot()
    with pytest.raises(ValueError, match="does not accept joint velocities"):
        project_joint_position_command(command)
    assert simulator.snapshot() == before


def test_headless_simulator_retains_pending_command_after_step() -> None:
    simulator = build_fast_arm_simulator()
    command = MotionCommand(
        timestamp_s=1.0,
        joint=JointCommand(joint_angles_rad=(0.1, -0.2, 0.3, -0.4)),
    )

    simulator.apply_joint_position_command(project_joint_position_command(command))
    simulator.record_motion_command_envelope(command)
    simulator.step(1.0 / 60.0)

    assert simulator.last_command is command
    assert simulator._pending_joint_position_command == project_joint_position_command(command)
    assert not hasattr(simulator, "apply_command")
