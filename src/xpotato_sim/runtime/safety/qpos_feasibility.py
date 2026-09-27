"""Qpos feasibility safety boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from xpotato_sim.schemas import MotionCommand


@dataclass(frozen=True, slots=True)
class QposFeasibilityDiagnostic:
    """Robot-agnostic diagnostic value emitted by a qpos feasibility guard."""

    code: str
    attributes: tuple[tuple[str, object], ...] = ()

    @classmethod
    def from_mapping(cls, code: str, attributes: Mapping[str, object]) -> "QposFeasibilityDiagnostic":
        return cls(code=code, attributes=tuple(attributes.items()))


@dataclass(frozen=True, slots=True)
class QposFeasibilityResult:
    motion_command: MotionCommand
    accepted: bool
    action: str
    candidate_qpos_rad: tuple[float, ...] | None
    diagnostics: tuple[QposFeasibilityDiagnostic, ...] = ()


@runtime_checkable
class QposFeasibilityGuard(Protocol):
    """Behavioral contract for the common runtime qpos safety boundary."""

    def evaluate(
        self,
        motion_command: MotionCommand,
        *,
        current_qpos_rad: Sequence[float],
    ) -> QposFeasibilityResult:
        ...


@dataclass(frozen=True, slots=True)
class NoOpQposFeasibilityGuard:
    """Explicit generic behavior when no robot-specific guard is injected."""

    def evaluate(
        self,
        motion_command: MotionCommand,
        *,
        current_qpos_rad: Sequence[float],
    ) -> QposFeasibilityResult:
        _ = current_qpos_rad
        candidate_qpos_rad = None
        if motion_command.joint is not None:
            candidate_qpos_rad = tuple(float(value) for value in motion_command.joint.joint_angles_rad)
        return QposFeasibilityResult(
            motion_command=motion_command,
            accepted=True,
            action="accept_no_guard" if candidate_qpos_rad is not None else "accept_no_qpos_candidate",
            candidate_qpos_rad=candidate_qpos_rad,
        )


__all__ = [
    "NoOpQposFeasibilityGuard",
    "QposFeasibilityDiagnostic",
    "QposFeasibilityGuard",
    "QposFeasibilityResult",
]
