"""Runtime forward-kinematics evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from xpotato_sim.kinematics import ForwardKinematicsSolver
from xpotato_sim.schemas import JointCommand, Vector3

_FK_ENDPOINT_UNIT = "meter"
_FK_ENDPOINT_COORDINATE_FRAME = "solver-defined frame"


def _coerce_joint_angles(name: str, values: Sequence[float]) -> tuple[float, ...]:
    joint_angles_rad = tuple(float(value) for value in values)
    if not joint_angles_rad:
        raise ValueError(f"{name} must contain at least one joint angle")

    return joint_angles_rad


@dataclass(frozen=True, slots=True)
class RuntimeForwardKinematicsEvaluation:
    input_joint_angles_rad: tuple[float, ...]
    solver_joint_angles_rad: tuple[float, ...]
    endpoint_m: Vector3
    unit: str = _FK_ENDPOINT_UNIT
    coordinate_frame: str = _FK_ENDPOINT_COORDINATE_FRAME
    solver_joint_count: int | None = None


def _resolve_solver_joint_angles(
    joint_angles_rad: Sequence[float],
    *,
    solver_joint_count: int | None = None,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    input_joint_angles_rad = _coerce_joint_angles("joint_angles_rad", joint_angles_rad)

    if solver_joint_count is None:
        return input_joint_angles_rad, input_joint_angles_rad

    if solver_joint_count <= 0:
        raise ValueError("solver_joint_count must be positive")

    if len(input_joint_angles_rad) < solver_joint_count:
        raise ValueError(
            "joint_angles_rad length does not satisfy the FK solver contract: "
            f"expected at least {solver_joint_count}, got {len(input_joint_angles_rad)}"
        )

    return input_joint_angles_rad, input_joint_angles_rad[:solver_joint_count]


def evaluate_fk_endpoint_from_qpos(
    solver: ForwardKinematicsSolver,
    joint_angles_rad: Sequence[float],
    *,
    solver_joint_count: int | None = None,
) -> RuntimeForwardKinematicsEvaluation:
    input_joint_angles_rad, solver_joint_angles_rad = _resolve_solver_joint_angles(
        joint_angles_rad,
        solver_joint_count=solver_joint_count,
    )

    endpoint_m = solver.forward(solver_joint_angles_rad)
    return RuntimeForwardKinematicsEvaluation(
        input_joint_angles_rad=input_joint_angles_rad,
        solver_joint_angles_rad=solver_joint_angles_rad,
        endpoint_m=endpoint_m,
        solver_joint_count=solver_joint_count,
    )


def evaluate_fk_endpoint_from_joint_command(
    solver: ForwardKinematicsSolver,
    joint_command: JointCommand,
    *,
    solver_joint_count: int | None = None,
) -> RuntimeForwardKinematicsEvaluation:
    return evaluate_fk_endpoint_from_qpos(
        solver,
        joint_command.joint_angles_rad,
        solver_joint_count=solver_joint_count,
    )


__all__ = [
    "RuntimeForwardKinematicsEvaluation",
    "evaluate_fk_endpoint_from_joint_command",
    "evaluate_fk_endpoint_from_qpos",
]
