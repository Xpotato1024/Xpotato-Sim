"""Canonical robot identity and joint convention for fast_arm."""

from __future__ import annotations

from dataclasses import dataclass


FAST_ARM_ID = "fast_arm"
FAST_ARM_MODEL_CONTRACT_VERSION = "fast_arm-mujoco-model/v1"
FAST_ARM_JOINT_NAMES: tuple[str, ...] = (
    "sholder_joint_1",
    "sholder_joint_2",
    "sholder_joint_3",
    "elbow_joint",
)
FAST_ARM_JOINT_AXES: tuple[tuple[float, float, float], ...] = (
    (0.0, -1.0, 0.0),
    (1.0, 0.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
)


@dataclass(frozen=True, slots=True)
class FastArmDefinition:
    robot_id: str = FAST_ARM_ID
    model_contract_version: str = FAST_ARM_MODEL_CONTRACT_VERSION
    joint_names: tuple[str, ...] = FAST_ARM_JOINT_NAMES
    joint_axes: tuple[tuple[float, float, float], ...] = FAST_ARM_JOINT_AXES
    joint_angle_unit: str = "rad"
    solver_coordinate_frame: str = "fast_arm solver-local frame"
    model_coordinate_frame: str = "MuJoCo world / scene frame"
    qpos_dimension: int = 4
    qvel_dimension: int = 4


FAST_ARM_DEFINITION = FastArmDefinition()


__all__ = [
    "FAST_ARM_DEFINITION",
    "FAST_ARM_ID",
    "FAST_ARM_JOINT_AXES",
    "FAST_ARM_JOINT_NAMES",
    "FAST_ARM_MODEL_CONTRACT_VERSION",
    "FastArmDefinition",
]
