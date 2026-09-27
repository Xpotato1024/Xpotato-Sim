"""Small test-only FK/IK doubles for geometry-independent contract tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from xpotato_sim.schemas import JointCommand, Vector3


class ZeroForwardKinematicsSolver:
    """Return a zero endpoint for explicit negative-control tests."""

    def forward(self, joint_angles_rad: tuple[float, ...]) -> Vector3:
        return (0.0, 0.0, 0.0)


class ZeroInverseKinematicsSolver:
    """Return an empty command for explicit negative-control tests."""

    def solve(
        self,
        target_position_m: Vector3,
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> JointCommand:
        return JointCommand()


@dataclass(frozen=True, slots=True)
class FixedForwardKinematicsSolver:
    """Return one configured endpoint and record every exact qpos input."""

    endpoint_m: Vector3
    calls: list[tuple[float, ...]] = field(default_factory=list, init=False, compare=False, repr=False)

    def forward(self, joint_angles_rad: tuple[float, ...]) -> Vector3:
        self.calls.append(joint_angles_rad)
        return self.endpoint_m


@dataclass(frozen=True, slots=True)
class FailingForwardKinematicsSolver:
    """Record FK inputs and raise a configured production-compatible failure."""

    error_message: str = "forward kinematics failed"
    calls: list[tuple[float, ...]] = field(default_factory=list, init=False, compare=False, repr=False)

    def forward(self, joint_angles_rad: tuple[float, ...]) -> Vector3:
        self.calls.append(joint_angles_rad)
        raise ValueError(self.error_message)


@dataclass(frozen=True, slots=True)
class FixedInverseKinematicsSolver:
    """Return one configured joint command and record target/seed arguments."""

    joint_command: JointCommand
    calls: list[tuple[Vector3, tuple[float, ...] | None]] = field(
        default_factory=list,
        init=False,
        compare=False,
        repr=False,
    )

    def solve(
        self,
        target_position_m: Vector3,
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> JointCommand:
        self.calls.append((target_position_m, seed_joint_angles_rad))
        return self.joint_command


@dataclass(frozen=True, slots=True)
class FailingInverseKinematicsSolver:
    """Record IK inputs and raise a configured production-compatible failure."""

    error_message: str = "inverse kinematics failed"
    calls: list[tuple[Vector3, tuple[float, ...] | None]] = field(
        default_factory=list,
        init=False,
        compare=False,
        repr=False,
    )

    def solve(
        self,
        target_position_m: Vector3,
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> JointCommand:
        self.calls.append((target_position_m, seed_joint_angles_rad))
        raise ValueError(self.error_message)


@dataclass(frozen=True, slots=True)
class SeedSensitiveInverseKinematicsSolver:
    """Accept only a configured seed length while preserving every call."""

    joint_command: JointCommand
    accepted_seed_length: int
    error_message: str = "seed_joint_angles_rad has an unsupported shape"
    calls: list[tuple[Vector3, tuple[float, ...] | None]] = field(
        default_factory=list,
        init=False,
        compare=False,
        repr=False,
    )

    def solve(
        self,
        target_position_m: Vector3,
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> JointCommand:
        self.calls.append((target_position_m, seed_joint_angles_rad))
        if seed_joint_angles_rad is None or len(seed_joint_angles_rad) != self.accepted_seed_length:
            raise ValueError(self.error_message)
        return self.joint_command


__all__ = [
    "FailingForwardKinematicsSolver",
    "FailingInverseKinematicsSolver",
    "FixedForwardKinematicsSolver",
    "FixedInverseKinematicsSolver",
    "SeedSensitiveInverseKinematicsSolver",
    "ZeroForwardKinematicsSolver",
    "ZeroInverseKinematicsSolver",
]
