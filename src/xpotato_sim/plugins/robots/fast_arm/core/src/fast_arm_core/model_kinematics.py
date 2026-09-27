"""Pure forward kinematics aligned with the packaged fast_arm MuJoCo model."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from fast_arm_core.definition import FAST_ARM_DEFINITION
from fast_arm_core.model_spec import FAST_ARM_TIP_SITE_NAME


FAST_ARM_MUJOCO_MODEL_TIP_SITE_NAME = FAST_ARM_TIP_SITE_NAME
FAST_ARM_MUJOCO_MODEL_JOINT_REFS_RAD = (0.0, -math.pi / 2.0, 0.0, 0.0)


def _rotation_matrix(axis: Sequence[float], angle_rad: float) -> np.ndarray:
    axis_vector = np.asarray(tuple(float(component) for component in axis), dtype=np.float64)
    norm = float(np.linalg.norm(axis_vector))
    if norm == 0.0:
        raise ValueError("rotation axis must be non-zero")
    x_axis, y_axis, z_axis = axis_vector / norm
    cos_angle = math.cos(angle_rad)
    sin_angle = math.sin(angle_rad)
    one_minus_cos = 1.0 - cos_angle
    return np.asarray(
        (
            (cos_angle + x_axis*x_axis*one_minus_cos, x_axis*y_axis*one_minus_cos-z_axis*sin_angle, x_axis*z_axis*one_minus_cos+y_axis*sin_angle),
            (y_axis*x_axis*one_minus_cos+z_axis*sin_angle, cos_angle+y_axis*y_axis*one_minus_cos, y_axis*z_axis*one_minus_cos-x_axis*sin_angle),
            (z_axis*x_axis*one_minus_cos-y_axis*sin_angle, z_axis*y_axis*one_minus_cos+x_axis*sin_angle, cos_angle+z_axis*z_axis*one_minus_cos),
        ),
        dtype=np.float64,
    )


def forward_fast_arm_mujoco_model_tip_site(qpos_rad: Sequence[float]) -> tuple[float, float, float]:
    qpos = tuple(float(value) for value in qpos_rad)
    if len(qpos) != FAST_ARM_DEFINITION.qpos_dimension:
        raise ValueError("qpos_rad must contain exactly four values for the fast_arm MuJoCo model")
    rotation = _rotation_matrix((0.0, 0.0, 1.0), math.pi / 2.0)
    position = np.zeros(3, dtype=np.float64)
    body_chain = (
        (None, (0.0, 0.069, 0.7), None, None, 0.0),
        (0, (0.0, -0.014, 0.0), (0.0, -0.055, 0.0), (0.0, -1.0, 0.0), 0.0),
        (1, (0.0, -0.131, 0.0), (0.0, 0.076, 0.0), (1.0, 0.0, 0.0), -math.pi / 2.0),
        (2, (0.0, -0.0115, 0.0), (0.0, 0.0, 0.0), (0.0, -1.0, 0.0), 0.0),
        (3, (0.0, -0.2505, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 0.0),
    )
    for joint_index, body_pos_m, joint_pos_m, joint_axis, joint_ref_rad in body_chain:
        body_pos = np.asarray(body_pos_m, dtype=np.float64)
        parent_rotation = rotation
        if joint_index is None:
            position = position + parent_rotation @ body_pos
            continue
        joint_pos = np.asarray(joint_pos_m, dtype=np.float64)
        joint_anchor_m = position + parent_rotation @ (body_pos + joint_pos)
        rotation = parent_rotation @ _rotation_matrix(joint_axis, qpos[joint_index] - joint_ref_rad)
        position = joint_anchor_m - rotation @ joint_pos
    result = position + rotation @ np.asarray((0.0, -0.284, 0.0), dtype=np.float64)
    return (float(result[0]), float(result[1]), float(result[2]))


@dataclass(frozen=True, slots=True)
class FastArmModelForwardKinematics:
    tip_site_name: str = FAST_ARM_MUJOCO_MODEL_TIP_SITE_NAME
    joint_refs_rad: tuple[float, float, float, float] = FAST_ARM_MUJOCO_MODEL_JOINT_REFS_RAD
    coordinate_frame: str = "MuJoCo world / scene frame"

    def __post_init__(self) -> None:
        if self.tip_site_name != FAST_ARM_MUJOCO_MODEL_TIP_SITE_NAME:
            raise ValueError("tip_site_name must match the fast_arm MuJoCo model contract")
        if self.joint_refs_rad != FAST_ARM_MUJOCO_MODEL_JOINT_REFS_RAD:
            raise ValueError("joint_refs_rad must match the fast_arm MuJoCo model contract")

    def forward(self, qpos_rad: tuple[float, ...]) -> tuple[float, float, float]:
        return forward_fast_arm_mujoco_model_tip_site(qpos_rad)


__all__ = [
    "FAST_ARM_MUJOCO_MODEL_JOINT_REFS_RAD",
    "FAST_ARM_MUJOCO_MODEL_TIP_SITE_NAME",
    "FastArmModelForwardKinematics",
    "forward_fast_arm_mujoco_model_tip_site",
]
