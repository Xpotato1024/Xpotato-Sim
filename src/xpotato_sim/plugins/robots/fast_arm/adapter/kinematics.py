"""Selfrionette Protocol/schema adapters over the pure fast_arm core solvers."""

from __future__ import annotations

from dataclasses import dataclass

from fast_arm_core.kinematics import (
    FAST_ARM_ENDPOINT_BASE_POSITION_M,
    FAST_ARM_ENDPOINT_JOINT_COUNT,
    FAST_ARM_ENDPOINT_LINK_LENGTHS_M,
    FastArmEndpointForwardKinematics,
    FastArmEndpointInverseKinematics,
)
from fast_arm_core.model_kinematics import (
    FAST_ARM_MUJOCO_MODEL_JOINT_REFS_RAD,
    FAST_ARM_MUJOCO_MODEL_TIP_SITE_NAME,
    FastArmModelForwardKinematics,
)
from xpotato_sim.schemas import JointCommand, Vector3


@dataclass(frozen=True, slots=True)
class FastArmEndpointForwardKinematicsSolver:
    link_lengths_m: tuple[float, float, float] = FAST_ARM_ENDPOINT_LINK_LENGTHS_M
    base_position_m: Vector3 = FAST_ARM_ENDPOINT_BASE_POSITION_M

    def __post_init__(self) -> None:
        FastArmEndpointForwardKinematics(self.link_lengths_m, self.base_position_m)

    def forward(self, joint_angles_rad: tuple[float, ...]) -> Vector3:
        return FastArmEndpointForwardKinematics(
            self.link_lengths_m, self.base_position_m
        ).forward(joint_angles_rad)


@dataclass(frozen=True, slots=True)
class FastArmMuJoCoModelForwardKinematicsSolver:
    tip_site_name: str = FAST_ARM_MUJOCO_MODEL_TIP_SITE_NAME
    joint_refs_rad: tuple[float, float, float, float] = FAST_ARM_MUJOCO_MODEL_JOINT_REFS_RAD
    coordinate_frame: str = "MuJoCo world / scene frame"

    def __post_init__(self) -> None:
        FastArmModelForwardKinematics(
            self.tip_site_name, self.joint_refs_rad, self.coordinate_frame
        )

    def forward(self, qpos_rad: tuple[float, ...]) -> Vector3:
        return FastArmModelForwardKinematics(
            self.tip_site_name, self.joint_refs_rad, self.coordinate_frame
        ).forward(qpos_rad)


@dataclass(frozen=True, slots=True)
class FastArmEndpointInverseKinematicsSolver:
    link_lengths_m: tuple[float, float, float] = FAST_ARM_ENDPOINT_LINK_LENGTHS_M
    base_position_m: Vector3 = FAST_ARM_ENDPOINT_BASE_POSITION_M

    def __post_init__(self) -> None:
        FastArmEndpointInverseKinematics(self.link_lengths_m, self.base_position_m)

    def solve(
        self,
        target_position_m: Vector3,
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> JointCommand:
        result = FastArmEndpointInverseKinematics(
            self.link_lengths_m, self.base_position_m
        ).solve(target_position_m, seed_joint_angles_rad)
        return JointCommand(joint_angles_rad=result)


__all__ = [
    "FAST_ARM_ENDPOINT_BASE_POSITION_M",
    "FAST_ARM_ENDPOINT_JOINT_COUNT",
    "FAST_ARM_ENDPOINT_LINK_LENGTHS_M",
    "FAST_ARM_MUJOCO_MODEL_JOINT_REFS_RAD",
    "FAST_ARM_MUJOCO_MODEL_TIP_SITE_NAME",
    "FastArmEndpointForwardKinematicsSolver",
    "FastArmEndpointInverseKinematicsSolver",
    "FastArmMuJoCoModelForwardKinematicsSolver",
]
