"""Pure solver-local fast_arm endpoint forward and inverse kinematics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Sequence

import numpy as np

Vector3 = tuple[float, float, float]

FAST_ARM_ENDPOINT_JOINT_COUNT = 4
FAST_ARM_ENDPOINT_LINK_LENGTHS_M: tuple[float, float, float] = (0.26, 0.24, 0.23)
FAST_ARM_ENDPOINT_BASE_POSITION_M: Vector3 = (0.0, 0.0, 0.0)
_IK_MAX_ITERATIONS = 24
_IK_STEP_LIMIT_RAD = 0.35
_IK_DAMPING = 1e-3
_IK_FINITE_DIFFERENCE_EPSILON_RAD = 1e-4
_IK_POSITION_TOLERANCE_M = 1e-5
_IK_NON_CONVERGENCE_MESSAGE = "target_position_m did not converge"


def _validate_vector3(name: str, value: object) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    components = tuple(float(component) for component in value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    for index, component in enumerate(components):
        if not math.isfinite(component):
            raise ValueError(f"{name} must contain only finite values at index {index}")

    return components


def _validate_seed_joint_angles(values: Sequence[float] | None) -> tuple[float, ...] | None:
    if values is None:
        return None

    joint_angles_rad = tuple(float(value) for value in values)
    if len(joint_angles_rad) != FAST_ARM_ENDPOINT_JOINT_COUNT:
        raise ValueError(
            "seed_joint_angles_rad must contain exactly four values for this solver"
        )

    return joint_angles_rad


def _wrap_angle(value_rad: float) -> float:
    return math.atan2(math.sin(value_rad), math.cos(value_rad))


def _forward_endpoint(
    joint_angles_rad: Sequence[float],
    *,
    link_lengths_m: Sequence[float],
    base_position_m: Vector3,
) -> Vector3:
    q0_rad, q1_rad, q2_rad, q3_rad = (float(value) for value in joint_angles_rad)
    l1_m, l2_m, l3_m = (float(value) for value in link_lengths_m)
    base_x_m, base_y_m, base_z_m = _validate_vector3("base_position_m", base_position_m)

    cumulative_angle_rad = q1_rad
    planar_x_m = l1_m * math.cos(cumulative_angle_rad)
    planar_z_m = l1_m * math.sin(cumulative_angle_rad)

    cumulative_angle_rad += q2_rad
    planar_x_m += l2_m * math.cos(cumulative_angle_rad)
    planar_z_m += l2_m * math.sin(cumulative_angle_rad)

    cumulative_angle_rad += q3_rad
    planar_x_m += l3_m * math.cos(cumulative_angle_rad)
    planar_z_m += l3_m * math.sin(cumulative_angle_rad)

    x_m = base_x_m + math.cos(q0_rad) * planar_x_m
    y_m = base_y_m + math.sin(q0_rad) * planar_x_m
    z_m = base_z_m + planar_z_m
    return (x_m, y_m, z_m)


def _planar_two_link_seed(
    *,
    planar_radius_m: float,
    height_m: float,
    link_lengths_m: Sequence[float],
) -> tuple[float, float, float]:
    l1_m, l2_m, l3_m = (float(value) for value in link_lengths_m)
    effective_second_link_m = l2_m + l3_m
    distance_m = math.hypot(planar_radius_m, height_m)
    max_reach_m = l1_m + effective_second_link_m
    if distance_m > max_reach_m + 1e-9:
        raise ValueError("target_position_m is outside the reachable workspace")

    if distance_m < abs(l1_m - effective_second_link_m) - 1e-9:
        raise ValueError("target_position_m is outside the reachable workspace")

    if l1_m == 0.0 and effective_second_link_m == 0.0:
        return (0.0, 0.0, 0.0)

    if l1_m == 0.0:
        return (0.0, 0.0, math.atan2(height_m, planar_radius_m))

    if effective_second_link_m == 0.0:
        shoulder_angle_rad = math.atan2(height_m, planar_radius_m)
        return (shoulder_angle_rad, 0.0, 0.0)

    cos_elbow_rad = (
        distance_m**2 - l1_m**2 - effective_second_link_m**2
    ) / (2.0 * l1_m * effective_second_link_m)
    if cos_elbow_rad < -1.0 - 1e-9 or cos_elbow_rad > 1.0 + 1e-9:
        raise ValueError("target_position_m is outside the reachable workspace")
    cos_elbow_rad = max(-1.0, min(1.0, cos_elbow_rad))
    elbow_angle_rad = -math.acos(cos_elbow_rad)

    k1_m = l1_m + effective_second_link_m * math.cos(elbow_angle_rad)
    k2_m = effective_second_link_m * math.sin(elbow_angle_rad)
    shoulder_angle_rad = math.atan2(height_m, planar_radius_m) - math.atan2(k2_m, k1_m)

    second_joint_angle_rad = 0.6 * elbow_angle_rad
    third_joint_angle_rad = 0.4 * elbow_angle_rad
    return (shoulder_angle_rad, second_joint_angle_rad, third_joint_angle_rad)


def _initial_joint_guess(
    target_position_m: Vector3,
    *,
    link_lengths_m: Sequence[float],
    base_position_m: Vector3,
    seed_joint_angles_rad: Sequence[float] | None,
) -> np.ndarray:
    if seed_joint_angles_rad is not None:
        return np.asarray(seed_joint_angles_rad, dtype=np.float64)

    target_x_m, target_y_m, target_z_m = _validate_vector3("target_position_m", target_position_m)
    base_x_m, base_y_m, base_z_m = _validate_vector3("base_position_m", base_position_m)
    local_x_m = target_x_m - base_x_m
    local_y_m = target_y_m - base_y_m
    local_z_m = target_z_m - base_z_m
    q0_rad = math.atan2(local_y_m, local_x_m)
    planar_radius_m = math.hypot(local_x_m, local_y_m)
    q1_rad, q2_rad, q3_rad = _planar_two_link_seed(
        planar_radius_m=planar_radius_m,
        height_m=local_z_m,
        link_lengths_m=link_lengths_m,
    )
    return np.asarray((q0_rad, q1_rad, q2_rad, q3_rad), dtype=np.float64)


def _finite_difference_jacobian(
    joint_angles_rad: np.ndarray,
    *,
    link_lengths_m: Sequence[float],
    base_position_m: Vector3,
) -> np.ndarray:
    jacobian = np.zeros((3, FAST_ARM_ENDPOINT_JOINT_COUNT), dtype=np.float64)
    base_position = _forward_endpoint(
        joint_angles_rad,
        link_lengths_m=link_lengths_m,
        base_position_m=base_position_m,
    )

    for joint_index in range(FAST_ARM_ENDPOINT_JOINT_COUNT):
        perturbed_joint_angles = joint_angles_rad.copy()
        perturbed_joint_angles[joint_index] += _IK_FINITE_DIFFERENCE_EPSILON_RAD
        perturbed_position = _forward_endpoint(
            perturbed_joint_angles,
            link_lengths_m=link_lengths_m,
            base_position_m=base_position_m,
        )
        jacobian[:, joint_index] = (
            np.asarray(perturbed_position, dtype=np.float64) - np.asarray(base_position, dtype=np.float64)
        ) / _IK_FINITE_DIFFERENCE_EPSILON_RAD

    return jacobian


def _solve_fast_arm_endpoint(
    target_position_m: Vector3,
    *,
    seed_joint_angles_rad: Sequence[float] | None,
    link_lengths_m: Sequence[float],
    base_position_m: Vector3,
) -> tuple[float, float, float, float]:
    target_x_m, target_y_m, target_z_m = _validate_vector3("target_position_m", target_position_m)
    base_x_m, base_y_m, base_z_m = _validate_vector3("base_position_m", base_position_m)
    local_target_m = (
        target_x_m - base_x_m,
        target_y_m - base_y_m,
        target_z_m - base_z_m,
    )

    max_reach_m = sum(float(length) for length in link_lengths_m)
    if math.sqrt(sum(component * component for component in local_target_m)) > max_reach_m + 1e-9:
        raise ValueError("target_position_m is outside the reachable workspace")

    joint_angles_rad = _initial_joint_guess(
        target_position_m,
        link_lengths_m=link_lengths_m,
        base_position_m=base_position_m,
        seed_joint_angles_rad=seed_joint_angles_rad,
    )

    target_vector = np.asarray(local_target_m, dtype=np.float64)
    for _ in range(_IK_MAX_ITERATIONS):
        current_position = np.asarray(
            _forward_endpoint(
                joint_angles_rad,
                link_lengths_m=link_lengths_m,
                base_position_m=base_position_m,
            ),
            dtype=np.float64,
        )
        error_vector = target_vector - current_position
        if float(np.linalg.norm(error_vector)) <= _IK_POSITION_TOLERANCE_M:
            break

        jacobian = _finite_difference_jacobian(
            joint_angles_rad,
            link_lengths_m=link_lengths_m,
            base_position_m=base_position_m,
        )
        jj_t = jacobian @ jacobian.T
        damped_jj_t = jj_t + (_IK_DAMPING**2) * np.eye(3, dtype=np.float64)
        step_vector = jacobian.T @ np.linalg.solve(damped_jj_t, error_vector)
        step_norm = float(np.linalg.norm(step_vector))
        if step_norm > _IK_STEP_LIMIT_RAD:
            step_vector = step_vector * (_IK_STEP_LIMIT_RAD / step_norm)

        joint_angles_rad = joint_angles_rad + step_vector
        joint_angles_rad = np.asarray([_wrap_angle(angle) for angle in joint_angles_rad], dtype=np.float64)

    final_position = np.asarray(
        _forward_endpoint(
            joint_angles_rad,
            link_lengths_m=link_lengths_m,
            base_position_m=base_position_m,
        ),
        dtype=np.float64,
    )
    final_error_norm_m = float(np.linalg.norm(target_vector - final_position))
    if final_error_norm_m > _IK_POSITION_TOLERANCE_M:
        raise ValueError(_IK_NON_CONVERGENCE_MESSAGE)

    return tuple(float(angle) for angle in joint_angles_rad)


@dataclass(frozen=True, slots=True)
class FastArmEndpointForwardKinematics:
    """Minimal fast_arm endpoint FK baseline for the 4DOF endpoint model."""

    link_lengths_m: tuple[float, float, float] = FAST_ARM_ENDPOINT_LINK_LENGTHS_M
    base_position_m: Vector3 = FAST_ARM_ENDPOINT_BASE_POSITION_M

    def __post_init__(self) -> None:
        if len(self.link_lengths_m) != FAST_ARM_ENDPOINT_JOINT_COUNT - 1:
            raise ValueError("link_lengths_m must contain exactly three links")
        if any(link_length < 0.0 for link_length in self.link_lengths_m):
            raise ValueError("link_lengths_m must be non-negative")
        _validate_vector3("base_position_m", self.base_position_m)

    def forward(self, joint_angles_rad: tuple[float, ...]) -> Vector3:
        if len(joint_angles_rad) != FAST_ARM_ENDPOINT_JOINT_COUNT:
            raise ValueError(
                "joint angle count does not match fast_arm endpoint contract: "
                f"expected {FAST_ARM_ENDPOINT_JOINT_COUNT}, got {len(joint_angles_rad)}"
            )

        return _forward_endpoint(
            joint_angles_rad,
            link_lengths_m=self.link_lengths_m,
            base_position_m=self.base_position_m,
        )


@dataclass(frozen=True, slots=True)
class FastArmEndpointInverseKinematics:
    """Minimal fast_arm endpoint IK v0 for the concrete 4DOF runtime path."""

    link_lengths_m: tuple[float, float, float] = FAST_ARM_ENDPOINT_LINK_LENGTHS_M
    base_position_m: Vector3 = FAST_ARM_ENDPOINT_BASE_POSITION_M

    def __post_init__(self) -> None:
        if len(self.link_lengths_m) != FAST_ARM_ENDPOINT_JOINT_COUNT - 1:
            raise ValueError("link_lengths_m must contain exactly three links")
        if any(link_length < 0.0 for link_length in self.link_lengths_m):
            raise ValueError("link_lengths_m must be non-negative")
        _validate_vector3("base_position_m", self.base_position_m)

    def solve(
        self,
        target_position_m: Vector3,
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> tuple[float, float, float, float]:
        validated_seed_joint_angles_rad = _validate_seed_joint_angles(seed_joint_angles_rad)
        solved_joint_angles_rad = _solve_fast_arm_endpoint(
            target_position_m,
            seed_joint_angles_rad=validated_seed_joint_angles_rad,
            link_lengths_m=self.link_lengths_m,
            base_position_m=self.base_position_m,
        )
        return solved_joint_angles_rad


__all__ = [
    "FAST_ARM_ENDPOINT_BASE_POSITION_M",
    "FAST_ARM_ENDPOINT_JOINT_COUNT",
    "FAST_ARM_ENDPOINT_LINK_LENGTHS_M",
    "FastArmEndpointForwardKinematics",
    "FastArmEndpointInverseKinematics",
]
