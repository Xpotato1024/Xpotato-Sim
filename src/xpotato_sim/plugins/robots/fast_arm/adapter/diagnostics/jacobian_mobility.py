"""Offline, deterministic local Jacobian mobility diagnostics for fast_arm.

This module deliberately sits beside, rather than inside, the viewer motion
policy.  It measures the existing endpoint path and never changes production
defaults or motion-status decisions.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Sequence

import numpy as np

if TYPE_CHECKING:
    from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator

from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import FastArmMuJoCoModelForwardKinematicsSolver
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator
from xpotato_sim.runtime.control.viewer_motion_policy import (
    DEFAULT_VIEWER_LOCAL_ENDPOINT_DAMPING,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_FD_EPSILON_RAD,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_QPOS_DELTA_NORM_RAD,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_SPEED_M_S,
)
from xpotato_sim.motion.local_endpoint_motion import _finite_difference_jacobian

CONTROLLED_JOINT_NAMES = (
    "sholder_joint_1",
    "sholder_joint_2",
    "sholder_joint_3",
    "elbow_joint",
)
DEFAULT_DT_S = 1.0 / 60.0
DEFAULT_ABSOLUTE_RANK_TOLERANCE = 1e-9
DEFAULT_RELATIVE_RANK_TOLERANCE = 1e-4
DEFAULT_DIRECTION_COSINE_TOLERANCE = 1e-12
DEFAULT_NEARBY_PERTURBATION_RAD = 0.1
DEFAULT_REQUESTED_DELTA_M = DEFAULT_VIEWER_LOCAL_ENDPOINT_SPEED_M_S * DEFAULT_DT_S


def _finite_vector(name: str, value: Sequence[float]) -> np.ndarray:
    array = np.asarray(tuple(float(item) for item in value), dtype=np.float64)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    return array


def _tuple(array: np.ndarray) -> tuple[float, ...]:
    return tuple(float(item) for item in array.reshape(-1))


def _matrix(name: str, value: Sequence[Sequence[float]]) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2 or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite two-dimensional matrix")
    return array


@dataclass(frozen=True, slots=True)
class JacobianMetrics:
    jacobian: tuple[tuple[float, ...], ...]
    numeric_rank: int
    effective_rank: int
    absolute_rank_tolerance: float
    relative_rank_tolerance: float
    effective_rank_tolerance: float
    singular_values: tuple[float, ...]
    minimum_singular_value: float
    condition_number: float
    row_norms: tuple[float, ...]
    column_norms: tuple[float, ...]
    manipulability: float


@dataclass(frozen=True, slots=True)
class DeltaMetrics:
    requested_delta_m: tuple[float, float, float]
    solved_unscaled_qpos_delta_rad: tuple[float, ...]
    qpos_delta_rad: tuple[float, ...]
    predicted_delta_m: tuple[float, float, float]
    measured_delta_m: tuple[float, float, float]
    signed_progress_m: float
    progress_ratio: float | None
    direction_cosine: float | None


@dataclass(frozen=True, slots=True)
class DirectionDiagnostic:
    label: str
    delta: DeltaMetrics


@dataclass(frozen=True, slots=True)
class PoseDiagnostic:
    label: str
    qpos_rad: tuple[float, ...]
    perturbed_joint_name: str | None
    requested_perturbation_rad: float | None
    actual_perturbation_rad: float | None
    actual_perturbation_vector_rad: tuple[float, ...]
    clipped: bool
    tip_position_m: tuple[float, float, float]
    finite_difference: JacobianMetrics
    native: JacobianMetrics
    jacobian_difference_norm: float
    directions: tuple[DirectionDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class SensitivityPoint:
    parameter: str
    value: float
    solved_unscaled_qpos_delta_rad: tuple[float, ...]
    qpos_delta_rad: tuple[float, ...]
    predicted_delta_m: tuple[float, float, float]
    measured_delta_m: tuple[float, float, float]
    singular_values: tuple[float, ...]
    numeric_rank: int
    effective_rank: int


@dataclass(frozen=True, slots=True)
class JacobianMobilityDiagnostics:
    schema_version: str
    model_identity: str
    endpoint_site: str
    coordinate_frame: str
    controlled_dof_mapping: tuple[dict[str, object], ...]
    absolute_rank_tolerance: float
    relative_rank_tolerance: float
    direction_cosine_tolerance: float
    dt_s: float
    requested_delta_m: float
    epsilon_values_rad: tuple[float, ...]
    damping_values: tuple[float, ...]
    qpos_cap_values_rad: tuple[float, ...]
    poses: tuple[PoseDiagnostic, ...]
    epsilon_sensitivity: tuple[SensitivityPoint, ...]
    damping_sensitivity: tuple[SensitivityPoint, ...]
    qpos_cap_sensitivity: tuple[SensitivityPoint, ...]

    def to_dict(self) -> dict[str, object]:
        def sanitize(value: object) -> object:
            if isinstance(value, float) and math.isinf(value):
                return "Infinity" if value > 0 else "-Infinity"
            if isinstance(value, dict):
                return {key: sanitize(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [sanitize(item) for item in value]
            return value

        return sanitize(asdict(self))  # type: ignore[return-value]

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True)


def summarize_jacobian(
    jacobian: Sequence[Sequence[float]],
    *,
    absolute_rank_tolerance: float = DEFAULT_ABSOLUTE_RANK_TOLERANCE,
    relative_rank_tolerance: float = DEFAULT_RELATIVE_RANK_TOLERANCE,
    effective_rank_tolerance: float | None = None,
) -> JacobianMetrics:
    matrix = _matrix("jacobian", jacobian)
    if matrix.shape[0] != 3:
        raise ValueError("jacobian must have shape 3xN")
    if absolute_rank_tolerance <= 0.0 or not math.isfinite(absolute_rank_tolerance):
        raise ValueError("absolute_rank_tolerance must be finite and positive")
    if relative_rank_tolerance < 0.0 or not math.isfinite(relative_rank_tolerance):
        raise ValueError("relative_rank_tolerance must be finite and non-negative")
    singular_values = np.linalg.svd(matrix, compute_uv=False)
    largest = float(singular_values[0]) if singular_values.size else 0.0
    effective_tolerance = max(absolute_rank_tolerance, relative_rank_tolerance * largest)
    if effective_rank_tolerance is not None:
        if effective_rank_tolerance <= 0.0 or not math.isfinite(effective_rank_tolerance):
            raise ValueError("effective_rank_tolerance must be finite and positive")
        effective_tolerance = max(effective_tolerance, effective_rank_tolerance)
    numeric_rank = int(np.count_nonzero(singular_values > absolute_rank_tolerance))
    effective_rank = int(np.count_nonzero(singular_values > effective_tolerance))
    minimum = float(singular_values[-1]) if singular_values.size else 0.0
    condition = math.inf if minimum <= effective_tolerance else float(singular_values[0] / minimum)
    gram_determinant = float(np.linalg.det(matrix @ matrix.T))
    manipulability = 0.0 if effective_rank < 3 else math.sqrt(max(0.0, gram_determinant))
    return JacobianMetrics(
        jacobian=tuple(_tuple(row) for row in matrix),
        numeric_rank=numeric_rank,
        effective_rank=effective_rank,
        absolute_rank_tolerance=absolute_rank_tolerance,
        relative_rank_tolerance=relative_rank_tolerance,
        effective_rank_tolerance=effective_tolerance,
        singular_values=_tuple(singular_values),
        minimum_singular_value=minimum,
        condition_number=condition,
        row_norms=_tuple(np.linalg.norm(matrix, axis=1)),
        column_norms=_tuple(np.linalg.norm(matrix, axis=0)),
        manipulability=manipulability,
    )


def build_delta_metrics(
    requested_delta_m: Sequence[float],
    solved_unscaled_qpos_delta_rad: Sequence[float],
    qpos_delta_rad: Sequence[float],
    predicted_delta_m: Sequence[float],
    measured_delta_m: Sequence[float],
    *,
    direction_cosine_tolerance: float = DEFAULT_DIRECTION_COSINE_TOLERANCE,
) -> DeltaMetrics:
    if direction_cosine_tolerance <= 0.0 or not math.isfinite(direction_cosine_tolerance):
        raise ValueError("direction_cosine_tolerance must be finite and positive")
    requested = _finite_vector("requested_delta_m", requested_delta_m)
    measured = _finite_vector("measured_delta_m", measured_delta_m)
    if requested.size != 3 or measured.size != 3:
        raise ValueError("endpoint deltas must contain exactly three values")
    requested_norm = float(np.linalg.norm(requested))
    measured_norm = float(np.linalg.norm(measured))
    signed = float(np.dot(measured, requested / requested_norm)) if requested_norm > direction_cosine_tolerance else 0.0
    ratio = signed / requested_norm if requested_norm > direction_cosine_tolerance else None
    cosine = None
    if requested_norm > direction_cosine_tolerance and measured_norm > direction_cosine_tolerance:
        cosine = float(np.dot(measured, requested) / (measured_norm * requested_norm))
    return DeltaMetrics(
        requested_delta_m=_tuple(requested),
        solved_unscaled_qpos_delta_rad=_tuple(_finite_vector("solved_unscaled_qpos_delta_rad", solved_unscaled_qpos_delta_rad)),
        qpos_delta_rad=_tuple(_finite_vector("qpos_delta_rad", qpos_delta_rad)),
        predicted_delta_m=_tuple(_finite_vector("predicted_delta_m", predicted_delta_m)),
        measured_delta_m=_tuple(measured),
        signed_progress_m=signed,
        progress_ratio=ratio,
        direction_cosine=cosine,
    )


def _solve_delta(jacobian: np.ndarray, requested: np.ndarray, damping: float, cap: float) -> tuple[np.ndarray, np.ndarray]:
    if damping < 0.0 or not math.isfinite(damping) or cap <= 0.0 or not math.isfinite(cap):
        raise ValueError("damping and cap must be finite and valid")
    delta = jacobian.T @ np.linalg.solve(jacobian @ jacobian.T + np.eye(3) * damping, requested)
    if not np.all(np.isfinite(delta)):
        raise ValueError("diagnostic qpos solve is non-finite")
    unscaled = delta.copy()
    norm = float(np.linalg.norm(delta))
    if norm > cap:
        delta *= cap / norm
    return unscaled, delta


def _native_jacobian(simulator: HeadlessMuJoCoSimulator, qpos: Sequence[float]) -> tuple[np.ndarray, tuple[dict[str, object], ...]]:
    mujoco = simulator._import_mujoco()
    model, data = simulator.model, simulator.data
    data.qpos[:] = 0.0
    for index, value in enumerate(qpos):
        data.qpos[index] = float(value)
    mujoco.mj_forward(model, data)
    site_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "tip"))
    if site_id < 0:
        raise ValueError("fast_arm MuJoCo tip site is missing")
    jacp = np.zeros((3, int(model.nv)), dtype=np.float64)
    jacr = np.zeros((3, int(model.nv)), dtype=np.float64)
    mujoco.mj_jacSite(model, data, jacp, jacr, site_id)
    columns: list[dict[str, object]] = []
    selected: list[int] = []
    for joint_name in CONTROLLED_JOINT_NAMES:
        joint_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name))
        if joint_id < 0 or int(model.jnt_type[joint_id]) != int(mujoco.mjtJoint.mjJNT_HINGE):
            raise ValueError(f"controlled joint is not a resolvable hinge: {joint_name}")
        qpos_address = int(model.jnt_qposadr[joint_id])
        dof_address = int(model.jnt_dofadr[joint_id])
        if qpos_address >= len(qpos) or dof_address >= int(model.nv):
            raise ValueError(f"invalid qpos/DoF mapping for {joint_name}")
        selected.append(dof_address)
        columns.append({"joint_name": joint_name, "joint_id": joint_id, "qpos_address": qpos_address, "dof_address": dof_address, "joint_type": "hinge"})
    return jacp[:, selected], tuple(columns)


def _tip(simulator: HeadlessMuJoCoSimulator, qpos: Sequence[float]) -> np.ndarray:
    mujoco = simulator._import_mujoco()
    simulator.data.qpos[:] = 0.0
    for index, value in enumerate(qpos):
        simulator.data.qpos[index] = float(value)
    mujoco.mj_forward(simulator.model, simulator.data)
    site_id = int(mujoco.mj_name2id(simulator.model, mujoco.mjtObj.mjOBJ_SITE, "tip"))
    return _finite_vector("tip_position_m", simulator.data.site_xpos[site_id])


def _pose_qpos(simulator: HeadlessMuJoCoSimulator) -> tuple[dict[str, object], ...]:
    mujoco = simulator._import_mujoco()
    initial = tuple(float(value) for value in simulator.data.qpos[: int(simulator.model.nq)])
    poses: list[dict[str, object]] = [{
        "label": "default_pose",
        "qpos": initial,
        "perturbed_joint_name": None,
        "requested_perturbation_rad": None,
        "actual_perturbation_rad": None,
        "actual_perturbation_vector_rad": tuple(0.0 for _ in initial),
        "clipped": False,
    }]
    seen = {initial}
    for joint_name in CONTROLLED_JOINT_NAMES:
        joint_id = int(mujoco.mj_name2id(simulator.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name))
        if joint_id < 0:
            raise ValueError(f"controlled joint is missing: {joint_name}")
        qpos_address = int(simulator.model.jnt_qposadr[joint_id])
        if qpos_address < 0 or qpos_address >= len(initial):
            raise ValueError(f"controlled qpos address is outside diagnostic vector: {joint_name} -> {qpos_address}")
        limited = bool(simulator.model.jnt_limited[joint_id])
        lower, upper = (float(value) for value in simulator.model.jnt_range[joint_id])
        for sign in (1.0, -1.0):
            requested = sign * DEFAULT_NEARBY_PERTURBATION_RAD
            qpos = list(initial)
            proposed = qpos[qpos_address] + requested
            clipped_value = min(upper, max(lower, proposed)) if limited else proposed
            qpos[qpos_address] = clipped_value
            actual = clipped_value - initial[qpos_address]
            actual_vector = tuple(qpos[index] - initial[index] for index in range(len(initial)))
            qpos_tuple = tuple(qpos)
            if abs(actual) <= 1e-15:
                raise ValueError(f"nearby pose is a no-op for {joint_name}")
            if actual * sign <= 0.0:
                raise ValueError(f"nearby pose has wrong perturbation sign for {joint_name}")
            if qpos_tuple in seen:
                raise ValueError(f"nearby pose duplicates an existing pose: {joint_name} {sign:+g}")
            seen.add(qpos_tuple)
            poses.append({
                "label": f"{joint_name}_{'positive' if sign > 0 else 'negative'}_nearby",
                "qpos": qpos_tuple,
                "perturbed_joint_name": joint_name,
                "requested_perturbation_rad": requested,
                "actual_perturbation_rad": actual,
                "actual_perturbation_vector_rad": actual_vector,
                "clipped": limited and clipped_value != proposed,
            })
    # Reuse the existing small combined FK/site fixture as a representative pose.
    combined_offset = (0.02, 0.01, 0.015, 0.005)
    if len(initial) != len(combined_offset):
        raise ValueError("canonical fast_arm qpos shape changed; representative fixture is invalid")
    combined = tuple(initial[index] + combined_offset[index] for index in range(len(initial)))
    if combined in seen:
        raise ValueError("representative pose duplicates an existing pose")
    poses.append({
        "label": "representative_combined_fixture",
        "qpos": combined,
        "perturbed_joint_name": "combined_fixture",
        "requested_perturbation_rad": None,
        "actual_perturbation_rad": None,
        "actual_perturbation_vector_rad": combined_offset,
        "clipped": False,
    })
    if len({pose["qpos"] for pose in poses}) != len(poses):
        raise ValueError("pose set contains duplicate qpos values")
    return tuple(poses)


def _direction_results(simulator: HeadlessMuJoCoSimulator, qpos: tuple[float, ...], jacobian: np.ndarray, *, damping: float, cap: float, requested_delta_m: float) -> tuple[DirectionDiagnostic, ...]:
    result: list[DirectionDiagnostic] = []
    for label, axis in (("+X", (1.0, 0.0, 0.0)), ("-X", (-1.0, 0.0, 0.0)), ("+Y", (0.0, 1.0, 0.0)), ("-Y", (0.0, -1.0, 0.0)), ("+Z", (0.0, 0.0, 1.0)), ("-Z", (0.0, 0.0, -1.0))):
        requested = np.asarray(axis, dtype=np.float64) * requested_delta_m
        unscaled, delta = _solve_delta(jacobian, requested, damping, cap)
        candidate = np.asarray(qpos, dtype=np.float64) + delta
        predicted = jacobian @ delta
        measured = _tip(simulator, candidate) - _tip(simulator, qpos)
        result.append(DirectionDiagnostic(label=label, delta=build_delta_metrics(requested, unscaled, delta, predicted, measured)))
    return tuple(result)


def evaluate_fast_arm_pose_mobility(
    simulator: HeadlessMuJoCoSimulator,
    qpos_rad: Sequence[float],
    *,
    epsilon_rad: float = DEFAULT_VIEWER_LOCAL_ENDPOINT_FD_EPSILON_RAD,
    damping: float = DEFAULT_VIEWER_LOCAL_ENDPOINT_DAMPING,
    qpos_cap_rad: float = DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_QPOS_DELTA_NORM_RAD,
    requested_delta_m: float = DEFAULT_REQUESTED_DELTA_M,
) -> PoseDiagnostic:
    """Evaluate one explicit fast_arm qpos with the canonical P9 metrics."""

    qpos = tuple(float(value) for value in qpos_rad)
    if len(qpos) != len(CONTROLLED_JOINT_NAMES):
        raise ValueError(
            "qpos_rad must contain exactly "
            f"{len(CONTROLLED_JOINT_NAMES)} values"
        )
    if not all(math.isfinite(value) for value in qpos):
        raise ValueError("qpos_rad must contain only finite values")
    if epsilon_rad <= 0.0 or not math.isfinite(epsilon_rad):
        raise ValueError("epsilon_rad must be finite and positive")
    if damping < 0.0 or not math.isfinite(damping):
        raise ValueError("damping must be finite and non-negative")
    if qpos_cap_rad <= 0.0 or not math.isfinite(qpos_cap_rad):
        raise ValueError("qpos_cap_rad must be finite and positive")
    if requested_delta_m <= 0.0 or not math.isfinite(requested_delta_m):
        raise ValueError("requested_delta_m must be finite and positive")

    endpoint = FastArmMuJoCoModelForwardKinematicsSolver()
    native, _ = _native_jacobian(simulator, qpos)
    finite_difference = _finite_difference_jacobian(
        qpos,
        endpoint_kinematics=endpoint,
        epsilon_rad=epsilon_rad,
    )
    discrepancy = float(np.linalg.norm(finite_difference - native))
    native_singular_values = np.linalg.svd(native, compute_uv=False)
    largest_native = float(native_singular_values[0]) if native_singular_values.size else 0.0
    effective_tolerance = max(
        DEFAULT_ABSOLUTE_RANK_TOLERANCE,
        DEFAULT_RELATIVE_RANK_TOLERANCE * largest_native,
        discrepancy,
    )
    return PoseDiagnostic(
        label="explicit_pose",
        qpos_rad=qpos,
        perturbed_joint_name=None,
        requested_perturbation_rad=None,
        actual_perturbation_rad=None,
        actual_perturbation_vector_rad=tuple(0.0 for _ in qpos),
        clipped=False,
        tip_position_m=_tuple(_tip(simulator, qpos)),
        finite_difference=summarize_jacobian(
            finite_difference,
            effective_rank_tolerance=effective_tolerance,
        ),
        native=summarize_jacobian(
            native,
            effective_rank_tolerance=effective_tolerance,
        ),
        jacobian_difference_norm=discrepancy,
        directions=_direction_results(
            simulator,
            qpos,
            finite_difference,
            damping=damping,
            cap=qpos_cap_rad,
            requested_delta_m=requested_delta_m,
        ),
    )


def _three_sensitivity_values(center: float, lower_factor: float, upper_factor: float) -> tuple[float, float, float]:
    if center <= 0.0 or not math.isfinite(center):
        raise ValueError("sensitivity center must be finite and positive")
    return (center * lower_factor, center, center * upper_factor)


def run_fast_arm_jacobian_mobility_diagnostics(*, dt_s: float = DEFAULT_DT_S, requested_delta_m: float | None = None, epsilon_rad: float = DEFAULT_VIEWER_LOCAL_ENDPOINT_FD_EPSILON_RAD, damping: float = DEFAULT_VIEWER_LOCAL_ENDPOINT_DAMPING, qpos_cap_rad: float = DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_QPOS_DELTA_NORM_RAD) -> JacobianMobilityDiagnostics:
    if dt_s <= 0.0 or not math.isfinite(dt_s) or epsilon_rad <= 0.0 or damping < 0.0 or qpos_cap_rad <= 0.0:
        raise ValueError("dt_s must be finite and positive; epsilon, damping, and cap must be valid")
    resolved_requested_delta_m = DEFAULT_VIEWER_LOCAL_ENDPOINT_SPEED_M_S * dt_s if requested_delta_m is None else requested_delta_m
    if resolved_requested_delta_m <= 0.0 or not math.isfinite(resolved_requested_delta_m):
        raise ValueError("requested_delta_m must be finite and positive")
    simulator = build_fast_arm_simulator()
    endpoint = FastArmMuJoCoModelForwardKinematicsSolver()
    poses: list[PoseDiagnostic] = []
    mapping: tuple[dict[str, object], ...] | None = None
    for pose_spec in _pose_qpos(simulator):
        label = str(pose_spec["label"])
        qpos = tuple(float(value) for value in pose_spec["qpos"])
        native, current_mapping = _native_jacobian(simulator, qpos)
        mapping = current_mapping if mapping is None else mapping
        fd = _finite_difference_jacobian(qpos, endpoint_kinematics=endpoint, epsilon_rad=epsilon_rad)
        discrepancy = float(np.linalg.norm(fd - native))
        effective_tolerance = max(DEFAULT_ABSOLUTE_RANK_TOLERANCE, DEFAULT_RELATIVE_RANK_TOLERANCE * float(np.linalg.svd(native, compute_uv=False)[0]), discrepancy)
        tip_position = _tip(simulator, qpos)
        poses.append(PoseDiagnostic(label, qpos, pose_spec["perturbed_joint_name"], pose_spec["requested_perturbation_rad"], pose_spec["actual_perturbation_rad"], pose_spec["actual_perturbation_vector_rad"], pose_spec["clipped"], _tuple(tip_position), summarize_jacobian(fd, effective_rank_tolerance=effective_tolerance), summarize_jacobian(native, effective_rank_tolerance=effective_tolerance), discrepancy, _direction_results(simulator, qpos, fd, damping=damping, cap=qpos_cap_rad, requested_delta_m=resolved_requested_delta_m)))
    if mapping is None:
        raise ValueError("no diagnostic poses were generated")
    default_qpos = poses[0].qpos_rad
    sensitivity_sets = (("epsilon", _three_sensitivity_values(epsilon_rad, 0.1, 10.0)), ("damping", _three_sensitivity_values(damping, 0.1, 10.0)), ("qpos_cap", _three_sensitivity_values(qpos_cap_rad, 0.5, 2.0)))
    sensitivities: dict[str, tuple[SensitivityPoint, ...]] = {}
    for parameter, values in sensitivity_sets:
        points: list[SensitivityPoint] = []
        for value in values:
            eps = value if parameter == "epsilon" else epsilon_rad
            damp = value if parameter == "damping" else damping
            cap = value if parameter == "qpos_cap" else qpos_cap_rad
            requested = np.asarray((resolved_requested_delta_m, 0.0, 0.0), dtype=np.float64)
            jacobian = _finite_difference_jacobian(default_qpos, endpoint_kinematics=endpoint, epsilon_rad=eps)
            unscaled, delta = _solve_delta(jacobian, requested, damp, cap)
            candidate = np.asarray(default_qpos) + delta
            metrics = summarize_jacobian(jacobian)
            points.append(SensitivityPoint(parameter, float(value), _tuple(unscaled), _tuple(delta), _tuple(jacobian @ delta), _tuple(_tip(simulator, candidate) - _tip(simulator, default_qpos)), metrics.singular_values, metrics.numeric_rank, metrics.effective_rank))
        sensitivities[parameter] = tuple(points)
    return JacobianMobilityDiagnostics("r7-e-p9-v2", "fast_arm_canonical", "tip", "MuJoCo world / scene frame", mapping, DEFAULT_ABSOLUTE_RANK_TOLERANCE, DEFAULT_RELATIVE_RANK_TOLERANCE, DEFAULT_DIRECTION_COSINE_TOLERANCE, dt_s, resolved_requested_delta_m, sensitivity_sets[0][1], sensitivity_sets[1][1], sensitivity_sets[2][1], tuple(poses), sensitivities["epsilon"], sensitivities["damping"], sensitivities["qpos_cap"])


__all__ = ["JacobianMetrics", "DeltaMetrics", "DirectionDiagnostic", "PoseDiagnostic", "SensitivityPoint", "JacobianMobilityDiagnostics", "summarize_jacobian", "build_delta_metrics", "evaluate_fast_arm_pose_mobility", "run_fast_arm_jacobian_mobility_diagnostics"]
