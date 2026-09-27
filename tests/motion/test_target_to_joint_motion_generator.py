from __future__ import annotations

from dataclasses import dataclass

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import (
    FastArmEndpointForwardKinematicsSolver,
    FastArmEndpointInverseKinematicsSolver,
)
from xpotato_sim.motion import TargetToJointMotionGenerator
from xpotato_sim.schemas import InputIntent, JointCommand
from tests.support.kinematics_solver_doubles import (
    FailingInverseKinematicsSolver,
    FixedInverseKinematicsSolver,
    SeedSensitiveInverseKinematicsSolver,
)


def assert_metadata_contains(metadata: dict[str, object], expected: dict[str, object]) -> None:
    for key, value in expected.items():
        assert metadata[key] == value


@dataclass(slots=True)
class FutureTargetPositionCompatibleIntent:
    source: str
    timestamp_s: float
    target_delta_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    joint_delta_rad: tuple[float, ...] = ()
    metadata: dict[str, object] | None = None
    desired_endpoint_m: tuple[float, float, float] | None = None
    target_position_m: tuple[float, float, float] | None = None


def test_target_to_joint_motion_generator_propagates_target_seed_and_solver_result() -> None:
    target_position_m = (0.35, 0.0, 0.25)
    solver_result = JointCommand(joint_angles_rad=(0.3, -0.2))
    solver = FixedInverseKinematicsSolver(joint_command=solver_result)
    intent = FutureTargetPositionCompatibleIntent(
        source="replay",
        timestamp_s=2.0,
        metadata={"origin": "concrete-ik"},
        target_position_m=target_position_m,
    )

    command = TargetToJointMotionGenerator(
        solver,
        seed_joint_angles_rad=(0.0, -0.2),
    ).update(intent, dt_s=0.016)

    assert command.timestamp_s == 2.0
    assert command.target is None
    assert_metadata_contains(command.metadata, {"origin": "concrete-ik"})
    assert command.joint is not None
    assert command.joint == solver_result
    assert solver.calls == [(target_position_m, (0.0, -0.2))]


def test_target_to_joint_motion_generator_prefers_desired_endpoint_metadata_over_target_position_m() -> None:
    desired_endpoint_m = (0.35, 0.0, 0.25)
    fallback_target_position_m = (9.0, 8.0, 7.0)
    solver = FixedInverseKinematicsSolver(joint_command=JointCommand(joint_angles_rad=(0.3, -0.2)))
    intent = FutureTargetPositionCompatibleIntent(
        source="replay",
        timestamp_s=2.0,
        metadata={"origin": "concrete-ik", "target_position_m": fallback_target_position_m},
        desired_endpoint_m=desired_endpoint_m,
        target_position_m=fallback_target_position_m,
    )

    command = TargetToJointMotionGenerator(
        solver,
        seed_joint_angles_rad=(0.0, -0.2),
    ).update(intent, dt_s=0.016)

    assert command.timestamp_s == 2.0
    assert command.target is None
    assert_metadata_contains(
        command.metadata,
        {"origin": "concrete-ik", "target_position_m": fallback_target_position_m},
    )
    assert command.joint is not None
    assert command.joint == solver.joint_command
    assert solver.calls == [(desired_endpoint_m, (0.0, -0.2))]


def test_target_to_joint_motion_generator_reports_target_position_m_for_invalid_fallback_metadata() -> None:
    solver = FixedInverseKinematicsSolver(joint_command=JointCommand(joint_angles_rad=(0.3, -0.2)))
    intent = FutureTargetPositionCompatibleIntent(
        source="replay",
        timestamp_s=2.0,
        metadata={"origin": "concrete-ik", "target_position_m": (0.1, 0.2)},
    )

    with pytest.raises(ValueError, match="target_position_m must contain exactly three values"):
        TargetToJointMotionGenerator(solver).update(intent, dt_s=0.016)
    assert solver.calls == []


def test_target_to_joint_motion_generator_uses_metadata_target_position_and_pads_qpos_for_backend() -> None:
    target_position_m = (0.35, 0.0, 0.25)
    solver = FixedInverseKinematicsSolver(joint_command=JointCommand(joint_angles_rad=(0.3, -0.2)))
    intent = InputIntent(
        source="replay",
        timestamp_s=2.0,
        metadata={"origin": "concrete-ik", "target_position_m": target_position_m},
    )

    command = TargetToJointMotionGenerator(
        solver,
        seed_joint_angles_rad=(0.0, -0.2),
        qpos_joint_count=4,
    ).update(intent, dt_s=0.016)

    assert command.timestamp_s == 2.0
    assert command.target is None
    assert_metadata_contains(
        command.metadata,
        {"origin": "concrete-ik", "target_position_m": target_position_m},
    )
    assert command.joint is not None
    assert command.joint.joint_angles_rad[:2] == pytest.approx((0.3, -0.2), abs=1e-9)
    assert command.joint.joint_angles_rad[2:] == (0.0, 0.0)
    assert command.joint != JointCommand()
    assert len(command.joint.joint_angles_rad) == 4
    assert solver.calls == [(target_position_m, (0.0, -0.2))]


def test_target_to_joint_motion_generator_keeps_four_dof_fast_arm_seed_and_does_not_pad_output() -> None:
    fk = FastArmEndpointForwardKinematicsSolver()
    solver = FastArmEndpointInverseKinematicsSolver()
    target_joint_angles_rad = (0.15, -0.25, 0.3, -0.05)
    desired_endpoint_m = fk.forward(target_joint_angles_rad)
    intent = InputIntent(
        source="replay",
        timestamp_s=2.0,
        metadata={"origin": "fast-arm", "desired_endpoint_m": desired_endpoint_m},
    )

    command = TargetToJointMotionGenerator(
        solver,
        current_qpos_rad=(0.0, 0.0, 0.0, 0.0),
        qpos_joint_count=4,
    ).update(intent, dt_s=0.016)

    assert command.timestamp_s == 2.0
    assert command.target is None
    assert_metadata_contains(
        command.metadata,
        {"origin": "fast-arm", "desired_endpoint_m": desired_endpoint_m},
    )
    assert command.joint is not None
    assert len(command.joint.joint_angles_rad) == 4
    assert command.joint.joint_angles_rad[2:] != (0.0, 0.0)
    assert fk.forward(command.joint.joint_angles_rad) == pytest.approx(desired_endpoint_m, abs=1e-4)


def test_target_to_joint_motion_generator_converts_target_rejection_to_safe_hold() -> None:
    target_position_m = (0.58, 0.02, 0.11)
    solver = FailingInverseKinematicsSolver(error_message="target_position_m did not converge")
    intent = InputIntent(
        source="replay",
        timestamp_s=3.0,
        metadata={"origin": "generic", "desired_endpoint_m": target_position_m},
    )

    command = TargetToJointMotionGenerator(
        solver,
        current_qpos_rad=(0.1, -0.1, 0.2, -0.2),
        qpos_joint_count=4,
    ).update(intent, dt_s=0.016)

    assert command.target is None
    assert command.joint is not None
    assert command.joint.joint_angles_rad == (0.1, -0.1, 0.2, -0.2)
    assert command.metadata["target_rejected"] is True
    assert command.metadata["target_rejection_reason"] == "target_non_convergence"
    assert command.metadata["target_rejection_message"] == "target_position_m did not converge"
    assert command.metadata["runtime_input_safety_applied"] is True
    assert solver.calls == [(target_position_m, (0.1, -0.1, 0.2, -0.2))]


def test_target_to_joint_motion_generator_rejects_unreachable_fast_arm_targets_with_specific_reason() -> None:
    solver = FastArmEndpointInverseKinematicsSolver()
    intent = InputIntent(
        source="replay",
        timestamp_s=3.0,
        metadata={"origin": "fast-arm", "desired_endpoint_m": (10.0, 0.0, 0.0)},
    )

    command = TargetToJointMotionGenerator(
        solver,
        current_qpos_rad=(0.1, -0.1, 0.2, -0.2),
        qpos_joint_count=4,
    ).update(intent, dt_s=0.016)

    assert command.target is None
    assert command.joint is not None
    assert command.joint.joint_angles_rad == (0.1, -0.1, 0.2, -0.2)
    assert command.metadata["target_rejected"] is True
    assert command.metadata["target_rejection_reason"] == "target_unreachable"
    assert command.metadata["target_rejection_message"] == "target_position_m is outside the reachable workspace"


def test_target_to_joint_motion_generator_prefers_four_dof_current_qpos_seed_without_planar_fallback() -> None:
    current_qpos_rad = (0.4, -0.3, 0.2, -0.1)
    solver = SeedSensitiveInverseKinematicsSolver(
        joint_command=JointCommand(joint_angles_rad=current_qpos_rad),
        accepted_seed_length=4,
        error_message="seed_joint_angles_rad must contain exactly four values for this solver",
    )
    intent = InputIntent(
        source="replay",
        timestamp_s=4.0,
        metadata={"origin": "fast-arm", "desired_endpoint_m": (0.58, 0.0, 0.1)},
    )

    command = TargetToJointMotionGenerator(
        solver,
        current_qpos_rad=current_qpos_rad,
        qpos_joint_count=4,
    ).update(intent, dt_s=0.016)

    assert solver.calls == [(
        (0.58, 0.0, 0.1),
        current_qpos_rad,
    )]
    assert command.joint is not None
    assert command.joint.joint_angles_rad == current_qpos_rad


def test_target_to_joint_motion_generator_falls_back_to_supported_seed_shape_when_needed() -> None:
    target_position_m = (0.35, 0.17, 0.25)
    solver_result = JointCommand(joint_angles_rad=(0.3, -0.2))
    solver = SeedSensitiveInverseKinematicsSolver(
        joint_command=solver_result,
        accepted_seed_length=2,
        error_message="seed_joint_angles_rad must contain exactly two values for this solver",
    )
    intent = InputIntent(
        source="replay",
        timestamp_s=5.0,
        metadata={"origin": "generic-seed-fallback", "desired_endpoint_m": target_position_m},
    )

    command = TargetToJointMotionGenerator(
        solver,
        current_qpos_rad=(0.0, -0.2, 0.4, -0.1),
    ).update(intent, dt_s=0.016)

    assert command.joint is not None
    assert command.joint == solver_result
    assert solver.calls == [
        (target_position_m, (0.0, -0.2, 0.4, -0.1)),
        (target_position_m, (0.0, -0.2)),
    ]


def test_target_to_joint_motion_generator_propagates_unrecognized_solver_failure() -> None:
    target_position_m = (0.35, 0.0, 0.25)
    solver = FailingInverseKinematicsSolver(error_message="solver service unavailable")

    with pytest.raises(ValueError, match="solver service unavailable"):
        TargetToJointMotionGenerator(solver).update(
            InputIntent(
                source="replay",
                timestamp_s=6.0,
                metadata={"desired_endpoint_m": target_position_m},
            ),
            dt_s=0.016,
        )

    assert solver.calls == [(target_position_m, None)]


def test_target_to_joint_motion_generator_preserves_discontinuity_boundary_and_metadata() -> None:
    current_qpos_rad = (0.0, 0.0)
    target_position_m = (0.35, 0.0, 0.25)
    accepted_solver = FixedInverseKinematicsSolver(
        joint_command=JointCommand(joint_angles_rad=(0.3, 0.4)),
    )
    accepted_command = TargetToJointMotionGenerator(
        accepted_solver,
        current_qpos_rad=current_qpos_rad,
        discontinuity_threshold_rad=0.5,
        discontinuity_threshold_label="test threshold",
    ).update(
        InputIntent(
            source="replay",
            timestamp_s=7.0,
            metadata={"unrelated": "preserved", "desired_endpoint_m": target_position_m},
        ),
        dt_s=0.016,
    )

    assert accepted_command.joint is not None
    assert accepted_command.joint == accepted_solver.joint_command
    assert accepted_command.metadata["unrelated"] == "preserved"
    assert accepted_command.metadata["qpos_discontinuity_norm_rad"] == pytest.approx(0.5)
    assert accepted_command.metadata["target_discontinuity_threshold_rad"] == 0.5
    assert accepted_command.metadata["target_discontinuity_threshold_label"] == "test threshold"

    rejected_solver = FixedInverseKinematicsSolver(
        joint_command=JointCommand(joint_angles_rad=(0.6, 0.0)),
    )
    rejected_command = TargetToJointMotionGenerator(
        rejected_solver,
        current_qpos_rad=current_qpos_rad,
        discontinuity_threshold_rad=0.5,
        discontinuity_threshold_label="test threshold",
    ).update(
        InputIntent(
            source="replay",
            timestamp_s=8.0,
            metadata={"unrelated": "preserved", "desired_endpoint_m": target_position_m},
        ),
        dt_s=0.016,
    )

    assert rejected_command.joint is not None
    assert rejected_command.joint.joint_angles_rad == current_qpos_rad
    assert rejected_command.metadata["unrelated"] == "preserved"
    assert rejected_command.metadata["target_rejected"] is True
    assert rejected_command.metadata["target_rejection_reason"] == "target_discontinuous"
    assert rejected_command.metadata["target_discontinuity_threshold_label"] == "test threshold"
    assert rejected_solver.calls == [(target_position_m, current_qpos_rad)]


def test_target_to_joint_motion_generator_rejects_solver_output_longer_than_qpos_contract() -> None:
    target_position_m = (0.35, 0.0, 0.25)
    solver = FixedInverseKinematicsSolver(
        joint_command=JointCommand(joint_angles_rad=(0.1, 0.2, 0.3)),
    )

    with pytest.raises(ValueError, match="solver output is longer than the configured qpos joint count"):
        TargetToJointMotionGenerator(solver, qpos_joint_count=2).update(
            InputIntent(
                source="replay",
                timestamp_s=9.0,
                metadata={"desired_endpoint_m": target_position_m},
            ),
            dt_s=0.016,
        )
