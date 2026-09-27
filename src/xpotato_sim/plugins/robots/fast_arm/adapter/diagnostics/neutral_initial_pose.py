"""Deterministic selection diagnostics for the fast_arm neutral startup pose."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np

from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import (
    FastArmMuJoCoModelForwardKinematicsSolver,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.model_contract import FAST_ARM_ARM_BODY_NAMES
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator
from xpotato_sim.runtime.evaluation.endpoint_progress import calculate_endpoint_progress
from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.jacobian_mobility import (
    CONTROLLED_JOINT_NAMES,
    PoseDiagnostic,
    evaluate_fast_arm_pose_mobility,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.initial_state import (
    FAST_ARM_INITIAL_STATE_CONTRACT,
    FAST_ARM_INITIAL_STATE_QPOS_RAD,
    FAST_ARM_INITIAL_STATE_TIP_POSITION_M,
    FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ,
)

SCHEMA_VERSION = "r7-e-p22-v2"
HISTORICAL_RAISED_BASELINE_QPOS_RAD = (0.0, -math.pi / 2.0, 0.0, 0.0)
# Selection contract frozen before candidate evaluation. Fractions are derived
# from the model's nominal shoulder-to-tip reach, rather than from candidate
# ranking output.
MINIMUM_HEIGHT_REDUCTION_REACH_FRACTION = 0.10
MINIMUM_EXTENSION_REDUCTION_REACH_FRACTION = 0.10
MINIMUM_FLOOR_CLEARANCE_REACH_FRACTION = 0.05
MINIMUM_LIMITED_JOINT_MARGIN = 0.10
FK_SITE_TOLERANCE_M = 1e-9
PENETRATION_TOLERANCE_M = 1e-9
MAXIMUM_PER_JOINT_BASELINE_DELTA_RAD = math.pi
NEUTRAL_HEIGHT_DROP_REACH_FRACTION = 0.50
NEUTRAL_EXTENSION_RATIO = 0.75
NEARBY_PERTURBATION_RAD = math.pi / 90.0


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    candidate_id: str
    qpos_rad: tuple[float, ...]
    category: str
    baseline: bool = False


@dataclass(frozen=True, slots=True)
class DirectionResult:
    label: str
    requested_delta_m: tuple[float, ...]
    predicted_delta_m: tuple[float, ...]
    measured_delta_m: tuple[float, ...]
    progress_ratio: float | None
    direction_cosine: float | None
    progress_status: str


@dataclass(frozen=True, slots=True)
class NearbySensitivity:
    perturbation_rad: float
    evaluated_count: int
    minimum_effective_rank: int
    maximum_tip_shift_m: float
    maximum_row_norm_relative_change: float


@dataclass(frozen=True, slots=True)
class CollisionEvidence:
    collision_check_available: bool
    collision_check_reason: str
    contact_count: int
    penetration_count: int
    minimum_contact_distance_m: float | None


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_id: str
    category: str
    baseline: bool
    qpos_rad: tuple[float, ...]
    tip_world_position_m: tuple[float, float, float]
    tip_height_m: float
    shoulder_to_tip_extension_m: float
    extension_ratio: float
    per_joint_normalized_limit_margin: tuple[float | None, ...]
    minimum_limited_joint_margin: float | None
    collision_check_available: bool
    collision_check_reason: str
    contact_count: int
    penetration_count: int
    minimum_contact_distance_m: float | None
    tip_floor_clearance_m: float
    fk_site_residual_m: float
    jacobian_numeric_rank: int
    jacobian_effective_rank: int
    singular_values: tuple[float, ...]
    minimum_useful_singular_value: float
    row_norms_xyz: tuple[float, ...]
    row_norm_balance: float
    translational_manipulability: float
    directions: tuple[DirectionResult, ...]
    nearby_sensitivity: NearbySensitivity
    baseline_qpos_delta_norm_rad: float
    hard_constraint_failures: tuple[str, ...]
    ranking_key: tuple[object, ...]

    @property
    def eligible(self) -> bool:
        return not self.baseline and not self.hard_constraint_failures


@dataclass(frozen=True, slots=True)
class NeutralPoseEvaluation:
    schema_version: str
    model_identity: str
    selection_contract: dict[str, object]
    nominal_reach_m: float
    shoulder_height_m: float
    candidate_count: int
    eligible_count: int
    rejection_counts: dict[str, int]
    selected_candidate_id: str | None
    candidates: tuple[CandidateEvaluation, ...]

    def to_dict(self) -> dict[str, object]:
        def sanitize(value: object) -> object:
            if isinstance(value, float) and math.isinf(value):
                return "Infinity" if value > 0.0 else "-Infinity"
            if isinstance(value, dict):
                return {str(key): sanitize(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [sanitize(item) for item in value]
            return value

        return sanitize(asdict(self))  # type: ignore[return-value]

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True)


def validate_candidate_qpos(value: object, *, expected_length: int) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("candidate qpos must be a numeric sequence")
    if len(value) != expected_length:
        raise ValueError(
            f"candidate qpos length mismatch: expected {expected_length}, got {len(value)}"
        )
    qpos: list[float] = []
    for index, component in enumerate(value):
        if isinstance(component, bool):
            raise ValueError(f"candidate qpos bool is not numeric at index {index}")
        try:
            number = float(component)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"candidate qpos is not numeric at index {index}") from exc
        if not math.isfinite(number):
            raise ValueError(f"candidate qpos must be finite at index {index}")
        qpos.append(number)
    return tuple(qpos)


def _joint_metadata(simulator: HeadlessMuJoCoSimulator) -> tuple[dict[str, object], ...]:
    mujoco = simulator._import_mujoco()
    metadata: list[dict[str, object]] = []
    for joint_name in CONTROLLED_JOINT_NAMES:
        joint_id = int(
            mujoco.mj_name2id(
                simulator.model,
                mujoco.mjtObj.mjOBJ_JOINT,
                joint_name,
            )
        )
        if joint_id < 0:
            raise ValueError(f"controlled joint is missing: {joint_name}")
        metadata.append(
            {
                "name": joint_name,
                "joint_id": joint_id,
                "qpos_address": int(simulator.model.jnt_qposadr[joint_id]),
                "limited": bool(simulator.model.jnt_limited[joint_id]),
                "range": tuple(float(value) for value in simulator.model.jnt_range[joint_id]),
            }
        )
    return tuple(metadata)


def generate_fast_arm_neutral_pose_candidates(
    simulator: HeadlessMuJoCoSimulator,
) -> tuple[CandidateSpec, ...]:
    """Build the fixed lower/bent grid from hinge metadata and turn fractions."""

    joint_metadata = _joint_metadata(simulator)
    if len(joint_metadata) != 4:
        raise ValueError("fast_arm candidate generation requires exactly four controlled joints")
    baseline = validate_candidate_qpos(
        HISTORICAL_RAISED_BASELINE_QPOS_RAD,
        expected_length=int(simulator.model.nq),
    )
    shoulder_fractions = (1.0 / 3.0, 0.5, 2.0 / 3.0, 5.0 / 6.0, 1.0)
    elbow_turn_fractions = (1.0 / 6.0, 1.0 / 4.0, 1.0 / 3.0, 5.0 / 12.0, 0.5)
    candidates: list[CandidateSpec] = [
        CandidateSpec("historical_raised_baseline", baseline, "baseline", baseline=True)
    ]
    seen = {baseline}

    def add(candidate_id: str, qpos: Sequence[float], category: str) -> None:
        validated = validate_candidate_qpos(qpos, expected_length=len(baseline))
        for component, joint in zip(validated, joint_metadata, strict=True):
            if joint["limited"]:
                lower, upper = joint["range"]  # type: ignore[misc]
                if component < lower or component > upper:
                    raise ValueError(f"generated candidate violates {joint['name']} range")
        if validated in seen:
            raise ValueError(f"generated candidate duplicates an existing qpos: {candidate_id}")
        seen.add(validated)
        candidates.append(CandidateSpec(candidate_id, validated, category))

    for shoulder_fraction in shoulder_fractions:
        shoulder = baseline[1] + shoulder_fraction * (0.0 - baseline[1])
        fraction_label = str(int(round(shoulder_fraction * 12.0)))
        add(
            f"shoulder_lower_{fraction_label}_twelfths",
            (0.0, shoulder, 0.0, 0.0),
            "shoulder_lowered",
        )
        for elbow_fraction in elbow_turn_fractions:
            elbow_magnitude = math.tau * elbow_fraction
            elbow_label = str(int(round(elbow_fraction * 12.0)))
            for sign, sign_label in ((-1.0, "negative"), (1.0, "positive")):
                qpos = (0.0, shoulder, 0.0, sign * elbow_magnitude)
                add(
                    f"combined_s{fraction_label}_e{sign_label}_{elbow_label}",
                    qpos,
                    "shoulder_elbow_combined",
                )

    for elbow_fraction in elbow_turn_fractions:
        elbow_magnitude = math.tau * elbow_fraction
        elbow_label = str(int(round(elbow_fraction * 12.0)))
        for sign, sign_label in ((-1.0, "negative"), (1.0, "positive")):
            add(
                f"elbow_bent_{sign_label}_{elbow_label}_twelfths",
                (0.0, baseline[1], 0.0, sign * elbow_magnitude),
                "elbow_bent",
            )

    symmetric_offset = math.tau / 24.0
    for shoulder_fraction in (0.5, 2.0 / 3.0):
        shoulder = baseline[1] + shoulder_fraction * (0.0 - baseline[1])
        fraction_label = str(int(round(shoulder_fraction * 12.0)))
        for elbow_sign, elbow_label in ((-1.0, "negative"), (1.0, "positive")):
            elbow = elbow_sign * math.tau / 3.0
            for joint_index, joint_label in ((0, "shoulder_1"), (2, "shoulder_3")):
                for offset_sign, offset_label in ((-1.0, "negative"), (1.0, "positive")):
                    qpos = [0.0, shoulder, 0.0, elbow]
                    qpos[joint_index] = offset_sign * symmetric_offset
                    add(
                        f"symmetric_s{fraction_label}_{elbow_label}_{joint_label}_{offset_label}",
                        qpos,
                        "symmetric_sign_comparison",
                    )
    return tuple(candidates)


def _apply_qpos(simulator: HeadlessMuJoCoSimulator, qpos: Sequence[float]) -> None:
    simulator.data.qpos[:] = qpos
    simulator.data.qvel[:] = 0.0
    simulator._import_mujoco().mj_forward(simulator.model, simulator.data)


def _model_geometry(
    simulator: HeadlessMuJoCoSimulator,
    qpos: Sequence[float],
) -> tuple[float, float, float, tuple[float, float, float]]:
    mujoco = simulator._import_mujoco()
    _apply_qpos(simulator, qpos)
    shoulder_id = int(
        mujoco.mj_name2id(
            simulator.model,
            mujoco.mjtObj.mjOBJ_JOINT,
            "sholder_joint_3",
        )
    )
    elbow_id = int(
        mujoco.mj_name2id(
            simulator.model,
            mujoco.mjtObj.mjOBJ_JOINT,
            "elbow_joint",
        )
    )
    tip_id = int(
        mujoco.mj_name2id(simulator.model, mujoco.mjtObj.mjOBJ_SITE, "tip")
    )
    shoulder = np.asarray(simulator.data.xanchor[shoulder_id], dtype=np.float64)
    elbow = np.asarray(simulator.data.xanchor[elbow_id], dtype=np.float64)
    tip = np.asarray(simulator.data.site_xpos[tip_id], dtype=np.float64)
    extension = float(np.linalg.norm(tip - shoulder))
    nominal_reach = float(np.linalg.norm(elbow - shoulder) + np.linalg.norm(tip - elbow))
    return extension, nominal_reach, float(shoulder[2]), tuple(float(value) for value in tip)


def _joint_margins(
    qpos: Sequence[float],
    joint_metadata: Sequence[dict[str, object]],
) -> tuple[tuple[float | None, ...], float | None, tuple[str, ...]]:
    margins: list[float | None] = []
    failures: list[str] = []
    for value, joint in zip(qpos, joint_metadata, strict=True):
        if not joint["limited"]:
            margins.append(None)
            continue
        lower, upper = joint["range"]  # type: ignore[misc]
        if value < lower or value > upper:
            failures.append(f"joint_limit:{joint['name']}")
            margins.append(0.0)
            continue
        width = upper - lower
        margin = 2.0 * min(value - lower, upper - value) / width
        margins.append(float(margin))
        if margin < MINIMUM_LIMITED_JOINT_MARGIN:
            failures.append(f"joint_margin:{joint['name']}")
    limited = tuple(value for value in margins if value is not None)
    return tuple(margins), (min(limited) if limited else None), tuple(failures)


def _collision_evidence(
    simulator: HeadlessMuJoCoSimulator,
) -> CollisionEvidence:
    mujoco = simulator._import_mujoco()
    robot_body_ids = {
        int(mujoco.mj_name2id(simulator.model, mujoco.mjtObj.mjOBJ_BODY, body_name))
        for body_name in FAST_ARM_ARM_BODY_NAMES
    }
    robot_geom_ids = tuple(
        geom_id
        for geom_id in range(int(simulator.model.ngeom))
        if int(simulator.model.geom_bodyid[geom_id]) in robot_body_ids
    )
    collision_check_available = any(
        int(simulator.model.geom_contype[geom_id]) != 0
        or int(simulator.model.geom_conaffinity[geom_id]) != 0
        for geom_id in robot_geom_ids
    )
    contacts = tuple(simulator.data.contact[index] for index in range(int(simulator.data.ncon)))
    distances = tuple(float(contact.dist) for contact in contacts)
    penetration_count = sum(distance < -PENETRATION_TOLERANCE_M for distance in distances)
    if not robot_geom_ids:
        reason = "robot_collision_geoms_missing"
    elif not collision_check_available:
        reason = "robot_collision_geoms_disabled"
    else:
        reason = "robot_collision_geoms_enabled"
    return CollisionEvidence(
        collision_check_available=collision_check_available,
        collision_check_reason=reason,
        contact_count=len(contacts),
        penetration_count=penetration_count,
        minimum_contact_distance_m=min(distances) if distances else None,
    )


def _directions(pose: PoseDiagnostic) -> tuple[DirectionResult, ...]:
    results: list[DirectionResult] = []
    for direction in pose.directions:
        progress = calculate_endpoint_progress(
            direction.delta.requested_delta_m,
            direction.delta.measured_delta_m,
        )
        results.append(
            DirectionResult(
                label=direction.label,
                requested_delta_m=direction.delta.requested_delta_m,
                predicted_delta_m=direction.delta.predicted_delta_m,
                measured_delta_m=direction.delta.measured_delta_m,
                progress_ratio=progress.progress_ratio,
                direction_cosine=progress.direction_cosine,
                progress_status=progress.status,
            )
        )
    return tuple(results)


def _nearby_sensitivity(
    simulator: HeadlessMuJoCoSimulator,
    qpos: tuple[float, ...],
    pose: PoseDiagnostic,
    joint_metadata: Sequence[dict[str, object]],
) -> NearbySensitivity:
    base_tip = np.asarray(pose.tip_position_m, dtype=np.float64)
    base_rows = np.asarray(pose.native.row_norms, dtype=np.float64)
    minimum_rank = pose.native.effective_rank
    maximum_tip_shift = 0.0
    maximum_row_change = 0.0
    evaluated_count = 0
    for joint_index, joint in enumerate(joint_metadata):
        for sign in (-1.0, 1.0):
            nearby = list(qpos)
            nearby[joint_index] += sign * NEARBY_PERTURBATION_RAD
            if joint["limited"]:
                lower, upper = joint["range"]  # type: ignore[misc]
                nearby[joint_index] = min(upper, max(lower, nearby[joint_index]))
            nearby_tuple = tuple(nearby)
            if nearby_tuple == qpos:
                continue
            nearby_pose = evaluate_fast_arm_pose_mobility(simulator, nearby_tuple)
            minimum_rank = min(minimum_rank, nearby_pose.native.effective_rank)
            maximum_tip_shift = max(
                maximum_tip_shift,
                float(np.linalg.norm(np.asarray(nearby_pose.tip_position_m) - base_tip)),
            )
            denominator = np.maximum(base_rows, 1e-12)
            maximum_row_change = max(
                maximum_row_change,
                float(np.max(np.abs(np.asarray(nearby_pose.native.row_norms) - base_rows) / denominator)),
            )
            evaluated_count += 1
    return NearbySensitivity(
        perturbation_rad=NEARBY_PERTURBATION_RAD,
        evaluated_count=evaluated_count,
        minimum_effective_rank=minimum_rank,
        maximum_tip_shift_m=maximum_tip_shift,
        maximum_row_norm_relative_change=maximum_row_change,
    )


def _ranking_key(
    *,
    candidate_id: str,
    pose: PoseDiagnostic,
    directions: Sequence[DirectionResult],
    tip_height_m: float,
    shoulder_height_m: float,
    extension_ratio: float,
    minimum_margin: float | None,
    nearby: NearbySensitivity,
    nominal_reach_m: float,
    qpos: Sequence[float],
) -> tuple[object, ...]:
    progressing_count = sum(result.progress_status == "progressing" for result in directions)
    largest_singular = pose.native.singular_values[0] if pose.native.singular_values else 0.0
    useful = tuple(
        value
        for value in pose.native.singular_values
        if value > pose.native.effective_rank_tolerance
    )
    minimum_useful_ratio = (min(useful) / largest_singular) if useful and largest_singular > 0.0 else 0.0
    rows = pose.native.row_norms
    row_balance = min(rows) / max(rows) if rows and max(rows) > 0.0 else 0.0
    target_height = shoulder_height_m - NEUTRAL_HEIGHT_DROP_REACH_FRACTION * nominal_reach_m
    height_error = abs(tip_height_m - target_height) / nominal_reach_m
    extension_error = abs(extension_ratio - NEUTRAL_EXTENSION_RATIO)
    simplicity = sum(abs(value) for value in qpos)
    return (
        progressing_count,
        pose.native.effective_rank,
        row_balance,
        minimum_useful_ratio,
        -height_error,
        -extension_error,
        1.0 if minimum_margin is None else minimum_margin,
        -nearby.maximum_row_norm_relative_change,
        -nearby.maximum_tip_shift_m,
        -simplicity,
        candidate_id,
    )


def evaluate_fast_arm_neutral_initial_pose_candidates() -> NeutralPoseEvaluation:
    simulator = build_fast_arm_simulator()
    joint_metadata = _joint_metadata(simulator)
    candidates = generate_fast_arm_neutral_pose_candidates(simulator)
    baseline_extension, nominal_reach, shoulder_height, baseline_tip = _model_geometry(
        simulator,
        HISTORICAL_RAISED_BASELINE_QPOS_RAD,
    )
    baseline_height = baseline_tip[2]
    fk = FastArmMuJoCoModelForwardKinematicsSolver()
    evaluations: list[CandidateEvaluation] = []
    rejection_counts: Counter[str] = Counter()

    for candidate in candidates:
        qpos = validate_candidate_qpos(candidate.qpos_rad, expected_length=int(simulator.model.nq))
        pose = evaluate_fast_arm_pose_mobility(simulator, qpos)
        _apply_qpos(simulator, qpos)
        extension, candidate_nominal_reach, candidate_shoulder_height, tip = _model_geometry(
            simulator,
            qpos,
        )
        if not math.isclose(candidate_nominal_reach, nominal_reach, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("nominal reach changed across candidate poses")
        margins, minimum_margin, margin_failures = _joint_margins(qpos, joint_metadata)
        collision = _collision_evidence(simulator)
        fk_tip = fk.forward(qpos)
        fk_residual = math.dist(fk_tip, tip)
        directions = _directions(pose)
        nearby = _nearby_sensitivity(simulator, qpos, pose, joint_metadata)
        baseline_delta = math.dist(qpos, HISTORICAL_RAISED_BASELINE_QPOS_RAD)
        failures = list(margin_failures)
        if not candidate.baseline:
            if tip[2] > baseline_height - MINIMUM_HEIGHT_REDUCTION_REACH_FRACTION * nominal_reach:
                failures.append("tip_not_materially_lower")
            if extension > baseline_extension - MINIMUM_EXTENSION_REDUCTION_REACH_FRACTION * nominal_reach:
                failures.append("extension_not_materially_smaller")
            if tip[2] < MINIMUM_FLOOR_CLEARANCE_REACH_FRACTION * nominal_reach:
                failures.append("floor_clearance")
            if any(
                abs(qpos[index] - HISTORICAL_RAISED_BASELINE_QPOS_RAD[index])
                > MAXIMUM_PER_JOINT_BASELINE_DELTA_RAD
                for index in range(len(qpos))
            ):
                failures.append("baseline_delta_too_large")
        if collision.collision_check_available:
            if collision.contact_count != 0:
                failures.append("startup_contact")
            if collision.penetration_count != 0:
                failures.append("startup_penetration")
        if fk_residual > FK_SITE_TOLERANCE_M:
            failures.append("fk_site_mismatch")
        failures_tuple = tuple(sorted(set(failures)))
        for reason in failures_tuple:
            rejection_counts[reason] += 1
        useful = tuple(
            value
            for value in pose.native.singular_values
            if value > pose.native.effective_rank_tolerance
        )
        minimum_useful = min(useful) if useful else 0.0
        rows = pose.native.row_norms
        row_balance = min(rows) / max(rows) if rows and max(rows) > 0.0 else 0.0
        ranking_key = _ranking_key(
            candidate_id=candidate.candidate_id,
            pose=pose,
            directions=directions,
            tip_height_m=tip[2],
            shoulder_height_m=shoulder_height,
            extension_ratio=extension / nominal_reach,
            minimum_margin=minimum_margin,
            nearby=nearby,
            nominal_reach_m=nominal_reach,
            qpos=qpos,
        )
        evaluations.append(
            CandidateEvaluation(
                candidate_id=candidate.candidate_id,
                category=candidate.category,
                baseline=candidate.baseline,
                qpos_rad=qpos,
                tip_world_position_m=tip,
                tip_height_m=tip[2],
                shoulder_to_tip_extension_m=extension,
                extension_ratio=extension / nominal_reach,
                per_joint_normalized_limit_margin=margins,
                minimum_limited_joint_margin=minimum_margin,
                collision_check_available=collision.collision_check_available,
                collision_check_reason=collision.collision_check_reason,
                contact_count=collision.contact_count,
                penetration_count=collision.penetration_count,
                minimum_contact_distance_m=collision.minimum_contact_distance_m,
                tip_floor_clearance_m=tip[2],
                fk_site_residual_m=fk_residual,
                jacobian_numeric_rank=pose.native.numeric_rank,
                jacobian_effective_rank=pose.native.effective_rank,
                singular_values=pose.native.singular_values,
                minimum_useful_singular_value=minimum_useful,
                row_norms_xyz=pose.native.row_norms,
                row_norm_balance=row_balance,
                translational_manipulability=pose.native.manipulability,
                directions=directions,
                nearby_sensitivity=nearby,
                baseline_qpos_delta_norm_rad=baseline_delta,
                hard_constraint_failures=failures_tuple,
                ranking_key=ranking_key,
            )
        )

    eligible = tuple(candidate for candidate in evaluations if candidate.eligible)
    selected = max(eligible, key=lambda candidate: candidate.ranking_key) if eligible else None
    contract = {
        "baseline_qpos_rad": HISTORICAL_RAISED_BASELINE_QPOS_RAD,
        "minimum_height_reduction_reach_fraction": MINIMUM_HEIGHT_REDUCTION_REACH_FRACTION,
        "minimum_extension_reduction_reach_fraction": MINIMUM_EXTENSION_REDUCTION_REACH_FRACTION,
        "minimum_floor_clearance_reach_fraction": MINIMUM_FLOOR_CLEARANCE_REACH_FRACTION,
        "minimum_limited_joint_margin": MINIMUM_LIMITED_JOINT_MARGIN,
        "fk_site_tolerance_m": FK_SITE_TOLERANCE_M,
        "penetration_tolerance_m": PENETRATION_TOLERANCE_M,
        "maximum_per_joint_baseline_delta_rad": MAXIMUM_PER_JOINT_BASELINE_DELTA_RAD,
        "neutral_height_drop_reach_fraction": NEUTRAL_HEIGHT_DROP_REACH_FRACTION,
        "neutral_extension_ratio": NEUTRAL_EXTENSION_RATIO,
        "nearby_perturbation_rad": NEARBY_PERTURBATION_RAD,
        "ranking_order": (
            "six_direction_progressing_count_desc",
            "effective_rank_desc",
            "xyz_row_norm_balance_desc",
            "minimum_useful_singular_ratio_desc",
            "neutral_height_error_asc",
            "neutral_extension_error_asc",
            "limited_joint_margin_desc",
            "nearby_row_norm_sensitivity_asc",
            "nearby_tip_sensitivity_asc",
            "qpos_l1_simplicity_asc",
            "candidate_id_desc_tiebreak",
        ),
        "rank_three_required": False,
    }
    return NeutralPoseEvaluation(
        schema_version=SCHEMA_VERSION,
        model_identity="fast_arm_canonical",
        selection_contract=contract,
        nominal_reach_m=nominal_reach,
        shoulder_height_m=shoulder_height,
        candidate_count=len(evaluations),
        eligible_count=len(eligible),
        rejection_counts=dict(sorted(rejection_counts.items())),
        selected_candidate_id=selected.candidate_id if selected is not None else None,
        candidates=tuple(evaluations),
    )


def format_neutral_pose_ranking(
    evaluation: NeutralPoseEvaluation,
    *,
    limit: int = 10,
) -> str:
    if limit <= 0:
        raise ValueError("limit must be positive")
    eligible = sorted(
        (candidate for candidate in evaluation.candidates if candidate.eligible),
        key=lambda candidate: candidate.ranking_key,
        reverse=True,
    )
    lines = [
        f"candidates={evaluation.candidate_count} eligible={evaluation.eligible_count} selected={evaluation.selected_candidate_id}",
        "rank candidate qpos tip_z extension ratio rank row_balance progressing",
    ]
    for rank, candidate in enumerate(eligible[:limit], start=1):
        progressing = sum(result.progress_status == "progressing" for result in candidate.directions)
        lines.append(
            f"{rank:>2} {candidate.candidate_id} {candidate.qpos_rad} "
            f"{candidate.tip_height_m:.6f} {candidate.shoulder_to_tip_extension_m:.6f} "
            f"{candidate.extension_ratio:.6f} {candidate.jacobian_effective_rank} "
            f"{candidate.row_norm_balance:.6f} {progressing}/6"
        )
    if evaluation.rejection_counts:
        lines.append(f"rejections={evaluation.rejection_counts}")
    return "\n".join(lines)


__all__ = [
    "CandidateEvaluation",
    "CandidateSpec",
    "FAST_ARM_INITIAL_STATE_CONTRACT",
    "FAST_ARM_INITIAL_STATE_QPOS_RAD",
    "FAST_ARM_INITIAL_STATE_TIP_POSITION_M",
    "FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ",
    "HISTORICAL_RAISED_BASELINE_QPOS_RAD",
    "NeutralPoseEvaluation",
    "evaluate_fast_arm_neutral_initial_pose_candidates",
    "format_neutral_pose_ranking",
    "generate_fast_arm_neutral_pose_candidates",
    "validate_candidate_qpos",
]
