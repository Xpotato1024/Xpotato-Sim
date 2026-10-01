"""fast_armのMuJoCo tip-site behaviorを層別に観測するsoftware diagnostic。

command intent、solver prediction、MuJoCo qpos/site measurementを別fieldで保持し、
world frameのposition/deltaはm、joint perturbationはradで記録する。diagnosticは
hardware I/Oを行わず、unavailable/rejected evidenceを推測値で補完しない。
"""

from __future__ import annotations

from xpotato_sim.runtime.execution.command_routes import project_joint_position_command

import asyncio
import csv
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from xpotato_sim.plugins.input_sources.replay import ReplayInputSource
from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import (
    FAST_ARM_ENDPOINT_BASE_POSITION_M,
    FAST_ARM_ENDPOINT_LINK_LENGTHS_M,
    FastArmEndpointForwardKinematicsSolver,
    FastArmEndpointInverseKinematicsSolver,
    FastArmMuJoCoModelForwardKinematicsSolver,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state
from xpotato_sim.mujoco_backend import inspect_mujoco_model
from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator
from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.control.desired_endpoint_resolver import resolve_desired_endpoint_from_motion_command
from xpotato_sim.runtime.evaluation.kinematics import evaluate_fk_endpoint_from_qpos
from xpotato_sim.schemas import JointCommand, MotionCommand, MuJoCoState, RawInputFrame, Vector3

_DEFAULT_COMMAND_DELTA_M = 0.02
_BASE_ENDPOINT_SOURCE_INITIAL_TIP = "initial_tip"
_BASE_ENDPOINT_SOURCE_EXPLICIT = "explicit"
_BASE_ENDPOINT_SOURCE_UNAVAILABLE = "unavailable"
_UNAVAILABLE = "unavailable"
_MUJOCO_SOLVER_BASE_BODY_NAME = "base_link"
_MUJOCO_QPOS_REF_MINUS_90_RAD = -math.pi / 2.0
_JOINT_AXIS_PERTURBATION_RAD = 0.02
_LOCAL_JACOBIAN_PERTURBATION_RAD = 0.01
_PERTURBATION_NO_MOVEMENT_EPSILON_M = 1e-9
_FK_SITE_CONSISTENCY_TOLERANCE_M = 1e-9
_IK_FK_SANITY_TOLERANCE_M = 1e-5
_KNOWN_FK_SITE_CONSISTENCY_STATUS = "pass"
_KNOWN_FK_SITE_CONSISTENCY_NOTE = "fk_site_consistency_repaired_with_mujoco_model_aligned_fk"
_TRAJECTORY_DIRECTION_DOT_THRESHOLD = 0.85
_TRAJECTORY_MOVEMENT_EPSILON_M = 1e-6


class _DiagnosticStatePublisher:
    """Retain diagnostic output locally without performing external I/O."""

    def __init__(self) -> None:
        self.last_state: MuJoCoState | None = None

    async def publish(self, state: MuJoCoState) -> None:
        self.last_state = state


_TRAJECTORY_LOG_COLUMNS = (
    "step",
    "time_s",
    "dt_s",
    "command_axis",
    "command_label",
    "target_x_m",
    "target_y_m",
    "target_z_m",
    "tip_x_m",
    "tip_y_m",
    "tip_z_m",
    "error_x_m",
    "error_y_m",
    "error_z_m",
    "error_norm_m",
    "status",
    "reason",
)
_COMMAND_AXES: tuple[tuple[str, int, Vector3], ...] = (
    ("x", 1, (1.0, 0.0, 0.0)),
    ("x", -1, (-1.0, 0.0, 0.0)),
    ("y", 1, (0.0, 1.0, 0.0)),
    ("y", -1, (0.0, -1.0, 0.0)),
    ("z", 1, (0.0, 0.0, 1.0)),
    ("z", -1, (0.0, 0.0, -1.0)),
)


def _coerce_vector3(name: str, value: object) -> Vector3:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    components = tuple(float(component) for component in value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    return components


def _vector_norm_m(vector_m: Sequence[float]) -> float:
    return math.sqrt(sum(float(component) * float(component) for component in vector_m))


def _normalize_vector3(vector_m: Sequence[float]) -> Vector3 | None:
    norm_m = _vector_norm_m(vector_m)
    if norm_m == 0.0:
        return None

    return tuple(float(component) / norm_m for component in vector_m)


def _dot_vector3(lhs_m: Sequence[float], rhs_m: Sequence[float]) -> float:
    return sum(float(lhs_m[index]) * float(rhs_m[index]) for index in range(3))


def _dominant_axis_index(vector_m: Sequence[float]) -> int:
    return max(range(3), key=lambda index: abs(float(vector_m[index])))


def _dominant_axis_label(vector_m: Sequence[float]) -> str:
    if _vector_norm_m(vector_m) <= _PERTURBATION_NO_MOVEMENT_EPSILON_M:
        return "none"
    return "xyz"[_dominant_axis_index(vector_m)]


def _dominant_axis_sign(vector_m: Sequence[float]) -> int:
    if _vector_norm_m(vector_m) <= _PERTURBATION_NO_MOVEMENT_EPSILON_M:
        return 0
    component = float(vector_m[_dominant_axis_index(vector_m)])
    return 1 if component >= 0.0 else -1


def _axis_label(axis: str, sign: int) -> str:
    return f"{'+' if sign > 0 else '-'}{axis}"


def _axis_delta(axis: str, sign: int, delta_m: float) -> Vector3:
    if axis == "x":
        return (float(sign) * delta_m, 0.0, 0.0)
    if axis == "y":
        return (0.0, float(sign) * delta_m, 0.0)
    if axis == "z":
        return (0.0, 0.0, float(sign) * delta_m)
    raise ValueError(f"unsupported axis: {axis!r}")


def _axis_index(axis: str) -> int:
    return "xyz".index(axis)


def _vector_subtract(lhs_m: Sequence[float], rhs_m: Sequence[float]) -> Vector3:
    return tuple(float(lhs_m[index]) - float(rhs_m[index]) for index in range(3))


def _vector_add(lhs_m: Sequence[float], rhs_m: Sequence[float]) -> Vector3:
    return tuple(float(lhs_m[index]) + float(rhs_m[index]) for index in range(3))


def _body_position_from_state(state: MuJoCoState, body_name: str) -> Vector3 | None:
    for body in state.bodies:
        if body.name == body_name:
            return body.position_m
    return None


def _mujoco_qpos_to_solver_joint_angles(qpos_rad: Sequence[float]) -> tuple[float, ...]:
    qpos = tuple(float(value) for value in qpos_rad[:4])
    if len(qpos) != 4:
        return qpos
    return (
        qpos[0],
        qpos[1] - _MUJOCO_QPOS_REF_MINUS_90_RAD,
        qpos[2],
        qpos[3],
    )


def _solver_joint_angles_to_mujoco_qpos(
    solver_joint_angles_rad: Sequence[float],
    *,
    current_qpos_rad: Sequence[float],
) -> tuple[float, ...]:
    solver_qpos = tuple(float(value) for value in solver_joint_angles_rad[:4])
    current_qpos = tuple(float(value) for value in current_qpos_rad[:4])
    if len(solver_qpos) != 4 or len(current_qpos) != 4:
        return solver_qpos

    return (
        current_qpos[0],
        solver_qpos[1] + _MUJOCO_QPOS_REF_MINUS_90_RAD,
        current_qpos[2],
        current_qpos[3],
    )


def _workspace_summary() -> dict[str, object]:
    link_lengths_m = tuple(float(length) for length in FAST_ARM_ENDPOINT_LINK_LENGTHS_M)
    min_radius_m = abs(link_lengths_m[0] - sum(link_lengths_m[1:]))
    max_radius_m = sum(link_lengths_m)
    return {
        "solver_base_position_m": FAST_ARM_ENDPOINT_BASE_POSITION_M,
        "link_lengths_m": link_lengths_m,
        "min_radius_m": min_radius_m,
        "max_radius_m": max_radius_m,
        "distance_rule": "Euclidean distance from solver_base_position_m must be within min/max radius",
    }


def _target_constraints_summary() -> dict[str, object]:
    return {
        "target_rejection_reasons": (
            "target_unreachable",
            "target_non_convergence",
            "target_discontinuous",
        ),
        "target_unreachable_message": "target_position_m is outside the reachable workspace",
        "target_non_convergence_message": "target_position_m did not converge",
        "last_valid_target_position_m": _UNAVAILABLE,
    }


def _frame_mapping_summary() -> dict[str, object]:
    return {
        "command_frame": "command-side endpoint frame",
        "solver_frame": f"FastArmEndpoint local frame rooted at MuJoCo body '{_MUJOCO_SOLVER_BASE_BODY_NAME}'",
        "mujoco_tip_frame": "MuJoCo world / scene frame",
        "mapping_status": "world target is transformed to solver local target",
        "known_mujoco_offset": (
            "fast_arm MJCF places base_link near world z=0.7 "
            "and sholder_joint_2 has ref=-90"
        ),
    }


def _qpos_ref_summary() -> dict[str, object]:
    return {
        "mapping_status": "q1_ref_adapter_with_q0_q2_q3_hold",
        "mujoco_to_solver": "solver_q1 = mujoco_qpos1 + pi/2",
        "solver_to_mujoco": "mujoco_qpos1 = solver_q1 - pi/2",
        "held_joints": ("qpos0", "qpos2", "qpos3"),
        "limitation": (
            "q0/q2/q3 solver values are not applied because MuJoCo perturbation "
            "diagnostics do not match the solver's yaw/planar joint convention"
        ),
    }


def _solver_to_mujoco_mapping_summary() -> dict[str, object]:
    return {
        "q0": "held at current MuJoCo qpos0; solver q0 is yaw but MuJoCo qpos0 axis is shoulder pitch-like",
        "q1": "mujoco_qpos1 = solver_q1 - pi/2",
        "q2": "held at current MuJoCo qpos2; solver q2 is planar bend but MuJoCo qpos2 axis duplicates qpos0 pitch-like axis",
        "q3": "held at current MuJoCo qpos3; MuJoCo qpos3 is local elbow z-axis and is not solver base yaw",
    }


def _mujoco_to_solver_mapping_summary() -> dict[str, object]:
    return {
        "qpos0": "solver seed q0 = mujoco_qpos0 for continuity only",
        "qpos1": "solver seed q1 = mujoco_qpos1 + pi/2",
        "qpos2": "solver seed q2 = mujoco_qpos2 for continuity only",
        "qpos3": "solver seed q3 = mujoco_qpos3 for continuity only",
    }


def _joint_axis_mapping_summary(
    perturbation_results: Sequence["FastArmJointAxisPerturbationResult"],
) -> dict[str, object]:
    return {
        "mapping_status": "q1_ref_adapter_with_q0_q2_q3_hold",
        "perturbation_rad": _JOINT_AXIS_PERTURBATION_RAD,
        "solver_to_mujoco_mapping": _solver_to_mujoco_mapping_summary(),
        "mujoco_to_solver_mapping": _mujoco_to_solver_mapping_summary(),
        "mapping_decision": (
            "keep q0/q2/q3 held in endpoint sanity helper; do not claim x/y "
            "alignment until a 3D solver DOF allocation matches the MuJoCo axes"
        ),
        "joint_order": tuple(result.joint_name for result in perturbation_results),
    }


def _diagnose_case(
    *,
    distance_from_solver_base_m: float | str,
    target_rejected: bool,
    target_rejection_reason: str | None,
    initial_tip_position_m: Vector3 | None,
    qpos_before: tuple[float, ...],
    solver_seed_qpos: tuple[float, ...] | str,
) -> str:
    workspace = _workspace_summary()
    max_radius_m = float(workspace["max_radius_m"])
    if isinstance(distance_from_solver_base_m, float) and distance_from_solver_base_m > max_radius_m + 1e-9:
        return "initial_tip_target_outside_solver_reachable_workspace"
    if target_rejected and target_rejection_reason == "target_unreachable":
        return "solver_rejected_target_as_unreachable"
    if target_rejected and target_rejection_reason == "target_non_convergence":
        return "solver_rejected_target_after_non_convergence"
    if initial_tip_position_m is not None:
        initial_tip_to_solver_base_m = _vector_norm_m(
            _vector_subtract(initial_tip_position_m, FAST_ARM_ENDPOINT_BASE_POSITION_M)
        )
        if initial_tip_to_solver_base_m > max_radius_m + 1e-9:
            return "initial_tip_outside_solver_reachable_workspace"
    if solver_seed_qpos == _UNAVAILABLE and qpos_before:
        return "current_qpos_not_used_as_solver_seed_in_runtime_pipeline"
    return "no_single_cause_identified"


@dataclass(frozen=True, slots=True)
class FastArmEndpointMotionSanityResult:
    """1 axis command caseのintent・execution・MuJoCo measurementを分離した結果。"""

    axis: str
    sign: int
    command_label: str
    commanded_delta_m: Vector3
    base_endpoint_m: Vector3 | None
    base_endpoint_source: str
    initial_tip_position_m: Vector3 | None
    desired_endpoint_m: Vector3 | None
    target_position_m: Vector3 | None
    final_tip_position_m: Vector3 | None
    actual_delta_m: Vector3 | None
    command_direction_m: Vector3
    actual_direction_m: Vector3 | None
    direction_dot: float | None
    direction_matches: bool | None
    status: str
    reason: str
    qpos_before: tuple[float, ...]
    qpos_after: tuple[float, ...]
    desired_endpoint_source: str | None
    target_rejected: bool
    target_rejection_reason: str | None = None
    target_rejection_message: str | None = None
    error_message: str | None = None
    solver_input_endpoint_m: Vector3 | None = None
    solver_seed_qpos: tuple[float, ...] | str = _UNAVAILABLE
    solver_result_qpos: tuple[float, ...] | str = _UNAVAILABLE
    reachable_workspace_summary: dict[str, object] | str = _UNAVAILABLE
    distance_from_solver_base_m: float | str = _UNAVAILABLE
    target_constraints_summary: dict[str, object] | str = _UNAVAILABLE
    frame_mapping_summary: dict[str, object] | str = _UNAVAILABLE
    diagnosis: str = _UNAVAILABLE
    rejected_desired_endpoint_m: Vector3 | str = _UNAVAILABLE
    last_valid_target_position_m: Vector3 | str = _UNAVAILABLE
    mujoco_base_link_position_m: Vector3 | str = _UNAVAILABLE
    mujoco_base_link_frame: str = _UNAVAILABLE
    mujoco_tip_position_m: Vector3 | str = _UNAVAILABLE
    tip_relative_to_base_link_m: Vector3 | str = _UNAVAILABLE
    tip_relative_to_solver_base_m: Vector3 | str = _UNAVAILABLE
    solver_base_world_position_m: Vector3 | str = _UNAVAILABLE
    solver_local_target_m: Vector3 | str = _UNAVAILABLE
    world_target_m: Vector3 | str = _UNAVAILABLE
    frame_transform_status: str = _UNAVAILABLE
    qpos_ref_summary: dict[str, object] | str = _UNAVAILABLE
    solver_fk_endpoint_m: Vector3 | str = _UNAVAILABLE
    transformed_solver_fk_world_m: Vector3 | str = _UNAVAILABLE
    joint_axis_mapping_summary: dict[str, object] | str = _UNAVAILABLE
    qpos_perturbation_results: tuple["FastArmJointAxisPerturbationResult", ...] = ()
    solver_to_mujoco_mapping: dict[str, object] | str = _UNAVAILABLE
    mujoco_to_solver_mapping: dict[str, object] | str = _UNAVAILABLE
    mapping_status: str = _UNAVAILABLE


@dataclass(frozen=True, slots=True)
class FastArmEndpointDiagnosticRecord:
    """1 diagnostic layerのstatus、world-vector(m)、provenance record。"""

    step_index: int
    command_label: str
    base_endpoint_source: str
    desired_endpoint_source: str | None
    desired_endpoint_m: Vector3 | None
    actual_tip_position_m: Vector3 | None
    endpoint_error_m: Vector3 | None
    endpoint_error_norm_m: float | None
    qpos_before: tuple[float, ...]
    qpos_after: tuple[float, ...]
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class FastArmFkSiteConsistencyDiagnostic:
    """solver FKとMuJoCo tip siteのworld position(m)差分診断。"""

    fixture_label: str
    qpos: tuple[float, ...]
    solver_qpos: tuple[float, ...]
    fk_endpoint_m: Vector3
    transformed_solver_fk_world_m: Vector3
    mujoco_tip_site_position_m: Vector3
    fk_site_error_m: Vector3
    fk_site_error_norm_m: float
    status: str
    reason: str
    site_name: str | None = None
    model_path: str | None = None
    joint_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FastArmIkFkSanityDiagnostic:
    """world target(m)に対するIK/FK round-tripとfailure reasonの診断。"""

    fixture_label: str
    target_endpoint_m: Vector3
    ik_input_target_m: Vector3
    ik_output_qpos: tuple[float, ...] | None
    fk_endpoint_from_ik_qpos_m: Vector3 | None
    ik_fk_error_m: Vector3 | None
    ik_fk_error_norm_m: float | None
    ik_status: str
    status: str
    reason: str
    known_fk_site_consistency_status: str
    known_fk_site_consistency_note: str
    seed_qpos: tuple[float, ...] | None = None
    joint_names: tuple[str, ...] = ()
    model_path: str | None = None


@dataclass(frozen=True, slots=True)
class FastArmViewerEndpointWorkspaceDiagnostic:
    """MuJoCo stateからviewerへ投影するtip site world position(m) sample。"""

    sample_label: str
    sample_kind: str
    qpos_sample: tuple[float, ...]
    mujoco_tip_site_world_position_m: Vector3
    solver_local_fk_endpoint_m: Vector3
    model_aligned_fk_endpoint_m: Vector3
    desired_world_endpoint_m: Vector3
    solver_local_ik_target_m: Vector3
    ik_success: bool
    ik_output_qpos_rad: tuple[float, ...] | None
    qpos_delta_norm_from_seed_rad: float | None
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class FastArmJointAxisPerturbationResult:
    """1 qpos indexへrad摂動した際のtip world delta(m)とaxis mapping evidence。"""

    joint_name: str
    qpos_index: int
    mujoco_joint_axis: Vector3
    mujoco_joint_ref_rad: float
    perturbation_rad: float
    qpos_before: tuple[float, ...]
    qpos_after: tuple[float, ...]
    tip_before: Vector3
    tip_after: Vector3
    tip_delta_m: Vector3
    dominant_axis: str
    dominant_sign: int
    direction_dot_to_positive_axes: dict[str, float]
    solver_to_mujoco_mapping: str
    mujoco_to_solver_mapping: str
    mapping_status: str


@dataclass(frozen=True, slots=True)
class FastArmLocalJacobianColumn:
    """central differenceで得た1 jointのlocal Jacobian column。unitはm/rad。"""

    pose_label: str
    qpos: tuple[float, ...]
    qpos_index: int
    joint_name: str
    joint_axis: Vector3
    perturbation_rad: float
    plus_tip_delta_m: Vector3
    minus_tip_delta_m: Vector3
    central_difference_column: Vector3
    dominant_axis: str
    dominant_sign: int
    norm: float


@dataclass(frozen=True, slots=True)
class FastArmLocalJacobianPoseDiagnostics:
    """1 poseにおけるjoint ordering付きlocal Jacobian診断。"""

    pose_label: str
    qpos: tuple[float, ...]
    tip_position_m: Vector3
    perturbation_rad: float
    jacobian_matrix: tuple[tuple[float, float, float, float], tuple[float, float, float, float], tuple[float, float, float, float]]
    columns: tuple[FastArmLocalJacobianColumn, ...]
    joint_contribution_summary: dict[str, object]


@dataclass(frozen=True, slots=True)
class FastArmEndpointTrajectoryStepRecord:
    """trajectory 1 stepのdesired/measured endpoint(m)とreject/hold状態。"""

    command_label: str
    step_index: int
    desired_endpoint_m: Vector3
    solver_local_target_m: Vector3 | str
    qpos_before: tuple[float, ...]
    qpos_after: tuple[float, ...]
    tip_before_m: Vector3
    tip_after_m: Vector3
    actual_delta_m: Vector3
    cumulative_actual_delta_m: Vector3
    command_direction_m: Vector3
    direction_dot: float | None
    dominant_axis: str
    status: str
    reason: str
    target_rejected: bool
    target_rejection_reason: str | None
    distance_from_solver_base_m: float | str


@dataclass(frozen=True, slots=True)
class FastArmEndpointTrajectorySummary:
    """trajectory全体のerror/rejection/continuity集計。distance unitはm。"""

    command_label: str
    step_count: int
    command_delta_m_per_step: float
    initial_alignment: str
    final_alignment: str
    best_alignment: float | None
    worst_alignment: float | None
    mean_direction_dot: float | None
    drift_from_command_axis_m: float
    orthogonal_drift_m: float
    cumulative_commanded_delta_m: Vector3
    cumulative_actual_delta_m: Vector3
    saturation_step: int | None
    first_rejection_step: int | None
    first_off_plane_step: int | None
    first_opposite_direction_step: int | None
    safe_hold_step: int | None
    final_status: str
    final_reason: str
    decision: str


@dataclass(frozen=True, slots=True)
class FastArmEndpointTrajectoryDiagnostics:
    """axis別trajectory record列とsummaryを束ねるdiagnostic結果。"""

    command_label: str
    axis: str
    sign: int
    command_delta_m_per_step: float
    step_count: int
    initial_tip_position_m: Vector3
    initial_qpos: tuple[float, ...]
    records: tuple[FastArmEndpointTrajectoryStepRecord, ...]
    summary: FastArmEndpointTrajectorySummary


def _vector_error_m(lhs_m: Sequence[float], rhs_m: Sequence[float]) -> Vector3:
    return tuple(float(lhs_m[index]) - float(rhs_m[index]) for index in range(3))


def _endpoint_error_vector_m(
    desired_endpoint_m: Sequence[float],
    actual_tip_position_m: Sequence[float],
) -> Vector3:
    return tuple(
        float(desired_endpoint_m[index]) - float(actual_tip_position_m[index])
        for index in range(3)
    )


def _ik_fk_error_vector_m(
    target_endpoint_m: Sequence[float],
    fk_endpoint_m: Sequence[float],
) -> Vector3:
    return _endpoint_error_vector_m(target_endpoint_m, fk_endpoint_m)


def _build_fast_arm_endpoint_diagnostic_records(
    results: Sequence[FastArmEndpointMotionSanityResult],
) -> tuple[FastArmEndpointDiagnosticRecord, ...]:
    records: list[FastArmEndpointDiagnosticRecord] = []
    for step_index, result in enumerate(results, start=1):
        desired_endpoint_m = result.desired_endpoint_m
        actual_tip_position_m = result.final_tip_position_m
        endpoint_error_m = (
            None
            if desired_endpoint_m is None or actual_tip_position_m is None
            else _endpoint_error_vector_m(desired_endpoint_m, actual_tip_position_m)
        )
        records.append(
            FastArmEndpointDiagnosticRecord(
                step_index=step_index,
                command_label=result.command_label,
                base_endpoint_source=result.base_endpoint_source,
                desired_endpoint_source=result.desired_endpoint_source,
                desired_endpoint_m=desired_endpoint_m,
                actual_tip_position_m=actual_tip_position_m,
                endpoint_error_m=endpoint_error_m,
                endpoint_error_norm_m=(
                    None if endpoint_error_m is None else _vector_norm_m(endpoint_error_m)
                ),
                qpos_before=result.qpos_before,
                qpos_after=result.qpos_after,
                status=result.status,
                reason=result.reason,
            )
        )
    return tuple(records)


def _fast_arm_fk_site_consistency_qpos_fixtures(
    *,
    model_path: str | Path | None = None,
) -> tuple[tuple[str, tuple[float, ...]], ...]:
    simulator = _build_fast_arm_simulator(model_path)
    initial_qpos = tuple(float(value) for value in simulator.snapshot().qpos[:4])
    qpos_offset = (0.02, 0.01, 0.015, 0.005)
    positive_qpos = tuple(initial_qpos[index] + qpos_offset[index] for index in range(4))
    negative_qpos = tuple(initial_qpos[index] - qpos_offset[index] for index in range(4))

    representative_motion_results = run_fast_arm_endpoint_motion_sanity(model_path=model_path)
    representative_qpos = tuple(float(value) for value in representative_motion_results[0].qpos_after[:4])

    return (
        ("default_qpos", initial_qpos),
        ("small_positive_perturbation", positive_qpos),
        ("small_negative_perturbation", negative_qpos),
        ("representative_endpoint_motion_sanity_qpos", representative_qpos),
    )


def _fast_arm_ik_fk_sanity_target_fixtures(
    *,
    model_path: str | Path | None = None,
) -> tuple[tuple[str, Vector3, tuple[float, ...], str], ...]:
    simulator = _build_fast_arm_simulator(model_path)
    initial_state = simulator.snapshot()
    default_tip_position_m = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m
    seed_qpos = tuple(float(value) for value in initial_state.qpos[:4])
    small_delta_m = 0.01
    representative_delta_m = 0.02
    return (
        (
            "default_tip_position",
            default_tip_position_m,
            seed_qpos,
            "reachability_unverified: target derived from the default tip position",
        ),
        (
            "small_positive_x_target",
            _vector_add(default_tip_position_m, (small_delta_m, 0.0, 0.0)),
            seed_qpos,
            "reachability_unverified: small positive x offset from the default tip position",
        ),
        (
            "small_positive_z_target",
            _vector_add(default_tip_position_m, (0.0, 0.0, small_delta_m)),
            seed_qpos,
            "reachability_unverified: small positive z offset from the default tip position",
        ),
        (
            "representative_endpoint_motion_sanity_target",
            _vector_add(default_tip_position_m, (0.0, 0.0, representative_delta_m)),
            seed_qpos,
            "reachability_unverified: representative short-step target derived from endpoint motion sanity",
        ),
    )


def _fk_site_consistency_status_reason(
    *,
    fk_site_error_norm_m: float,
    tip_site_kind: str,
) -> tuple[str, str]:
    if tip_site_kind != "site":
        return "mismatch", "tip_site_reference_is_not_primary"
    if fk_site_error_norm_m <= _FK_SITE_CONSISTENCY_TOLERANCE_M:
        return "pass", "fk_endpoint_matches_tip_site_within_tolerance"
    return "mismatch", "remaining_model_axis_or_link_contract_mismatch"


def _build_fast_arm_fk_site_consistency_diagnostic(
    *,
    fixture_label: str,
    qpos: Sequence[float],
    model_path: str | Path | None = None,
) -> FastArmFkSiteConsistencyDiagnostic:
    qpos_tuple = tuple(float(value) for value in qpos[:4])
    if len(qpos_tuple) != 4:
        raise ValueError("qpos must contain exactly four values")

    simulator = _build_fast_arm_simulator(model_path)
    simulator.apply_qpos_command(JointCommand(joint_angles_rad=qpos_tuple))
    state = simulator.snapshot()
    tip_site = extract_fast_arm_tip_site_endpoint_from_state(state)
    solver_qpos = _mujoco_qpos_to_solver_joint_angles(qpos_tuple)
    fk_evaluation = evaluate_fk_endpoint_from_qpos(
        FastArmMuJoCoModelForwardKinematicsSolver(),
        qpos_tuple,
        solver_joint_count=4,
    )
    transformed_solver_fk_world_m = fk_evaluation.endpoint_m
    fk_site_error_m = _vector_subtract(transformed_solver_fk_world_m, tip_site.position_m)
    fk_site_error_norm_m = _vector_norm_m(fk_site_error_m)
    status, reason = _fk_site_consistency_status_reason(
        fk_site_error_norm_m=fk_site_error_norm_m,
        tip_site_kind=tip_site.kind,
    )
    joint_names = inspect_mujoco_model(simulator.model).joint_names[:4]
    return FastArmFkSiteConsistencyDiagnostic(
        fixture_label=fixture_label,
        qpos=qpos_tuple,
        solver_qpos=solver_qpos,
        fk_endpoint_m=fk_evaluation.endpoint_m,
        transformed_solver_fk_world_m=transformed_solver_fk_world_m,
        mujoco_tip_site_position_m=tip_site.position_m,
        fk_site_error_m=fk_site_error_m,
        fk_site_error_norm_m=fk_site_error_norm_m,
        status=status,
        reason=reason,
        site_name=tip_site.name,
        model_path=(str(Path(model_path)) if model_path is not None else None),
        joint_names=tuple(joint_names),
    )


def run_fast_arm_fk_site_consistency_diagnostics(
    *,
    model_path: str | Path | None = None,
    qpos_fixtures: Sequence[tuple[str, Sequence[float]]] | None = None,
) -> tuple[FastArmFkSiteConsistencyDiagnostic, ...]:
    """fixture qposごとにsolver FKとMuJoCo tip siteをworld frame(m)で比較する。"""

    fixtures = (
        _fast_arm_fk_site_consistency_qpos_fixtures(model_path=model_path)
        if qpos_fixtures is None
        else tuple((label, tuple(float(value) for value in qpos)) for label, qpos in qpos_fixtures)
    )
    return tuple(
        _build_fast_arm_fk_site_consistency_diagnostic(
            fixture_label=label,
            qpos=qpos,
            model_path=model_path,
        )
        for label, qpos in fixtures
    )


def _ik_fk_sanity_status_reason(
    *,
    ik_status: str,
    ik_fk_error_norm_m: float | None,
    fixture_note: str,
) -> tuple[str, str]:
    if ik_status != "solved":
        return "ik_failed", "target_position_m did not converge"
    if ik_fk_error_norm_m is None:
        return "diagnostic_only", "ik_solved_but_fk_endpoint_was_unavailable"
    if ik_fk_error_norm_m <= _IK_FK_SANITY_TOLERANCE_M:
        return "pass", f"ik_solved_and_target_vs_fk_within_tolerance; {fixture_note}"
    return "mismatch", f"ik_solved_but_target_vs_fk_error_exceeds_tolerance; {fixture_note}"


def _build_fast_arm_ik_fk_sanity_diagnostic(
    *,
    fixture_label: str,
    target_endpoint_m: Sequence[float],
    seed_qpos: Sequence[float] | None = None,
    model_path: str | Path | None = None,
    fixture_note: str = "reachability_unverified",
) -> FastArmIkFkSanityDiagnostic:
    target_endpoint_tuple = _coerce_vector3("target_endpoint_m", target_endpoint_m)
    seed_qpos_tuple = None if seed_qpos is None else tuple(float(value) for value in seed_qpos[:4])
    model_path_value = str(Path(model_path)) if model_path is not None else None
    simulator = _build_fast_arm_simulator(model_path)
    initial_state = simulator.snapshot()
    solver_base_world_position_m = _body_position_from_state(initial_state, _MUJOCO_SOLVER_BASE_BODY_NAME)
    joint_names = tuple(inspect_mujoco_model(simulator.model).joint_names[:4])
    ik_solver = FastArmEndpointInverseKinematicsSolver()
    fk_solver = FastArmEndpointForwardKinematicsSolver()

    if solver_base_world_position_m is None:
        return FastArmIkFkSanityDiagnostic(
            fixture_label=fixture_label,
            target_endpoint_m=target_endpoint_tuple,
            ik_input_target_m=target_endpoint_tuple,
            ik_output_qpos=None,
            fk_endpoint_from_ik_qpos_m=None,
            ik_fk_error_m=None,
            ik_fk_error_norm_m=None,
            ik_status="failed",
            status="diagnostic_only",
            reason="missing MuJoCo body 'base_link'; " + fixture_note,
            known_fk_site_consistency_status=_KNOWN_FK_SITE_CONSISTENCY_STATUS,
            known_fk_site_consistency_note=_KNOWN_FK_SITE_CONSISTENCY_NOTE,
            seed_qpos=seed_qpos_tuple,
            joint_names=joint_names,
            model_path=model_path_value,
        )

    ik_input_target_m = _vector_subtract(target_endpoint_tuple, solver_base_world_position_m)

    try:
        solver_joint_command = ik_solver.solve(
            ik_input_target_m,
            seed_joint_angles_rad=seed_qpos_tuple,
        )
    except ValueError as exc:
        return FastArmIkFkSanityDiagnostic(
            fixture_label=fixture_label,
            target_endpoint_m=target_endpoint_tuple,
            ik_input_target_m=ik_input_target_m,
            ik_output_qpos=None,
            fk_endpoint_from_ik_qpos_m=None,
            ik_fk_error_m=None,
            ik_fk_error_norm_m=None,
            ik_status="failed",
            status="ik_failed",
            reason=f"{exc}; {fixture_note}",
            known_fk_site_consistency_status=_KNOWN_FK_SITE_CONSISTENCY_STATUS,
            known_fk_site_consistency_note=_KNOWN_FK_SITE_CONSISTENCY_NOTE,
            seed_qpos=seed_qpos_tuple,
            joint_names=joint_names,
            model_path=model_path_value,
        )

    ik_output_qpos = tuple(float(value) for value in solver_joint_command.joint_angles_rad[:4])
    fk_evaluation = evaluate_fk_endpoint_from_qpos(
        fk_solver,
        ik_output_qpos,
        solver_joint_count=4,
    )
    fk_endpoint_from_ik_qpos_m = _vector_add(fk_evaluation.endpoint_m, solver_base_world_position_m)
    ik_fk_error_m = _ik_fk_error_vector_m(target_endpoint_tuple, fk_endpoint_from_ik_qpos_m)
    ik_fk_error_norm_m = _vector_norm_m(ik_fk_error_m)
    status, reason = _ik_fk_sanity_status_reason(
        ik_status="solved",
        ik_fk_error_norm_m=ik_fk_error_norm_m,
        fixture_note=fixture_note,
    )
    return FastArmIkFkSanityDiagnostic(
        fixture_label=fixture_label,
        target_endpoint_m=target_endpoint_tuple,
        ik_input_target_m=ik_input_target_m,
        ik_output_qpos=ik_output_qpos,
        fk_endpoint_from_ik_qpos_m=fk_endpoint_from_ik_qpos_m,
        ik_fk_error_m=ik_fk_error_m,
        ik_fk_error_norm_m=ik_fk_error_norm_m,
        ik_status="solved",
        status=status,
        reason=reason,
        known_fk_site_consistency_status=_KNOWN_FK_SITE_CONSISTENCY_STATUS,
        known_fk_site_consistency_note=_KNOWN_FK_SITE_CONSISTENCY_NOTE,
        seed_qpos=seed_qpos_tuple,
        joint_names=joint_names,
        model_path=model_path_value,
    )


def run_fast_arm_ik_fk_sanity_diagnostics(
    *,
    model_path: str | Path | None = None,
    target_fixtures: Sequence[tuple[str, Sequence[float], Sequence[float] | None, str]] | None = None,
) -> tuple[FastArmIkFkSanityDiagnostic, ...]:
    """world target fixtureへIK/FKを適用し、unreachableを理由付きで記録する。"""

    fixtures = (
        _fast_arm_ik_fk_sanity_target_fixtures(model_path=model_path)
        if target_fixtures is None
        else tuple(
            (
                label,
                tuple(float(value) for value in target_endpoint_m),
                None if seed_qpos is None else tuple(float(value) for value in seed_qpos),
                fixture_note,
            )
            for label, target_endpoint_m, seed_qpos, fixture_note in target_fixtures
        )
    )
    return tuple(
        _build_fast_arm_ik_fk_sanity_diagnostic(
            fixture_label=label,
            target_endpoint_m=target_endpoint_m,
            seed_qpos=seed_qpos,
            model_path=model_path,
            fixture_note=fixture_note,
        )
        for label, target_endpoint_m, seed_qpos, fixture_note in fixtures
    )


def _ik_fk_sanity_diagnostic_row(
    record: FastArmIkFkSanityDiagnostic,
) -> dict[str, object]:
    return {
        "fixture_label": record.fixture_label,
        "target_endpoint_m": record.target_endpoint_m,
        "ik_input_target_m": record.ik_input_target_m,
        "ik_output_qpos": record.ik_output_qpos if record.ik_output_qpos is not None else _UNAVAILABLE,
        "fk_endpoint_from_ik_qpos_m": (
            record.fk_endpoint_from_ik_qpos_m if record.fk_endpoint_from_ik_qpos_m is not None else _UNAVAILABLE
        ),
        "ik_fk_error_m": record.ik_fk_error_m if record.ik_fk_error_m is not None else _UNAVAILABLE,
        "ik_fk_error_norm_m": (
            record.ik_fk_error_norm_m if record.ik_fk_error_norm_m is not None else _UNAVAILABLE
        ),
        "ik_status": record.ik_status,
        "status": record.status,
        "reason": record.reason,
        "known_fk_site_consistency_status": record.known_fk_site_consistency_status,
        "known_fk_site_consistency_note": record.known_fk_site_consistency_note,
        "seed_qpos": record.seed_qpos if record.seed_qpos is not None else _UNAVAILABLE,
        "joint_names": record.joint_names or _UNAVAILABLE,
        "model_path": record.model_path or _UNAVAILABLE,
    }


def build_fast_arm_ik_fk_sanity_log_rows(
    records: Sequence[FastArmIkFkSanityDiagnostic],
) -> tuple[dict[str, object], ...]:
    """IK/FK結果をJSON-compatible rowへ投影し、field semanticsを変更しない。"""

    return tuple(_ik_fk_sanity_diagnostic_row(record) for record in records)


def write_fast_arm_ik_fk_sanity_log_jsonl(
    records: Sequence[FastArmIkFkSanityDiagnostic],
    output_path: str | Path,
) -> Path:
    """IK/FK rowを指定pathへUTF-8 JSONLとして書き込む唯一のfile side effect。"""

    rows = build_fast_arm_ik_fk_sanity_log_rows(records)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")
    return path


def _fk_site_consistency_diagnostic_row(
    record: FastArmFkSiteConsistencyDiagnostic,
) -> dict[str, object]:
    return {
        "fixture_label": record.fixture_label,
        "qpos": record.qpos,
        "solver_qpos": record.solver_qpos,
        "fk_endpoint_m": record.fk_endpoint_m,
        "transformed_solver_fk_world_m": record.transformed_solver_fk_world_m,
        "mujoco_tip_site_position_m": record.mujoco_tip_site_position_m,
        "fk_site_error_m": record.fk_site_error_m,
        "fk_site_error_norm_m": record.fk_site_error_norm_m,
        "status": record.status,
        "reason": record.reason,
        "site_name": record.site_name or _UNAVAILABLE,
        "model_path": record.model_path or _UNAVAILABLE,
        "joint_names": record.joint_names or _UNAVAILABLE,
    }


def build_fast_arm_fk_site_consistency_log_rows(
    records: Sequence[FastArmFkSiteConsistencyDiagnostic],
) -> tuple[dict[str, object], ...]:
    """FK/site結果をJSON-compatible rowへ投影する。"""

    return tuple(_fk_site_consistency_diagnostic_row(record) for record in records)


def write_fast_arm_fk_site_consistency_log_jsonl(
    records: Sequence[FastArmFkSiteConsistencyDiagnostic],
    output_path: str | Path,
) -> Path:
    """FK/site rowを指定pathへUTF-8 JSONLとして書き込む。"""

    rows = build_fast_arm_fk_site_consistency_log_rows(records)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")
    return path


def _endpoint_diagnostic_row_from_record(
    record: FastArmEndpointDiagnosticRecord,
) -> dict[str, object]:
    return {
        "step_index": record.step_index,
        "command_label": record.command_label,
        "base_endpoint_source": record.base_endpoint_source,
        "desired_endpoint_source": record.desired_endpoint_source or _UNAVAILABLE,
        "desired_endpoint_m": record.desired_endpoint_m or _UNAVAILABLE,
        "actual_tip_position_m": record.actual_tip_position_m or _UNAVAILABLE,
        "endpoint_error_m": record.endpoint_error_m or _UNAVAILABLE,
        "endpoint_error_norm_m": (
            record.endpoint_error_norm_m if record.endpoint_error_norm_m is not None else _UNAVAILABLE
        ),
        "qpos_before": record.qpos_before,
        "qpos_after": record.qpos_after,
        "status": record.status,
        "reason": record.reason,
    }


def build_fast_arm_endpoint_diagnostic_log_rows(
    results: Sequence[FastArmEndpointMotionSanityResult],
) -> tuple[dict[str, object], ...]:
    """endpoint sanity結果の層別recordをflat rowへ投影する。"""

    return tuple(
        _endpoint_diagnostic_row_from_record(record)
        for record in _build_fast_arm_endpoint_diagnostic_records(results)
    )


def write_fast_arm_endpoint_diagnostic_log_csv(
    results: Sequence[FastArmEndpointMotionSanityResult],
    output_path: str | Path,
) -> Path:
    """endpoint diagnostic rowを指定pathへCSVとして書き込む。"""

    rows = build_fast_arm_endpoint_diagnostic_log_rows(results)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = (
            "step_index",
            "command_label",
            "base_endpoint_source",
            "desired_endpoint_source",
            "desired_endpoint_m",
            "actual_tip_position_m",
            "endpoint_error_m",
            "endpoint_error_norm_m",
            "qpos_before",
            "qpos_after",
            "status",
            "reason",
        )
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def write_fast_arm_endpoint_diagnostic_log_jsonl(
    results: Sequence[FastArmEndpointMotionSanityResult],
    output_path: str | Path,
) -> Path:
    """endpoint diagnostic rowを指定pathへUTF-8 JSONLとして書き込む。"""

    rows = build_fast_arm_endpoint_diagnostic_log_rows(results)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")
    return path


def _trajectory_log_row_from_record(
    *,
    diagnostics: FastArmEndpointTrajectoryDiagnostics,
    record: FastArmEndpointTrajectoryStepRecord,
    dt_s: float,
) -> dict[str, object]:
    error_m = _vector_error_m(record.tip_after_m, record.desired_endpoint_m)
    return {
        "step": record.step_index,
        "time_s": float(record.step_index - 1) * float(dt_s),
        "dt_s": float(dt_s),
        "command_axis": diagnostics.axis,
        "command_label": diagnostics.command_label,
        "target_x_m": record.desired_endpoint_m[0],
        "target_y_m": record.desired_endpoint_m[1],
        "target_z_m": record.desired_endpoint_m[2],
        "tip_x_m": record.tip_after_m[0],
        "tip_y_m": record.tip_after_m[1],
        "tip_z_m": record.tip_after_m[2],
        "error_x_m": error_m[0],
        "error_y_m": error_m[1],
        "error_z_m": error_m[2],
        "error_norm_m": _vector_norm_m(error_m),
        "status": record.status,
        "reason": record.reason,
    }


def build_fast_arm_endpoint_trajectory_log_rows(
    trajectory_diagnostics: Sequence[FastArmEndpointTrajectoryDiagnostics],
    *,
    dt_s: float | None = None,
) -> tuple[dict[str, object], ...]:
    """trajectory step/summaryをunit付きJSON-compatible rowへ投影する。"""

    if dt_s is None:
        dt_s = RuntimeConfig().dt_s
    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")

    rows: list[dict[str, object]] = []
    for diagnostics in trajectory_diagnostics:
        for record in diagnostics.records:
            rows.append(
                _trajectory_log_row_from_record(
                    diagnostics=diagnostics,
                    record=record,
                    dt_s=dt_s,
                )
            )
    return tuple(rows)


def write_fast_arm_endpoint_trajectory_log_csv(
    trajectory_diagnostics: Sequence[FastArmEndpointTrajectoryDiagnostics],
    output_path: str | Path,
    *,
    dt_s: float | None = None,
) -> Path:
    """trajectory rowを指定pathへCSVとして書き込む。"""

    rows = build_fast_arm_endpoint_trajectory_log_rows(trajectory_diagnostics, dt_s=dt_s)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_TRAJECTORY_LOG_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def write_fast_arm_endpoint_trajectory_log_jsonl(
    trajectory_diagnostics: Sequence[FastArmEndpointTrajectoryDiagnostics],
    output_path: str | Path,
    *,
    dt_s: float | None = None,
) -> Path:
    """trajectory rowを指定pathへUTF-8 JSONLとして書き込む。"""

    rows = build_fast_arm_endpoint_trajectory_log_rows(trajectory_diagnostics, dt_s=dt_s)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")
    return path


def _mapping_status_for_qpos_index(qpos_index: int) -> str:
    if qpos_index == 1:
        return "mapped_with_ref_minus_90_adapter"
    return "diagnostic_only_held_current"


def _solver_to_mujoco_mapping_for_qpos_index(qpos_index: int) -> str:
    return str(_solver_to_mujoco_mapping_summary()[f"q{qpos_index}"])


def _mujoco_to_solver_mapping_for_qpos_index(qpos_index: int) -> str:
    return str(_mujoco_to_solver_mapping_summary()[f"qpos{qpos_index}"])


def _build_joint_axis_perturbation_result(
    *,
    simulator: HeadlessMuJoCoSimulator,
    joint_name: str,
    qpos_index: int,
    mujoco_joint_axis: Vector3,
    perturbation_rad: float,
) -> FastArmJointAxisPerturbationResult:
    initial_state = simulator.snapshot()
    qpos_before = tuple(initial_state.qpos[:4])
    tip_before = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m
    qpos_after_values = list(qpos_before)
    qpos_after_values[qpos_index] += perturbation_rad
    qpos_after = tuple(qpos_after_values)

    simulator.apply_qpos_command(JointCommand(joint_angles_rad=qpos_after))
    perturbed_state = simulator.snapshot()
    tip_after = extract_fast_arm_tip_site_endpoint_from_state(perturbed_state).position_m
    tip_delta_m = _vector_subtract(tip_after, tip_before)
    actual_direction_m = (
        (0.0, 0.0, 0.0)
        if _vector_norm_m(tip_delta_m) <= _PERTURBATION_NO_MOVEMENT_EPSILON_M
        else (_normalize_vector3(tip_delta_m) or (0.0, 0.0, 0.0))
    )

    return FastArmJointAxisPerturbationResult(
        joint_name=joint_name,
        qpos_index=qpos_index,
        mujoco_joint_axis=mujoco_joint_axis,
        mujoco_joint_ref_rad=qpos_before[qpos_index],
        perturbation_rad=perturbation_rad,
        qpos_before=qpos_before,
        qpos_after=qpos_after,
        tip_before=tip_before,
        tip_after=tip_after,
        tip_delta_m=tip_delta_m,
        dominant_axis=_dominant_axis_label(tip_delta_m),
        dominant_sign=_dominant_axis_sign(tip_delta_m),
        direction_dot_to_positive_axes={
            "x": _dot_vector3(actual_direction_m, (1.0, 0.0, 0.0)),
            "y": _dot_vector3(actual_direction_m, (0.0, 1.0, 0.0)),
            "z": _dot_vector3(actual_direction_m, (0.0, 0.0, 1.0)),
        },
        solver_to_mujoco_mapping=_solver_to_mujoco_mapping_for_qpos_index(qpos_index),
        mujoco_to_solver_mapping=_mujoco_to_solver_mapping_for_qpos_index(qpos_index),
        mapping_status=_mapping_status_for_qpos_index(qpos_index),
    )


def run_fast_arm_joint_axis_mapping_diagnostics(
    *,
    model_path: str | Path | None = None,
    perturbation_rad: float = _JOINT_AXIS_PERTURBATION_RAD,
) -> tuple[FastArmJointAxisPerturbationResult, ...]:
    """各jointへrad摂動を与え、MuJoCo tip siteのworld delta(m)を測る。"""

    if perturbation_rad <= 0.0:
        raise ValueError("perturbation_rad must be positive")

    simulator = _build_fast_arm_simulator(model_path)
    mujoco = simulator._import_mujoco()
    joint_names = inspect_mujoco_model(simulator.model).joint_names
    results: list[FastArmJointAxisPerturbationResult] = []
    for joint_name in joint_names[:4]:
        joint_id = mujoco.mj_name2id(simulator.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        qpos_index = int(simulator.model.jnt_qposadr[joint_id])
        axis = tuple(float(component) for component in simulator.model.jnt_axis[joint_id])
        if len(axis) != 3:
            raise ValueError("mujoco_joint_axis must contain exactly three values")
        result_simulator = _build_fast_arm_simulator(model_path)
        results.append(
            _build_joint_axis_perturbation_result(
                simulator=result_simulator,
                joint_name=joint_name,
                qpos_index=qpos_index,
                mujoco_joint_axis=axis,
                perturbation_rad=perturbation_rad,
            )
        )

    return tuple(results)


def _fast_arm_viewer_workspace_qpos_samples(
    *,
    initial_qpos: tuple[float, ...],
    perturbation_rad: float = 0.02,
) -> tuple[tuple[str, tuple[float, ...]], ...]:
    qpos = tuple(float(value) for value in initial_qpos[:4])
    samples = [("default_qpos", qpos)]
    for index, name in enumerate(("joint_1", "joint_2", "joint_3", "elbow")):
        samples.append(
            (
                f"{name}_positive_small",
                tuple(value + perturbation_rad if axis_index == index else value for axis_index, value in enumerate(qpos)),
            )
        )
        samples.append(
            (
                f"{name}_negative_small",
                tuple(value - perturbation_rad if axis_index == index else value for axis_index, value in enumerate(qpos)),
            )
        )
    return tuple(samples)


def _fast_arm_viewer_workspace_endpoint_samples(
    *,
    initial_tip_position_m: Vector3,
    delta_m: float = 0.01,
) -> tuple[tuple[str, Vector3], ...]:
    return (
        ("desired_initial_tip_x_positive_small", _vector_add(initial_tip_position_m, (delta_m, 0.0, 0.0))),
        ("desired_initial_tip_x_negative_small", _vector_add(initial_tip_position_m, (-delta_m, 0.0, 0.0))),
        ("desired_initial_tip_y_positive_small", _vector_add(initial_tip_position_m, (0.0, delta_m, 0.0))),
        ("desired_initial_tip_y_negative_small", _vector_add(initial_tip_position_m, (0.0, -delta_m, 0.0))),
        ("desired_initial_tip_z_positive_small", _vector_add(initial_tip_position_m, (0.0, 0.0, delta_m))),
        ("desired_initial_tip_z_negative_small", _vector_add(initial_tip_position_m, (0.0, 0.0, -delta_m))),
        ("safe_endpoint", (0.6, 0.0, 0.1)),
        ("initial_mujoco_tip", initial_tip_position_m),
    )


def _set_fast_arm_qpos(
    simulator: HeadlessMuJoCoSimulator,
    qpos: Sequence[float],
) -> None:
    simulator.apply_qpos_command(JointCommand(joint_angles_rad=tuple(float(value) for value in qpos[:4])))


def _build_fast_arm_viewer_workspace_diagnostic(
    *,
    sample_label: str,
    sample_kind: str,
    qpos_sample: tuple[float, ...],
    desired_world_endpoint_m: Vector3 | None,
    model_path: str | Path | None,
) -> FastArmViewerEndpointWorkspaceDiagnostic:
    simulator = _build_fast_arm_simulator(model_path)
    _set_fast_arm_qpos(simulator, qpos_sample)
    state = simulator.snapshot()
    mujoco_tip_site_world_position_m = extract_fast_arm_tip_site_endpoint_from_state(state).position_m
    base_world_position_m = _body_position_from_state(state, _MUJOCO_SOLVER_BASE_BODY_NAME)
    if base_world_position_m is None:
        raise ValueError(f"missing MuJoCo body {_MUJOCO_SOLVER_BASE_BODY_NAME!r}")

    solver_fk = FastArmEndpointForwardKinematicsSolver()
    model_fk = FastArmMuJoCoModelForwardKinematicsSolver()
    solver_seed_qpos = _mujoco_qpos_to_solver_joint_angles(qpos_sample)
    solver_local_fk_endpoint_m = solver_fk.forward(solver_seed_qpos)
    model_aligned_fk_endpoint_m = model_fk.forward(qpos_sample)
    world_endpoint_m = mujoco_tip_site_world_position_m if desired_world_endpoint_m is None else desired_world_endpoint_m
    solver_local_ik_target_m = _vector_subtract(world_endpoint_m, base_world_position_m)

    ik_solver = FastArmEndpointInverseKinematicsSolver()
    try:
        ik_command = ik_solver.solve(
            solver_local_ik_target_m,
            seed_joint_angles_rad=qpos_sample,
        )
    except ValueError as exc:
        return FastArmViewerEndpointWorkspaceDiagnostic(
            sample_label=sample_label,
            sample_kind=sample_kind,
            qpos_sample=qpos_sample,
            mujoco_tip_site_world_position_m=mujoco_tip_site_world_position_m,
            solver_local_fk_endpoint_m=solver_local_fk_endpoint_m,
            model_aligned_fk_endpoint_m=model_aligned_fk_endpoint_m,
            desired_world_endpoint_m=world_endpoint_m,
            solver_local_ik_target_m=solver_local_ik_target_m,
            ik_success=False,
            ik_output_qpos_rad=None,
            qpos_delta_norm_from_seed_rad=None,
            rejection_reason=(
                "target_unreachable"
                if str(exc) == "target_position_m is outside the reachable workspace"
                else "target_non_convergence"
            ),
        )

    ik_output_qpos_rad = tuple(ik_command.joint_angles_rad[:4])
    return FastArmViewerEndpointWorkspaceDiagnostic(
        sample_label=sample_label,
        sample_kind=sample_kind,
        qpos_sample=qpos_sample,
        mujoco_tip_site_world_position_m=mujoco_tip_site_world_position_m,
        solver_local_fk_endpoint_m=solver_local_fk_endpoint_m,
        model_aligned_fk_endpoint_m=model_aligned_fk_endpoint_m,
        desired_world_endpoint_m=world_endpoint_m,
        solver_local_ik_target_m=solver_local_ik_target_m,
        ik_success=True,
        ik_output_qpos_rad=ik_output_qpos_rad,
        qpos_delta_norm_from_seed_rad=_vector_norm_m(
            tuple(ik_output_qpos_rad[index] - qpos_sample[index] for index in range(4))
        ),
        rejection_reason=None,
    )


def sample_fast_arm_viewer_endpoint_workspace(
    *,
    model_path: str | Path | None = None,
) -> tuple[FastArmViewerEndpointWorkspaceDiagnostic, ...]:
    """viewer projection元となるMuJoCo tip-site world position(m)だけをsampleする。"""

    simulator = _build_fast_arm_simulator(model_path)
    initial_state = simulator.snapshot()
    initial_qpos = tuple(initial_state.qpos[:4])
    initial_tip_position_m = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m
    diagnostics: list[FastArmViewerEndpointWorkspaceDiagnostic] = []

    for sample_label, qpos_sample in _fast_arm_viewer_workspace_qpos_samples(initial_qpos=initial_qpos):
        diagnostics.append(
            _build_fast_arm_viewer_workspace_diagnostic(
                sample_label=sample_label,
                sample_kind="qpos_sample",
                qpos_sample=qpos_sample,
                desired_world_endpoint_m=None,
                model_path=model_path,
            )
        )

    for sample_label, desired_world_endpoint_m in _fast_arm_viewer_workspace_endpoint_samples(
        initial_tip_position_m=initial_tip_position_m
    ):
        diagnostics.append(
            _build_fast_arm_viewer_workspace_diagnostic(
                sample_label=sample_label,
                sample_kind="desired_world_endpoint_sample",
                qpos_sample=initial_qpos,
                desired_world_endpoint_m=desired_world_endpoint_m,
                model_path=model_path,
            )
        )

    return tuple(diagnostics)


def _build_fast_arm_simulator(model_path: str | Path | None) -> HeadlessMuJoCoSimulator:
    return (
        build_fast_arm_simulator()
        if model_path is None
        else HeadlessMuJoCoSimulator.from_model_path(model_path)
    )


def _joint_name_axis_pairs(simulator: HeadlessMuJoCoSimulator) -> tuple[tuple[str, int, Vector3], ...]:
    mujoco = simulator._import_mujoco()
    pairs: list[tuple[str, int, Vector3]] = []
    for joint_name in inspect_mujoco_model(simulator.model).joint_names[:4]:
        joint_id = mujoco.mj_name2id(simulator.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        qpos_index = int(simulator.model.jnt_qposadr[joint_id])
        axis = tuple(float(component) for component in simulator.model.jnt_axis[joint_id])
        if len(axis) != 3:
            raise ValueError("mujoco_joint_axis must contain exactly three values")
        pairs.append((joint_name, qpos_index, axis))
    return tuple(pairs)


def _tip_for_qpos(
    *,
    model_path: str | Path | None,
    qpos_rad: Sequence[float],
) -> Vector3:
    simulator = _build_fast_arm_simulator(model_path)
    simulator.apply_qpos_command(JointCommand(joint_angles_rad=tuple(float(value) for value in qpos_rad[:4])))
    return extract_fast_arm_tip_site_endpoint_from_state(simulator.snapshot()).position_m


def _local_jacobian_pose_presets(
    initial_qpos: tuple[float, ...],
) -> tuple[tuple[str, tuple[float, ...]], ...]:
    q1_offset = list(initial_qpos)
    q1_offset[1] += 0.1
    q3_offset = list(initial_qpos)
    q3_offset[3] += 0.1
    q1_q3_offset = list(initial_qpos)
    q1_q3_offset[1] += 0.1
    q1_q3_offset[3] += 0.1
    return (
        ("initial", initial_qpos),
        ("q1_offset", tuple(q1_offset)),
        ("q3_offset", tuple(q3_offset)),
        ("q1_q3_offset", tuple(q1_q3_offset)),
    )


def _summarize_local_jacobian_columns(
    columns: Sequence[FastArmLocalJacobianColumn],
) -> dict[str, object]:
    effective_by_axis: dict[str, list[str]] = {"x": [], "y": [], "z": []}
    for column in columns:
        if column.dominant_axis in effective_by_axis and column.norm > _PERTURBATION_NO_MOVEMENT_EPSILON_M:
            label = f"{column.joint_name}:{'+' if column.dominant_sign > 0 else '-'}{column.dominant_axis}"
            effective_by_axis[column.dominant_axis].append(label)

    return {
        "x": tuple(effective_by_axis["x"]),
        "y": tuple(effective_by_axis["y"]),
        "z": tuple(effective_by_axis["z"]),
        "decision": (
            "the selected neutral pose has material local contributions on all world axes: "
            "sholder_joint_2/sholder_joint_3 on x, sholder_joint_1 on y, and elbow_joint on z; "
            "the older q1-only solver adapter remains a separate mechanism limitation"
        ),
    }


def run_fast_arm_local_jacobian_diagnostics(
    *,
    model_path: str | Path | None = None,
    perturbation_rad: float = _LOCAL_JACOBIAN_PERTURBATION_RAD,
) -> tuple[FastArmLocalJacobianPoseDiagnostics, ...]:
    """preset poseごとにcentral-difference Jacobian(m/rad)を測定する。"""

    if perturbation_rad <= 0.0:
        raise ValueError("perturbation_rad must be positive")

    base_simulator = _build_fast_arm_simulator(model_path)
    initial_qpos = tuple(base_simulator.snapshot().qpos[:4])
    joint_pairs = _joint_name_axis_pairs(base_simulator)
    diagnostics: list[FastArmLocalJacobianPoseDiagnostics] = []

    for pose_label, qpos in _local_jacobian_pose_presets(initial_qpos):
        tip_position_m = _tip_for_qpos(model_path=model_path, qpos_rad=qpos)
        columns: list[FastArmLocalJacobianColumn] = []
        for joint_name, qpos_index, joint_axis in joint_pairs:
            plus_qpos = list(qpos)
            minus_qpos = list(qpos)
            plus_qpos[qpos_index] += perturbation_rad
            minus_qpos[qpos_index] -= perturbation_rad
            plus_tip = _tip_for_qpos(model_path=model_path, qpos_rad=plus_qpos)
            minus_tip = _tip_for_qpos(model_path=model_path, qpos_rad=minus_qpos)
            plus_tip_delta_m = _vector_subtract(plus_tip, tip_position_m)
            minus_tip_delta_m = _vector_subtract(minus_tip, tip_position_m)
            central_difference_column = tuple(
                (plus_tip[index] - minus_tip[index]) / (2.0 * perturbation_rad)
                for index in range(3)
            )
            columns.append(
                FastArmLocalJacobianColumn(
                    pose_label=pose_label,
                    qpos=qpos,
                    qpos_index=qpos_index,
                    joint_name=joint_name,
                    joint_axis=joint_axis,
                    perturbation_rad=perturbation_rad,
                    plus_tip_delta_m=plus_tip_delta_m,
                    minus_tip_delta_m=minus_tip_delta_m,
                    central_difference_column=central_difference_column,
                    dominant_axis=_dominant_axis_label(central_difference_column),
                    dominant_sign=_dominant_axis_sign(central_difference_column),
                    norm=_vector_norm_m(central_difference_column),
                )
            )

        diagnostics.append(
            FastArmLocalJacobianPoseDiagnostics(
                pose_label=pose_label,
                qpos=qpos,
                tip_position_m=tip_position_m,
                perturbation_rad=perturbation_rad,
                jacobian_matrix=(
                    tuple(column.central_difference_column[0] for column in columns),  # type: ignore[assignment]
                    tuple(column.central_difference_column[1] for column in columns),  # type: ignore[assignment]
                    tuple(column.central_difference_column[2] for column in columns),  # type: ignore[assignment]
                ),
                columns=tuple(columns),
                joint_contribution_summary=_summarize_local_jacobian_columns(columns),
            )
        )

    return tuple(diagnostics)


def _build_command_frame(
    *,
    base_endpoint_m: Vector3,
    base_endpoint_source: str,
    command_delta_m: Vector3,
    command_label: str,
) -> RawInputFrame:
    desired_endpoint_m = tuple(
        base_endpoint_m[index] + command_delta_m[index]
        for index in range(3)
    )
    return RawInputFrame(
        source="replay",
        timestamp_s=0.0,
        metadata={
            "preset": "r7-e-p1-fast-arm-endpoint-motion-sanity",
            "command_label": command_label,
            "commanded_delta_m": command_delta_m,
            "desired_endpoint_m": desired_endpoint_m,
            "target_position_m": desired_endpoint_m,
            "base_endpoint_m": base_endpoint_m,
            "base_endpoint_source": base_endpoint_source,
        },
    )


def _initialization_frame(command_label: str) -> RawInputFrame:
    return RawInputFrame(
        source="replay",
        timestamp_s=0.0,
        metadata={
            "preset": "r7-e-p1-fast-arm-endpoint-motion-sanity-init",
            "command_label": command_label,
        },
    )


def _unavailable_result(
    *,
    axis: str,
    sign: int,
    command_label: str,
    command_delta_vector_m: Vector3,
    base_endpoint_m: Vector3 | None,
    base_endpoint_source: str,
    initial_tip_position_m: Vector3 | None,
    desired_endpoint_m: Vector3 | None,
    target_position_m: Vector3 | None,
    reason: str,
    qpos_before: tuple[float, ...] = (),
    error_message: str | None = None,
    qpos_perturbation_results: tuple[FastArmJointAxisPerturbationResult, ...] = (),
) -> FastArmEndpointMotionSanityResult:
    joint_axis_mapping_summary = _joint_axis_mapping_summary(qpos_perturbation_results)
    return FastArmEndpointMotionSanityResult(
        axis=axis,
        sign=sign,
        command_label=command_label,
        commanded_delta_m=command_delta_vector_m,
        base_endpoint_m=base_endpoint_m,
        base_endpoint_source=base_endpoint_source,
        initial_tip_position_m=initial_tip_position_m,
        desired_endpoint_m=desired_endpoint_m,
        target_position_m=target_position_m,
        final_tip_position_m=None,
        actual_delta_m=None,
        command_direction_m=_normalize_vector3(command_delta_vector_m) or command_delta_vector_m,
        actual_direction_m=None,
        direction_dot=None,
        direction_matches=None,
        status="unavailable",
        reason=reason,
        qpos_before=qpos_before,
        qpos_after=qpos_before,
        desired_endpoint_source=None,
        target_rejected=False,
        error_message=error_message,
        solver_input_endpoint_m=desired_endpoint_m,
        solver_seed_qpos=_UNAVAILABLE,
        solver_result_qpos=_UNAVAILABLE,
        reachable_workspace_summary=_workspace_summary(),
        distance_from_solver_base_m=(
            _UNAVAILABLE
            if desired_endpoint_m is None
            else _vector_norm_m(_vector_subtract(desired_endpoint_m, FAST_ARM_ENDPOINT_BASE_POSITION_M))
        ),
        target_constraints_summary=_target_constraints_summary(),
        frame_mapping_summary=_frame_mapping_summary(),
        diagnosis=reason,
        qpos_ref_summary=_qpos_ref_summary(),
        joint_axis_mapping_summary=joint_axis_mapping_summary,
        qpos_perturbation_results=qpos_perturbation_results,
        solver_to_mujoco_mapping=_solver_to_mujoco_mapping_summary(),
        mujoco_to_solver_mapping=_mujoco_to_solver_mapping_summary(),
        mapping_status=str(joint_axis_mapping_summary["mapping_status"]),
    )


def _classify_sanity_result(
    *,
    axis_index: int,
    command_delta_m: Vector3,
    actual_delta_m: Vector3 | None,
    target_rejected: bool,
    target_rejection_reason: str | None,
    target_rejection_message: str | None,
    error_message: str | None,
) -> tuple[str, str, bool | None, float | None]:
    if error_message is not None:
        return "unavailable", "backend_exception", None, None

    if target_rejected:
        return "rejected", target_rejection_reason or "target_rejected", False, None

    if actual_delta_m is None:
        return "unavailable", "missing_actual_tip_position", None, None

    actual_norm_m = _vector_norm_m(actual_delta_m)
    if actual_norm_m == 0.0:
        return "limitation", "no_movement", False, 0.0

    actual_direction_m = _normalize_vector3(actual_delta_m)
    if actual_direction_m is None:
        return "limitation", "no_movement", False, 0.0

    direction_dot = _dot_vector3(
        _normalize_vector3(command_delta_m) or command_delta_m,
        actual_direction_m,
    )
    dominant_axis_index = _dominant_axis_index(actual_delta_m)
    direction_matches = (
        dominant_axis_index == axis_index
        and math.copysign(1.0, actual_delta_m[axis_index]) == math.copysign(1.0, command_delta_m[axis_index])
    )

    if direction_matches:
        return "pass", "aligned", True, direction_dot

    if dominant_axis_index == axis_index:
        return "limitation", "opposite_direction", False, direction_dot

    return "limitation", "off_plane", False, direction_dot


def _build_endpoint_motion_command_for_world_target(
    *,
    world_target_m: Vector3,
    qpos_before: tuple[float, ...],
    solver_base_world_position_m: Vector3 | None,
    metadata: dict[str, object],
) -> tuple[
    MotionCommand,
    Vector3 | str,
    tuple[float, ...] | str,
    tuple[float, ...] | str,
    bool,
    str | None,
    str | None,
]:
    solver_seed_qpos = _mujoco_qpos_to_solver_joint_angles(qpos_before)
    if solver_base_world_position_m is None:
        reason = "solver_base_unavailable"
        return (
            MotionCommand(
                timestamp_s=0.0,
                joint=JointCommand(joint_angles_rad=qpos_before),
                metadata={
                    **metadata,
                    "target_rejected": True,
                    "target_rejection_reason": reason,
                    "target_rejection_message": f"missing MuJoCo body {_MUJOCO_SOLVER_BASE_BODY_NAME!r}",
                    "rejected_desired_endpoint_m": world_target_m,
                },
            ),
            _UNAVAILABLE,
            solver_seed_qpos,
            _UNAVAILABLE,
            True,
            reason,
            f"missing MuJoCo body {_MUJOCO_SOLVER_BASE_BODY_NAME!r}",
        )

    solver_local_target_m = _vector_subtract(world_target_m, solver_base_world_position_m)
    solver = FastArmEndpointInverseKinematicsSolver()
    try:
        solver_joint_command = solver.solve(
            solver_local_target_m,
            seed_joint_angles_rad=solver_seed_qpos if len(solver_seed_qpos) == 4 else None,
        )
    except ValueError as exc:
        message = str(exc)
        reason = (
            "target_unreachable"
            if message == "target_position_m is outside the reachable workspace"
            else "target_non_convergence"
        )
        return (
            MotionCommand(
                timestamp_s=0.0,
                joint=JointCommand(joint_angles_rad=qpos_before),
                metadata={
                    **metadata,
                    "target_rejected": True,
                    "target_rejection_reason": reason,
                    "target_rejection_message": message,
                    "rejected_desired_endpoint_m": world_target_m,
                    "solver_input_endpoint_m": solver_local_target_m,
                    "solver_seed_qpos": solver_seed_qpos,
                    "solver_base_world_position_m": solver_base_world_position_m,
                    "world_target_m": world_target_m,
                    "frame_transform_status": "world_minus_mujoco_base_link",
                    "qpos_ref_summary": _qpos_ref_summary(),
                },
            ),
            solver_local_target_m,
            solver_seed_qpos,
            _UNAVAILABLE,
            True,
            reason,
            message,
        )

    solver_result_qpos = tuple(solver_joint_command.joint_angles_rad[:4])
    mujoco_qpos_command = _solver_joint_angles_to_mujoco_qpos(
        solver_joint_command.joint_angles_rad,
        current_qpos_rad=qpos_before,
    )
    return (
        MotionCommand(
            timestamp_s=0.0,
            joint=JointCommand(joint_angles_rad=mujoco_qpos_command),
            metadata={
                **metadata,
                "solver_input_endpoint_m": solver_local_target_m,
                "solver_seed_qpos": solver_seed_qpos,
                "solver_result_qpos": solver_result_qpos,
                "solver_base_world_position_m": solver_base_world_position_m,
                "world_target_m": world_target_m,
                "frame_transform_status": "world_minus_mujoco_base_link",
                "qpos_ref_summary": _qpos_ref_summary(),
            },
        ),
        solver_local_target_m,
        solver_seed_qpos,
        solver_result_qpos,
        False,
        None,
        None,
    )


def _trajectory_decision(summary_status: str, command_label: str) -> str:
    if command_label in {"+x", "-x", "+y", "-y"}:
        return "current_solver_dof_allocation_limitation"
    if summary_status == "pass":
        return "short_range_aligned"
    if command_label in {"+z", "-z"}:
        return "z_primary_but_degrades_over_repeated_commands"
    return "current_solver_dof_allocation_limitation"


def _summarize_trajectory_records(
    *,
    command_label: str,
    command_delta_vector_m: Vector3,
    command_delta_m_per_step: float,
    records: Sequence[FastArmEndpointTrajectoryStepRecord],
) -> FastArmEndpointTrajectorySummary:
    direction_dots = [record.direction_dot for record in records if record.direction_dot is not None]
    cumulative_actual_delta_m = records[-1].cumulative_actual_delta_m if records else (0.0, 0.0, 0.0)
    command_axis = _dominant_axis_index(command_delta_vector_m)
    signed_actual_along_axis_m = cumulative_actual_delta_m[command_axis]
    commanded_axis_delta_m = command_delta_vector_m[command_axis] * len(records)
    drift_from_command_axis_m = signed_actual_along_axis_m - commanded_axis_delta_m
    orthogonal_components = [
        cumulative_actual_delta_m[index]
        for index in range(3)
        if index != command_axis
    ]
    orthogonal_drift_m = math.sqrt(sum(component * component for component in orthogonal_components))

    saturation_step = next(
        (
            record.step_index
            for record in records
            if _vector_norm_m(record.actual_delta_m) <= _TRAJECTORY_MOVEMENT_EPSILON_M
        ),
        None,
    )
    first_rejection_step = next((record.step_index for record in records if record.target_rejected), None)
    first_off_plane_step = next((record.step_index for record in records if record.reason == "off_plane"), None)
    first_opposite_direction_step = next(
        (record.step_index for record in records if record.reason == "opposite_direction"),
        None,
    )
    safe_hold_step = next(
        (
            record.step_index
            for record in records
            if record.target_rejected or record.reason in {"no_movement", "safe_hold"}
        ),
        None,
    )
    final_status = records[-1].status if records else "unavailable"
    final_reason = records[-1].reason if records else "no_records"
    return FastArmEndpointTrajectorySummary(
        command_label=command_label,
        step_count=len(records),
        command_delta_m_per_step=command_delta_m_per_step,
        initial_alignment=records[0].reason if records else "no_records",
        final_alignment=final_reason,
        best_alignment=max(direction_dots) if direction_dots else None,
        worst_alignment=min(direction_dots) if direction_dots else None,
        mean_direction_dot=(sum(direction_dots) / len(direction_dots)) if direction_dots else None,
        drift_from_command_axis_m=drift_from_command_axis_m,
        orthogonal_drift_m=orthogonal_drift_m,
        cumulative_commanded_delta_m=tuple(component * len(records) for component in command_delta_vector_m),
        cumulative_actual_delta_m=cumulative_actual_delta_m,
        saturation_step=saturation_step,
        first_rejection_step=first_rejection_step,
        first_off_plane_step=first_off_plane_step,
        first_opposite_direction_step=first_opposite_direction_step,
        safe_hold_step=safe_hold_step,
        final_status=final_status,
        final_reason=final_reason,
        decision=_trajectory_decision(final_status, command_label),
    )


def _run_fast_arm_endpoint_trajectory_case(
    *,
    axis: str,
    sign: int,
    trajectory_steps: int,
    trajectory_delta_m: float,
    config: RuntimeConfig,
    model_path: str | Path | None,
    seed_joint_angles_rad: tuple[float, ...] | None,
) -> FastArmEndpointTrajectoryDiagnostics:
    command_label = _axis_label(axis, sign)
    command_delta_vector_m = _axis_delta(axis, sign, trajectory_delta_m)
    command_direction_m = _normalize_vector3(command_delta_vector_m) or command_delta_vector_m
    simulator = _build_fast_arm_simulator(model_path)
    if seed_joint_angles_rad is not None:
        simulator.apply_qpos_command(JointCommand(joint_angles_rad=seed_joint_angles_rad))

    initial_state = simulator.snapshot()
    initial_tip_position_m = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m
    initial_qpos = tuple(initial_state.qpos[:4])
    solver_base_world_position_m = _body_position_from_state(initial_state, _MUJOCO_SOLVER_BASE_BODY_NAME)
    desired_endpoint_m = initial_tip_position_m
    cumulative_actual_delta_m: Vector3 = (0.0, 0.0, 0.0)
    records: list[FastArmEndpointTrajectoryStepRecord] = []

    for step_index in range(1, trajectory_steps + 1):
        before_state = simulator.snapshot()
        tip_before_m = extract_fast_arm_tip_site_endpoint_from_state(before_state).position_m
        qpos_before = tuple(before_state.qpos[:4])
        desired_endpoint_m = _vector_add(desired_endpoint_m, command_delta_vector_m)
        command, solver_local_target_m, _, _, target_rejected, target_rejection_reason, target_rejection_message = (
            _build_endpoint_motion_command_for_world_target(
                world_target_m=desired_endpoint_m,
                qpos_before=qpos_before,
                solver_base_world_position_m=solver_base_world_position_m,
                metadata={
                    "preset": "r7-e-p1-fast-arm-endpoint-trajectory-diagnostics",
                    "command_label": command_label,
                    "step_index": step_index,
                    "desired_endpoint_m": desired_endpoint_m,
                    "target_position_m": desired_endpoint_m,
                    "commanded_delta_m": command_delta_vector_m,
                },
            )
        )
        simulator.apply_joint_position_command(project_joint_position_command(command))
        simulator.record_motion_command_envelope(command)
        error_message: str | None = None
        try:
            simulator.step(config.dt_s)
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
        after_state = simulator.snapshot()
        tip_after_m = extract_fast_arm_tip_site_endpoint_from_state(after_state).position_m
        qpos_after = tuple(after_state.qpos[:4])
        actual_delta_m = _vector_subtract(tip_after_m, tip_before_m)
        cumulative_actual_delta_m = _vector_add(cumulative_actual_delta_m, actual_delta_m)
        status, reason, _, direction_dot = _classify_sanity_result(
            axis_index=_axis_index(axis),
            command_delta_m=command_delta_vector_m,
            actual_delta_m=actual_delta_m,
            target_rejected=target_rejected,
            target_rejection_reason=target_rejection_reason,
            target_rejection_message=target_rejection_message,
            error_message=error_message,
        )
        if direction_dot is not None and direction_dot < _TRAJECTORY_DIRECTION_DOT_THRESHOLD and reason == "aligned":
            status = "limitation"
            reason = "alignment_degraded"
        records.append(
            FastArmEndpointTrajectoryStepRecord(
                command_label=command_label,
                step_index=step_index,
                desired_endpoint_m=desired_endpoint_m,
                solver_local_target_m=solver_local_target_m,
                qpos_before=qpos_before,
                qpos_after=qpos_after,
                tip_before_m=tip_before_m,
                tip_after_m=tip_after_m,
                actual_delta_m=actual_delta_m,
                cumulative_actual_delta_m=cumulative_actual_delta_m,
                command_direction_m=command_direction_m,
                direction_dot=direction_dot,
                dominant_axis=_dominant_axis_label(actual_delta_m),
                status=status,
                reason=reason,
                target_rejected=target_rejected,
                target_rejection_reason=target_rejection_reason,
                distance_from_solver_base_m=(
                    _vector_norm_m(solver_local_target_m)
                    if isinstance(solver_local_target_m, tuple)
                    else _UNAVAILABLE
                ),
            )
        )

    summary = _summarize_trajectory_records(
        command_label=command_label,
        command_delta_vector_m=command_delta_vector_m,
        command_delta_m_per_step=trajectory_delta_m,
        records=records,
    )
    return FastArmEndpointTrajectoryDiagnostics(
        command_label=command_label,
        axis=axis,
        sign=sign,
        command_delta_m_per_step=trajectory_delta_m,
        step_count=trajectory_steps,
        initial_tip_position_m=initial_tip_position_m,
        initial_qpos=initial_qpos,
        records=tuple(records),
        summary=summary,
    )


def run_fast_arm_endpoint_trajectory_diagnostics(
    *,
    trajectory_steps: int = 30,
    trajectory_delta_m: float = 0.005,
    config: RuntimeConfig | None = None,
    model_path: str | Path | None = None,
    seed_joint_angles_rad: tuple[float, ...] | None = None,
) -> tuple[FastArmEndpointTrajectoryDiagnostics, ...]:
    """software runtimeでaxis別trajectoryを実行し、reject/hold/errorを層別記録する。"""

    if trajectory_steps <= 0:
        raise ValueError("trajectory_steps must be positive")
    if trajectory_delta_m <= 0.0:
        raise ValueError("trajectory_delta_m must be positive")

    runtime_config = RuntimeConfig(robot_profile_id="fast_arm") if config is None else config
    return tuple(
        _run_fast_arm_endpoint_trajectory_case(
            axis=axis,
            sign=sign,
            trajectory_steps=trajectory_steps,
            trajectory_delta_m=trajectory_delta_m,
            config=runtime_config,
            model_path=model_path,
            seed_joint_angles_rad=seed_joint_angles_rad,
        )
        for axis, sign, _ in _COMMAND_AXES
    )


async def _run_fast_arm_endpoint_motion_sanity_case_async(
    *,
    axis: str,
    sign: int,
    command_delta_m: float,
    base_desired_endpoint_m: Vector3 | None,
    config: RuntimeConfig,
    model_path: str | Path | None,
    seed_joint_angles_rad: tuple[float, ...] | None,
    qpos_perturbation_results: tuple[FastArmJointAxisPerturbationResult, ...],
) -> FastArmEndpointMotionSanityResult:
    command_delta_vector_m = _axis_delta(axis, sign, command_delta_m)
    command_label = _axis_label(axis, sign)
    joint_axis_mapping_summary = _joint_axis_mapping_summary(qpos_perturbation_results)
    try:
        pipeline = build_concrete_mujoco_pipeline(
            frames=(_initialization_frame(command_label),),
            config=config,
            model_path=model_path,
            publisher=_DiagnosticStatePublisher(),
            seed_joint_angles_rad=seed_joint_angles_rad,
        )
    except Exception as exc:  # noqa: BLE001
        return _unavailable_result(
            axis=axis,
            sign=sign,
            command_label=command_label,
            command_delta_vector_m=command_delta_vector_m,
            base_endpoint_m=None,
            base_endpoint_source=_BASE_ENDPOINT_SOURCE_UNAVAILABLE,
            initial_tip_position_m=None,
            desired_endpoint_m=None,
            target_position_m=None,
            reason="backend_exception",
            error_message=str(exc),
            qpos_perturbation_results=qpos_perturbation_results,
        )

    initial_state = pipeline.simulator.snapshot()
    initial_tip_position_m: Vector3 | None = None
    try:
        initial_tip_position_m = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m
    except ValueError:
        initial_tip_position_m = None

    qpos_before = tuple(initial_state.qpos[:4])
    solver_seed_qpos = _mujoco_qpos_to_solver_joint_angles(qpos_before)
    mujoco_base_link_position_m = _body_position_from_state(initial_state, _MUJOCO_SOLVER_BASE_BODY_NAME)
    if base_desired_endpoint_m is None:
        if initial_tip_position_m is None:
            return _unavailable_result(
                axis=axis,
                sign=sign,
                command_label=command_label,
                command_delta_vector_m=command_delta_vector_m,
                base_endpoint_m=None,
                base_endpoint_source=_BASE_ENDPOINT_SOURCE_UNAVAILABLE,
                initial_tip_position_m=None,
                desired_endpoint_m=None,
                target_position_m=None,
                reason="missing_initial_tip_position",
                qpos_before=qpos_before,
                qpos_perturbation_results=qpos_perturbation_results,
            )
        base_endpoint_m = initial_tip_position_m
        base_endpoint_source = _BASE_ENDPOINT_SOURCE_INITIAL_TIP
    else:
        base_endpoint_m = base_desired_endpoint_m
        base_endpoint_source = _BASE_ENDPOINT_SOURCE_EXPLICIT

    frame = _build_command_frame(
        base_endpoint_m=base_endpoint_m,
        base_endpoint_source=base_endpoint_source,
        command_delta_m=command_delta_vector_m,
        command_label=command_label,
    )
    pipeline.input_source = ReplayInputSource((frame,))
    solver_base_world_position_m = mujoco_base_link_position_m
    world_target_m = frame.metadata["desired_endpoint_m"]  # type: ignore[assignment]
    solver_local_target_m: Vector3 | str = _UNAVAILABLE
    solver_result_qpos: tuple[float, ...] | str = _UNAVAILABLE
    solver_fk_endpoint_m: Vector3 | str = _UNAVAILABLE
    transformed_solver_fk_world_m: Vector3 | str = _UNAVAILABLE
    frame_transform_status = "unavailable"
    target_rejected = False
    target_rejection_reason = None
    target_rejection_message = None
    rejected_desired_endpoint_m: Vector3 | str = _UNAVAILABLE
    error_message: str | None = None

    if solver_base_world_position_m is None:
        command = MotionCommand(
            timestamp_s=0.0,
            joint=JointCommand(joint_angles_rad=qpos_before),
            metadata={
                **frame.metadata,
                "target_rejected": True,
                "target_rejection_reason": "solver_base_unavailable",
                "target_rejection_message": f"missing MuJoCo body {_MUJOCO_SOLVER_BASE_BODY_NAME!r}",
                "rejected_desired_endpoint_m": world_target_m,
            },
        )
        target_rejected = True
        target_rejection_reason = "solver_base_unavailable"
        target_rejection_message = f"missing MuJoCo body {_MUJOCO_SOLVER_BASE_BODY_NAME!r}"
        rejected_desired_endpoint_m = world_target_m
    else:
        solver_local_target_m = _vector_subtract(world_target_m, solver_base_world_position_m)
        frame_transform_status = "world_minus_mujoco_base_link"
        solver = FastArmEndpointInverseKinematicsSolver()
        fk_solver = FastArmEndpointForwardKinematicsSolver()
        try:
            solver_joint_command = solver.solve(
                solver_local_target_m,
                seed_joint_angles_rad=solver_seed_qpos if len(solver_seed_qpos) == 4 else None,
            )
            solver_result_qpos = tuple(solver_joint_command.joint_angles_rad[:4])
            solver_fk_endpoint_m = fk_solver.forward(solver_joint_command.joint_angles_rad[:4])
            transformed_solver_fk_world_m = _vector_add(solver_fk_endpoint_m, solver_base_world_position_m)
            mujoco_qpos_command = _solver_joint_angles_to_mujoco_qpos(
                solver_joint_command.joint_angles_rad,
                current_qpos_rad=qpos_before,
            )
            command = MotionCommand(
                timestamp_s=0.0,
                joint=JointCommand(joint_angles_rad=mujoco_qpos_command),
                metadata={
                    **frame.metadata,
                    "solver_input_endpoint_m": solver_local_target_m,
                    "solver_seed_qpos": solver_seed_qpos,
                    "solver_result_qpos": solver_result_qpos,
                    "solver_base_world_position_m": solver_base_world_position_m,
                    "world_target_m": world_target_m,
                    "frame_transform_status": frame_transform_status,
                    "qpos_ref_summary": _qpos_ref_summary(),
                },
            )
        except ValueError as exc:
            target_rejected = True
            target_rejection_message = str(exc)
            target_rejection_reason = (
                "target_unreachable"
                if target_rejection_message == "target_position_m is outside the reachable workspace"
                else "target_non_convergence"
            )
            rejected_desired_endpoint_m = world_target_m
            command = MotionCommand(
                timestamp_s=0.0,
                joint=JointCommand(joint_angles_rad=qpos_before),
                metadata={
                    **frame.metadata,
                    "target_rejected": True,
                    "target_rejection_reason": target_rejection_reason,
                    "target_rejection_message": target_rejection_message,
                    "rejected_desired_endpoint_m": rejected_desired_endpoint_m,
                    "solver_input_endpoint_m": solver_local_target_m,
                    "solver_seed_qpos": solver_seed_qpos,
                    "solver_base_world_position_m": solver_base_world_position_m,
                    "world_target_m": world_target_m,
                    "frame_transform_status": frame_transform_status,
                    "qpos_ref_summary": _qpos_ref_summary(),
                },
            )

    pipeline.simulator.apply_joint_position_command(project_joint_position_command(command))
    pipeline.simulator.record_motion_command_envelope(command)
    final_state: MuJoCoState | None = None
    try:
        pipeline.simulator.step(config.dt_s)
        final_state = pipeline.simulator.snapshot()
    except Exception as exc:  # noqa: BLE001
        error_message = str(exc)

    if final_state is None:
        return _unavailable_result(
            axis=axis,
            sign=sign,
            command_label=command_label,
            command_delta_vector_m=command_delta_vector_m,
            base_endpoint_m=base_endpoint_m,
            base_endpoint_source=base_endpoint_source,
            initial_tip_position_m=initial_tip_position_m,
            desired_endpoint_m=frame.metadata["desired_endpoint_m"],  # type: ignore[assignment]
            target_position_m=frame.metadata["target_position_m"],  # type: ignore[assignment]
            reason="backend_exception",
            qpos_before=qpos_before,
            error_message=error_message,
            qpos_perturbation_results=qpos_perturbation_results,
        )

    final_tip_position_m: Vector3 | None = None
    try:
        final_tip_position_m = extract_fast_arm_tip_site_endpoint_from_state(final_state).position_m
    except ValueError:
        final_tip_position_m = None

    qpos_after = tuple(final_state.qpos[:4])
    actual_delta_m: Vector3 | None = None
    if initial_tip_position_m is not None and final_tip_position_m is not None:
        actual_delta_m = tuple(
            final_tip_position_m[index] - initial_tip_position_m[index]
            for index in range(3)
        )

    last_command = pipeline.simulator.last_command
    desired_endpoint_source = None
    if last_command is not None:
        try:
            resolved_desired_endpoint = resolve_desired_endpoint_from_motion_command(last_command)
            desired_endpoint_source = resolved_desired_endpoint.source
        except ValueError:
            desired_endpoint_source = None
        target_rejected = bool(last_command.metadata.get("target_rejected", target_rejected))
        if target_rejected:
            target_rejection_reason = str(last_command.metadata.get("target_rejection_reason", "target_rejected"))
            target_rejection_message = str(last_command.metadata.get("target_rejection_message", target_rejection_reason))
            rejected_desired_endpoint = last_command.metadata.get("rejected_desired_endpoint_m")
            if rejected_desired_endpoint is not None:
                rejected_desired_endpoint_m = _coerce_vector3(
                    "rejected_desired_endpoint_m",
                    rejected_desired_endpoint,
                )

    status, reason, direction_matches, direction_dot = _classify_sanity_result(
        axis_index=("xyz".index(axis)),
        command_delta_m=command_delta_vector_m,
        actual_delta_m=actual_delta_m,
        target_rejected=target_rejected,
        target_rejection_reason=target_rejection_reason,
        target_rejection_message=target_rejection_message,
        error_message=error_message,
    )
    solver_input_endpoint_m = solver_local_target_m if isinstance(solver_local_target_m, tuple) else None
    distance_from_solver_base_m = (
        _vector_norm_m(solver_local_target_m)
        if isinstance(solver_local_target_m, tuple)
        else _UNAVAILABLE
    )
    diagnosis = _diagnose_case(
        distance_from_solver_base_m=distance_from_solver_base_m,
        target_rejected=target_rejected,
        target_rejection_reason=target_rejection_reason,
        initial_tip_position_m=initial_tip_position_m,
        qpos_before=qpos_before,
        solver_seed_qpos=solver_seed_qpos,
    )
    if not target_rejected and isinstance(actual_delta_m, tuple):
        diagnosis = "world_target_transformed_to_mujoco_base_link_solver_frame"

    return FastArmEndpointMotionSanityResult(
        axis=axis,
        sign=sign,
        command_label=command_label,
        commanded_delta_m=command_delta_vector_m,
        base_endpoint_m=base_endpoint_m,
        base_endpoint_source=base_endpoint_source,
        initial_tip_position_m=initial_tip_position_m,
        desired_endpoint_m=frame.metadata["desired_endpoint_m"],  # type: ignore[assignment]
        target_position_m=frame.metadata["target_position_m"],  # type: ignore[assignment]
        final_tip_position_m=final_tip_position_m,
        actual_delta_m=actual_delta_m,
        command_direction_m=_normalize_vector3(command_delta_vector_m) or command_delta_vector_m,
        actual_direction_m=_normalize_vector3(actual_delta_m) if actual_delta_m is not None else None,
        direction_dot=direction_dot,
        direction_matches=direction_matches,
        status=status,
        reason=reason,
        qpos_before=qpos_before,
        qpos_after=qpos_after,
        desired_endpoint_source=desired_endpoint_source,
        target_rejected=target_rejected,
        target_rejection_reason=target_rejection_reason,
        target_rejection_message=target_rejection_message,
        error_message=error_message,
        solver_input_endpoint_m=solver_input_endpoint_m,
        solver_seed_qpos=solver_seed_qpos,
        solver_result_qpos=solver_result_qpos,
        reachable_workspace_summary=_workspace_summary(),
        distance_from_solver_base_m=distance_from_solver_base_m,
        target_constraints_summary=_target_constraints_summary(),
        frame_mapping_summary=_frame_mapping_summary(),
        diagnosis=diagnosis,
        rejected_desired_endpoint_m=rejected_desired_endpoint_m,
        last_valid_target_position_m=_UNAVAILABLE,
        mujoco_base_link_position_m=mujoco_base_link_position_m or _UNAVAILABLE,
        mujoco_base_link_frame="MuJoCo world / scene frame",
        mujoco_tip_position_m=initial_tip_position_m or _UNAVAILABLE,
        tip_relative_to_base_link_m=(
            _vector_subtract(initial_tip_position_m, mujoco_base_link_position_m)
            if initial_tip_position_m is not None and mujoco_base_link_position_m is not None
            else _UNAVAILABLE
        ),
        tip_relative_to_solver_base_m=(
            _vector_subtract(initial_tip_position_m, solver_base_world_position_m)
            if initial_tip_position_m is not None and solver_base_world_position_m is not None
            else _UNAVAILABLE
        ),
        solver_base_world_position_m=solver_base_world_position_m or _UNAVAILABLE,
        solver_local_target_m=solver_local_target_m,
        world_target_m=world_target_m,
        frame_transform_status=frame_transform_status,
        qpos_ref_summary=_qpos_ref_summary(),
        solver_fk_endpoint_m=solver_fk_endpoint_m,
        transformed_solver_fk_world_m=transformed_solver_fk_world_m,
        joint_axis_mapping_summary=joint_axis_mapping_summary,
        qpos_perturbation_results=qpos_perturbation_results,
        solver_to_mujoco_mapping=_solver_to_mujoco_mapping_summary(),
        mujoco_to_solver_mapping=_mujoco_to_solver_mapping_summary(),
        mapping_status=str(joint_axis_mapping_summary["mapping_status"]),
    )


async def _run_fast_arm_endpoint_motion_sanity_async(
    *,
    base_desired_endpoint_m: Vector3 | None,
    command_delta_m: float,
    config: RuntimeConfig,
    model_path: str | Path | None,
    seed_joint_angles_rad: tuple[float, ...] | None,
) -> tuple[FastArmEndpointMotionSanityResult, ...]:
    results: list[FastArmEndpointMotionSanityResult] = []
    try:
        qpos_perturbation_results = run_fast_arm_joint_axis_mapping_diagnostics(model_path=model_path)
    except Exception:  # noqa: BLE001
        qpos_perturbation_results = ()
    for axis, sign, _ in _COMMAND_AXES:
        result = await _run_fast_arm_endpoint_motion_sanity_case_async(
            axis=axis,
            sign=sign,
            command_delta_m=command_delta_m,
            base_desired_endpoint_m=base_desired_endpoint_m,
            config=config,
            model_path=model_path,
            seed_joint_angles_rad=seed_joint_angles_rad,
            qpos_perturbation_results=qpos_perturbation_results,
        )
        results.append(result)

    return tuple(results)


def run_fast_arm_endpoint_motion_sanity(
    *,
    base_desired_endpoint_m: Sequence[float] | None = None,
    command_delta_m: float = _DEFAULT_COMMAND_DELTA_M,
    config: RuntimeConfig | None = None,
    model_path: str | Path | None = None,
    seed_joint_angles_rad: tuple[float, ...] | None = None,
) -> tuple[FastArmEndpointMotionSanityResult, ...]:
    """±XYZのworld target commandをsoftware-onlyで評価する同期entry point。

    ``asyncio.run`` を所有するため既存event loop内からは呼ばない。serial/OSC/robot output
    は行わず、command、solver、MuJoCo measurementのfailure timingを別々に保持する。
    """

    if command_delta_m <= 0.0:
        raise ValueError("command_delta_m must be positive")

    runtime_config = RuntimeConfig(robot_profile_id="fast_arm") if config is None else config
    explicit_base_desired_endpoint_m = (
        None
        if base_desired_endpoint_m is None
        else _coerce_vector3("base_desired_endpoint_m", base_desired_endpoint_m)
    )
    return asyncio.run(
        _run_fast_arm_endpoint_motion_sanity_async(
            base_desired_endpoint_m=explicit_base_desired_endpoint_m,
            command_delta_m=command_delta_m,
            config=runtime_config,
            model_path=model_path,
            seed_joint_angles_rad=seed_joint_angles_rad,
        )
    )


__all__ = [
    "FastArmEndpointTrajectoryDiagnostics",
    "FastArmEndpointTrajectoryStepRecord",
    "FastArmEndpointTrajectorySummary",
    "FastArmFkSiteConsistencyDiagnostic",
    "FastArmEndpointDiagnosticRecord",
    "FastArmIkFkSanityDiagnostic",
    "FastArmViewerEndpointWorkspaceDiagnostic",
    "FastArmLocalJacobianColumn",
    "FastArmLocalJacobianPoseDiagnostics",
    "FastArmJointAxisPerturbationResult",
    "FastArmEndpointMotionSanityResult",
    "run_fast_arm_fk_site_consistency_diagnostics",
    "run_fast_arm_ik_fk_sanity_diagnostics",
    "sample_fast_arm_viewer_endpoint_workspace",
    "run_fast_arm_endpoint_trajectory_diagnostics",
    "run_fast_arm_local_jacobian_diagnostics",
    "run_fast_arm_joint_axis_mapping_diagnostics",
    "run_fast_arm_endpoint_motion_sanity",
    "build_fast_arm_fk_site_consistency_log_rows",
    "build_fast_arm_ik_fk_sanity_log_rows",
    "build_fast_arm_endpoint_diagnostic_log_rows",
    "write_fast_arm_fk_site_consistency_log_jsonl",
    "write_fast_arm_ik_fk_sanity_log_jsonl",
    "write_fast_arm_endpoint_diagnostic_log_csv",
    "write_fast_arm_endpoint_diagnostic_log_jsonl",
    "build_fast_arm_endpoint_trajectory_log_rows",
    "write_fast_arm_endpoint_trajectory_log_csv",
    "write_fast_arm_endpoint_trajectory_log_jsonl",
]
