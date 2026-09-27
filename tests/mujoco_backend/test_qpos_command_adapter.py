from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator
from xpotato_sim.plugins.robots.fast_arm.adapter.bundle import (
    FAST_ARM_ROBOT_BUNDLE,
)

import pytest

from xpotato_sim.mujoco_backend.command_adapter import motion_command_to_qpos_command
from xpotato_sim.runtime.execution.command_routes import (
    project_joint_position_command,
)
from xpotato_sim.runtime.experiment.contracts import (
    JOINT_POSITION_COMMAND_V1,
)
from xpotato_sim.schemas import (
    JointCommand,
    JointPositionCommand,
    MotionCommand,
    TargetCommand,
)


def test_motion_command_joint_is_exposed_as_qpos_command_boundary() -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        joint=JointCommand(joint_angles_rad=(0.1, -0.2, 0.3, -0.4)),
    )

    qpos_command = motion_command_to_qpos_command(command)

    assert qpos_command == JointCommand(joint_angles_rad=(0.1, -0.2, 0.3, -0.4))


def test_motion_command_target_is_rejected_in_qpos_command_boundary() -> None:
    command = MotionCommand(timestamp_s=1.0, target=TargetCommand())

    with pytest.raises(ValueError, match="qpos command boundary"):
        motion_command_to_qpos_command(command)


def test_motion_command_target_position_feedback_is_also_not_qpos_boundary() -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        target=TargetCommand(position_m=(0.3, 0.2, 0.1), delta_m=(0.001, 0.0, 0.0)),
    )

    with pytest.raises(ValueError, match="qpos command boundary"):
        motion_command_to_qpos_command(command)


def test_headless_simulator_apply_qpos_command_updates_snapshot() -> None:
    simulator = build_fast_arm_simulator()

    simulator.apply_qpos_command(JointCommand(joint_angles_rad=(0.1, -0.2, 0.3, -0.4)))
    state = simulator.snapshot()

    assert state.qpos[:4] == pytest.approx((0.1, -0.2, 0.3, -0.4))


def test_motion_command_projects_to_typed_joint_position_boundary() -> None:
    command = MotionCommand(
        timestamp_s=1.5,
        target=TargetCommand(position_m=(0.3, 0.2, 0.1)),
        joint=JointCommand(joint_angles_rad=(0.1, -0.2, 0.3, -0.4)),
        metadata={"diagnostic": "preserved-in-envelope-only"},
    )

    assert project_joint_position_command(command) == JointPositionCommand(
        timestamp_s=1.5,
        joint_angles_rad=(0.1, -0.2, 0.3, -0.4),
    )


@pytest.mark.parametrize(
    ("command", "message"),
    (
        (
            MotionCommand(timestamp_s=1.0),
            "requires MotionCommand.joint",
        ),
        (
            MotionCommand(timestamp_s=1.0, target=TargetCommand()),
            "requires MotionCommand.joint",
        ),
        (
            MotionCommand(
                timestamp_s=1.0,
                joint=JointCommand(
                    joint_velocities_rad_s=(0.1, 0.2),
                ),
            ),
            "does not accept joint velocities",
        ),
    ),
)
def test_joint_position_projection_rejects_non_position_envelopes(
    command: MotionCommand,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        project_joint_position_command(command)


def test_fast_arm_joint_position_provider_accepts_only_typed_command() -> None:
    provider = FAST_ARM_ROBOT_BUNDLE.command_semantic_provider(
        JOINT_POSITION_COMMAND_V1
    )

    class _Backend:
        received: list[JointPositionCommand] = []

        def apply_joint_position_command(
            self, command: JointPositionCommand
        ) -> None:
            self.received.append(command)

    backend = _Backend()
    command = JointPositionCommand(
        timestamp_s=1.0,
        joint_angles_rad=(0.1, -0.2, 0.3, -0.4),
    )

    assert provider.command_type is JointPositionCommand
    provider.execute(command, backend=backend)
    assert backend.received == [command]

    with pytest.raises(
        TypeError,
        match="requires JointPositionCommand",
    ):
        provider.execute(
            MotionCommand(
                timestamp_s=1.0,
                joint=JointCommand(
                    joint_angles_rad=command.joint_angles_rad
                ),
            ),
            backend=backend,
        )
