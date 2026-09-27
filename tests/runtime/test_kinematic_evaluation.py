from __future__ import annotations

import pytest

from xpotato_sim.runtime.evaluation.kinematics import (
    RuntimeForwardKinematicsEvaluation,
    evaluate_fk_endpoint_from_joint_command,
    evaluate_fk_endpoint_from_qpos,
)
from xpotato_sim.schemas import JointCommand
from tests.support.kinematics_solver_doubles import (
    FailingForwardKinematicsSolver,
    FixedForwardKinematicsSolver,
)


def test_evaluate_fk_endpoint_from_joint_command_returns_runtime_evaluation_in_solver_defined_frame() -> None:
    solver = FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0))
    joint_command = JointCommand(joint_angles_rad=(0.3, -0.2))

    evaluation = evaluate_fk_endpoint_from_joint_command(solver, joint_command)

    assert isinstance(evaluation, RuntimeForwardKinematicsEvaluation)
    assert evaluation.input_joint_angles_rad == (0.3, -0.2)
    assert evaluation.solver_joint_angles_rad == (0.3, -0.2)
    assert evaluation.endpoint_m == (0.3, 0.4, 0.0)
    assert solver.calls == [(0.3, -0.2)]
    assert evaluation.unit == "meter"
    assert evaluation.coordinate_frame == "solver-defined frame"
    assert evaluation.solver_joint_count is None
    assert not hasattr(evaluation, "desired_endpoint_m")
    assert not hasattr(evaluation, "target_position_m")
    assert not hasattr(evaluation, "site_position_m")


def test_evaluate_fk_endpoint_from_qpos_trims_backend_padding_with_explicit_solver_joint_count() -> None:
    solver = FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0))
    padded_qpos_joint_angles_rad = (0.3, -0.2, 0.0, 0.0)

    evaluation = evaluate_fk_endpoint_from_qpos(
        solver,
        padded_qpos_joint_angles_rad,
        solver_joint_count=2,
    )

    assert evaluation.input_joint_angles_rad == padded_qpos_joint_angles_rad
    assert evaluation.solver_joint_angles_rad == (0.3, -0.2)
    assert evaluation.endpoint_m == (0.3, 0.4, 0.0)
    assert evaluation.solver_joint_count == 2
    assert solver.calls == [(0.3, -0.2)]


def test_evaluate_fk_endpoint_from_qpos_rejects_empty_and_too_short_inputs() -> None:
    solver = FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0))

    with pytest.raises(ValueError, match="must contain at least one joint angle"):
        evaluate_fk_endpoint_from_qpos(solver, ())

    with pytest.raises(ValueError, match="expected at least 2, got 1"):
        evaluate_fk_endpoint_from_qpos(solver, (0.3,), solver_joint_count=2)

    assert solver.calls == []


def test_evaluate_fk_endpoint_from_qpos_rejects_non_positive_solver_joint_count() -> None:
    solver = FixedForwardKinematicsSolver(endpoint_m=(0.3, 0.4, 0.0))

    with pytest.raises(ValueError, match="solver_joint_count must be positive"):
        evaluate_fk_endpoint_from_qpos(solver, (0.3, -0.2), solver_joint_count=0)

    assert solver.calls == []


def test_evaluate_fk_endpoint_propagates_solver_failure() -> None:
    solver = FailingForwardKinematicsSolver(error_message="fk unavailable")

    with pytest.raises(ValueError, match="fk unavailable"):
        evaluate_fk_endpoint_from_qpos(solver, (0.3, -0.2))

    assert solver.calls == [(0.3, -0.2)]
