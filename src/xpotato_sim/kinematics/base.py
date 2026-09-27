from __future__ import annotations

from typing import Protocol, runtime_checkable

from xpotato_sim.schemas import JointCommand, Vector3


@runtime_checkable
class ForwardKinematicsSolver(Protocol):
    def forward(self, joint_angles_rad: tuple[float, ...]) -> Vector3:
        ...


@runtime_checkable
class InverseKinematicsSolver(Protocol):
    def solve(
        self,
        target_position_m: Vector3,
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> JointCommand:
        ...
