from __future__ import annotations

import pytest

from xpotato_sim.kinematics import ForwardKinematicsSolver, InverseKinematicsSolver
from xpotato_sim.schemas import JointCommand
from tests.support.kinematics_solver_doubles import (
    FailingForwardKinematicsSolver,
    FailingInverseKinematicsSolver,
    FixedForwardKinematicsSolver,
    FixedInverseKinematicsSolver,
    SeedSensitiveInverseKinematicsSolver,
)


def test_fixed_fk_is_protocol_compatible_returns_literal_result_and_records_exact_input() -> None:
    endpoint_m = (0.1, 0.2, 0.3)
    solver = FixedForwardKinematicsSolver(endpoint_m=endpoint_m)
    qpos = (0.4, -0.5, 0.6)

    assert isinstance(solver, ForwardKinematicsSolver)
    assert solver.forward(qpos) == endpoint_m
    assert solver.calls == [qpos]


def test_fixed_ik_is_protocol_compatible_returns_literal_result_and_preserves_call_order() -> None:
    command = JointCommand(joint_angles_rad=(0.4, -0.5))
    solver = FixedInverseKinematicsSolver(joint_command=command)
    first_target = (0.1, 0.2, 0.3)
    second_target = (0.4, 0.5, 0.6)

    assert isinstance(solver, InverseKinematicsSolver)
    assert solver.solve(first_target) is command
    assert solver.solve(second_target, seed_joint_angles_rad=(0.7, -0.8)) is command
    assert solver.calls == [
        (first_target, None),
        (second_target, (0.7, -0.8)),
    ]


def test_failing_doubles_record_inputs_and_raise_configured_value_error() -> None:
    fk = FailingForwardKinematicsSolver(error_message="fk unavailable")
    ik = FailingInverseKinematicsSolver(error_message="ik unavailable")

    with pytest.raises(ValueError, match="fk unavailable"):
        fk.forward((0.1, 0.2))
    with pytest.raises(ValueError, match="ik unavailable"):
        ik.solve((0.3, 0.4, 0.5), seed_joint_angles_rad=None)

    assert fk.calls == [(0.1, 0.2)]
    assert ik.calls == [((0.3, 0.4, 0.5), None)]


def test_seed_sensitive_double_rejects_wrong_shape_then_accepts_configured_shape() -> None:
    target = (0.1, 0.2, 0.3)
    command = JointCommand(joint_angles_rad=(0.4, -0.5))
    solver = SeedSensitiveInverseKinematicsSolver(
        joint_command=command,
        accepted_seed_length=2,
        error_message="seed_joint_angles_rad must contain exactly two values",
    )

    with pytest.raises(ValueError, match="seed_joint_angles_rad"):
        solver.solve(target, seed_joint_angles_rad=(0.6,))
    assert solver.solve(target, seed_joint_angles_rad=(0.6, -0.7)) is command
    assert solver.calls == [
        (target, (0.6,)),
        (target, (0.6, -0.7)),
    ]


def test_doubles_do_not_mutate_inputs() -> None:
    target = (0.1, 0.2, 0.3)
    seed = (0.4, -0.5)
    solver = FixedInverseKinematicsSolver(joint_command=JointCommand(joint_angles_rad=(0.6, 0.7)))

    solver.solve(target, seed_joint_angles_rad=seed)

    assert target == (0.1, 0.2, 0.3)
    assert seed == (0.4, -0.5)
    assert solver.calls == [(target, seed)]
