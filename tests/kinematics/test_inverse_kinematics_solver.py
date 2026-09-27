from __future__ import annotations

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import (
    FastArmEndpointForwardKinematicsSolver,
    FastArmEndpointInverseKinematicsSolver,
)
from xpotato_sim.schemas import JointCommand


def test_fast_arm_endpoint_inverse_kinematics_solver_returns_four_joint_angles() -> None:
    fk = FastArmEndpointForwardKinematicsSolver()
    solver = FastArmEndpointInverseKinematicsSolver()

    desired_joint_angles_rad = (0.2, -0.3, 0.25, -0.15)
    target_position_m = fk.forward(desired_joint_angles_rad)

    command = solver.solve(target_position_m, seed_joint_angles_rad=(0.0, 0.0, 0.0, 0.0))

    assert isinstance(command, JointCommand)
    assert len(command.joint_angles_rad) == 4
    assert command.joint_angles_rad != ()
    assert command.joint_angles_rad[2:] != (0.0, 0.0)
    assert fk.forward(command.joint_angles_rad) == pytest.approx(target_position_m, abs=1e-4)


def test_fast_arm_endpoint_inverse_kinematics_solver_accepts_small_y_delta() -> None:
    solver = FastArmEndpointInverseKinematicsSolver()

    command = solver.solve((0.58, 0.04, 0.12), seed_joint_angles_rad=(0.0, 0.0, 0.0, 0.0))

    assert isinstance(command, JointCommand)
    assert len(command.joint_angles_rad) == 4
    assert command.joint_angles_rad != ()


def test_fast_arm_endpoint_inverse_kinematics_solver_rejects_invalid_seed_shape() -> None:
    solver = FastArmEndpointInverseKinematicsSolver()

    with pytest.raises(ValueError, match="exactly four values"):
        solver.solve((0.6, 0.0, 0.1), seed_joint_angles_rad=(0.0, 0.0, 0.0))
