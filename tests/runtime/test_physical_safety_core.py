from __future__ import annotations

from copy import copy
from dataclasses import replace

import pytest

from xpotato_sim.runtime.safety.collision_policy import (
    CollisionCheckResult,
    CollisionContext,
    CollisionEvaluation,
    CollisionKind,
    CollisionStatus,
)
from xpotato_sim.runtime.safety.limit_resolution import (
    DEFAULT_COMPARISON_TOLERANCE_RAD,
    JointSpaceConversion,
    LimitParityRecord,
    LimitResolutionResult,
    LimitResolutionStatus,
    LimitSpace,
    ParityStatus,
    ResolvedJointBound,
    resolve_joint_space_bounds,
)
from xpotato_sim.runtime.safety.physical_safety_core import (
    BoundedSafetySamplingResult,
    SafetyComponent,
    SafetyComponentAssessment,
    SafetyDecision,
    SafetyDecisionAction,
    SafetyInput,
    SafetyReason,
    evaluate_bounded_safety_samples,
    evaluate_physical_safety,
    validate_bounded_safety_sampling_result,
    validate_safety_decision,
    validate_safety_input,
    validate_safety_projection,
)
from xpotato_sim.runtime.safety.trajectory_feasibility import (
    ConfigurationFeasibilityResult,
    ConfigurationState,
    FeasibilityDiagnostic,
    FeasibilityStatus,
    JacobianDiagnostic,
    TrajectoryFeasibilityPolicy,
    TrajectoryFeasibilityResult,
    TrajectorySample,
    VelocityEvidenceBinding,
    VelocityEvidenceKind,
    evaluate_configuration_feasibility,
    evaluate_trajectory_feasibility,
)
from xpotato_sim.runtime.safety.physical_limits import (
    EvidenceStatus,
    LimitQuantity,
    LimitSourceProvenance,
    PhysicalLimit,
    source_identity,
)


JOINTS = ("joint_a", "joint_b")
LIMIT_TOLERANCE_RAD = DEFAULT_COMPARISON_TOLERANCE_RAD
POLICY_ID = "fixture-dynamic-policy"
POLICY_REVISION = "rev-1"


def _source(name: str, status: EvidenceStatus) -> LimitSourceProvenance:
    return LimitSourceProvenance(
        source_kind="manufacturer_document"
        if status is EvidenceStatus.AUTHORITATIVE
        else "software_config",
        source_id=name,
        revision="rev-1",
        status=status,
        evidence_reference="fixture-record" if status is EvidenceStatus.AUTHORITATIVE else None,
    )


def _limits(
    status: LimitResolutionStatus = LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
    *,
    parity_delta: float = 0.0,
    tolerance: float = LIMIT_TOLERANCE_RAD,
    expected_joint_names: tuple[str, ...] = JOINTS,
    robot_id: str = "fixture-robot",
) -> LimitResolutionResult:
    def _bound(name: str) -> ResolvedJointBound:
        if status in {
            LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
            LimitResolutionStatus.RESOLVED_PROVISIONAL,
        }:
            source_status = (
                EvidenceStatus.AUTHORITATIVE
                if status is LimitResolutionStatus.RESOLVED_AUTHORITATIVE
                else EvidenceStatus.PROVISIONAL
            )
            source = _source(f"{name}-source", source_status)
            parity = LimitParityRecord(
                joint_name=name,
                source_name=source_identity(source, unit="rad"),
                status=ParityStatus.MATCH,
                lower=-1.0 + parity_delta,
                upper=1.0 + parity_delta,
                unit="rad",
                source=source,
            )
            return ResolvedJointBound(
                joint_name=name,
                lower_rad=-1.0,
                upper_rad=1.0,
                status=status,
                source_names=(source_identity(source, unit="rad"),),
                parity=(parity,),
                comparison_tolerance_rad=tolerance,
            )
        source_status = {
            LimitResolutionStatus.MISMATCH: EvidenceStatus.PROVISIONAL,
            LimitResolutionStatus.UNKNOWN: EvidenceStatus.UNKNOWN,
            LimitResolutionStatus.UNAVAILABLE: EvidenceStatus.UNAVAILABLE,
            LimitResolutionStatus.INVALID: EvidenceStatus.INVALID,
        }[status]
        if status is LimitResolutionStatus.MISMATCH:
            first = _source(f"{name}-source", source_status)
            second = _source(f"{name}-second-source", source_status)
            parity = (
                LimitParityRecord(name, source_identity(first, unit="rad"), ParityStatus.MISMATCH, -1.0, 1.0, "rad", "source ranges disagree", first),
                LimitParityRecord(name, source_identity(second, unit="rad"), ParityStatus.MISMATCH, -0.5, 0.5, "rad", "source ranges disagree", second),
            )
            return ResolvedJointBound(
                name,
                None,
                None,
                status,
                (source_identity(first, unit="rad"), source_identity(second, unit="rad")),
                parity,
                "source ranges disagree",
                tolerance,
            )
        source = _source(f"{name}-source", source_status)
        parity_status = {
            LimitResolutionStatus.UNKNOWN: ParityStatus.UNKNOWN,
            LimitResolutionStatus.UNAVAILABLE: ParityStatus.UNAVAILABLE,
            LimitResolutionStatus.INVALID: ParityStatus.INVALID,
        }[status]
        parity = LimitParityRecord(
            name,
            source_identity(source, unit="rad"),
            parity_status,
            None,
            None,
            "rad",
            status.value,
            source,
        )
        return ResolvedJointBound(
            name,
            None,
            None,
            status,
            (source_identity(source, unit="rad"),),
            (parity,),
            status.value,
            tolerance,
        )

    bounds = tuple(_bound(name) for name in expected_joint_names)
    return LimitResolutionResult(
        1,
        robot_id,
        bounds,
        (),
        expected_joint_names,
        tolerance,
    )


def _position_limit(
    unit: str,
    source_kind: str,
    *,
    status: EvidenceStatus = EvidenceStatus.PROVISIONAL,
) -> PhysicalLimit:
    source = LimitSourceProvenance(
        source_kind=(
            "manufacturer_document"
            if status is EvidenceStatus.AUTHORITATIVE
            else "software_config"
        ),
        source_id=f"{source_kind}-source",
        revision="rev-1",
        status=status,
        evidence_reference="fixture-record" if status is EvidenceStatus.AUTHORITATIVE else None,
    )
    return PhysicalLimit(
        name="joint_a",
        quantity=LimitQuantity.POSITION,
        lower=-1.0,
        upper=1.0,
        unit=unit,
        space=LimitSpace.JOINT,
        frame="fast_arm joint space",
        status=status,
        source=source,
    )


def _dynamic_policy_fingerprint(
    expected_joint_names: tuple[str, ...],
    *,
    status: EvidenceStatus = EvidenceStatus.AUTHORITATIVE,
) -> tuple[
    tuple[object, ...],
    tuple[str, ...],
    tuple[EvidenceStatus, ...],
    tuple[str, ...],
]:
    """P4のcanonical policy fingerprintをfixture側で再利用する。"""

    policy = _dynamic_policy(expected_joint_names, status=status)
    fingerprint = policy.canonical_fingerprint
    source_ids: list[str] = []
    statuses: list[EvidenceStatus] = []
    evidence_ids: list[str] = []
    for raw in fingerprint[2]:
        source_ids.append("|".join((raw[0], raw[1], raw[2], raw[4], raw[10], raw[11], raw[12])))
        statuses.append(EvidenceStatus(raw[7]))
        evidence_ids.append(raw[13] or source_ids[-1])
    return fingerprint, tuple(source_ids), tuple(statuses), tuple(evidence_ids)


def _dynamic_policy(
    expected_joint_names: tuple[str, ...],
    *,
    status: EvidenceStatus = EvidenceStatus.AUTHORITATIVE,
) -> TrajectoryFeasibilityPolicy:
    """P4 evaluatorへ渡すtyped dynamic policyを組み立てる。"""

    limits: list[PhysicalLimit] = []
    for name in expected_joint_names:
        for quantity, unit, lower, upper in (
            (LimitQuantity.VELOCITY, "rad/s", -2.0, 2.0),
            (LimitQuantity.ACCELERATION, "rad/s^2", -10.0, 10.0),
        ):
            index = len(limits)
            limits.append(
                PhysicalLimit(
                    name=name,
                    quantity=quantity,
                    lower=lower if status in {EvidenceStatus.AUTHORITATIVE, EvidenceStatus.PROVISIONAL} else None,
                    upper=upper if status in {EvidenceStatus.AUTHORITATIVE, EvidenceStatus.PROVISIONAL} else None,
                    unit=unit,
                    space=LimitSpace.JOINT,
                    frame="fast_arm joint space",
                    status=status,
                    source=LimitSourceProvenance(
                        source_kind=(
                            "manufacturer_document"
                            if status is EvidenceStatus.AUTHORITATIVE
                            else "software_config"
                        ),
                        source_id=f"fixture-limit-{index}",
                        revision="rev-1",
                        status=status,
                        evidence_reference=(
                            f"fixture-evidence-{index}"
                            if status is EvidenceStatus.AUTHORITATIVE
                            else None
                        ),
                    ),
                    reason=None if status in {EvidenceStatus.AUTHORITATIVE, EvidenceStatus.PROVISIONAL} else "fixture unavailable",
                )
            )
    return TrajectoryFeasibilityPolicy(
        joint_names=expected_joint_names,
        dynamic_limits=tuple(limits),
        expected_cadence_s=0.1,
        cadence_tolerance_s=1e-9,
        maximum_gap_s=0.2,
        required_jacobian_rank=3,
        minimum_singular_value=0.1,
        maximum_condition_number=100.0,
        policy_id=POLICY_ID,
        policy_revision=POLICY_REVISION,
    )


def _conversion(relation_id: str = "fixture-relation") -> JointSpaceConversion:
    return JointSpaceConversion(
        source_space=LimitSpace.MOTOR,
        joint_name="joint_a",
        source_name="motor_a",
        gear_ratio=2.0,
        sign=1.0,
        offset=0.0,
        relation_id=relation_id,
        unit="tick",
    )


def _mixed_limit_result(
    parity_status: ParityStatus,
    resolution_status: LimitResolutionStatus,
) -> LimitResolutionResult:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    bound = result.bounds[0]
    matched = bound.parity[0]
    unresolved_source = _source(
        "joint_a-unresolved-source",
        EvidenceStatus.UNKNOWN
        if parity_status is ParityStatus.UNKNOWN
        else EvidenceStatus.UNAVAILABLE,
    )
    unresolved = LimitParityRecord(
        joint_name=matched.joint_name,
        source_name=source_identity(unresolved_source, unit="rad"),
        status=parity_status,
        lower=None,
        upper=None,
        unit="rad",
        reason=f"source {parity_status.value}",
        source=unresolved_source,
    )
    mixed_bound = ResolvedJointBound(
        joint_name=bound.joint_name,
        lower_rad=None,
        upper_rad=None,
        status=resolution_status,
        source_names=(matched.source_name, unresolved.source_name),
        parity=(matched, unresolved),
        reason=f"limit source {resolution_status.value}",
    )
    return replace(result, bounds=(mixed_bound, result.bounds[1]))


def _trajectory_dynamic(
    *,
    authoritative: bool = True,
    status: FeasibilityStatus = FeasibilityStatus.FEASIBLE,
) -> TrajectoryFeasibilityResult:
    sample_count = 3
    source_ids = tuple(f"fixture-trajectory-{index}" for index in range(sample_count))
    if status is FeasibilityStatus.FEASIBLE:
        policy = _dynamic_policy(
            JOINTS,
            status=(EvidenceStatus.AUTHORITATIVE if authoritative else EvidenceStatus.PROVISIONAL),
        )
        samples = tuple(
            TrajectorySample(
                timestamp_s=index * 0.1,
                qpos_rad=(index * 0.1, 0.0),
                qvel_rad_s=(1.0, 0.0),
                jacobian=JacobianDiagnostic(
                    source_id=f"fixture-jacobian-{index}",
                    row_count=3,
                    column_count=3,
                    numeric_rank=3,
                    effective_rank=3,
                    minimum_singular_value=0.5,
                    condition_number=2.0,
                    evidence_reference=f"fixture-jacobian-evidence-{index}",
                ),
                source_id=source_ids[index],
            )
            for index in range(sample_count)
        )
        return evaluate_trajectory_feasibility(samples, policy)
    bound_status = EvidenceStatus.AUTHORITATIVE if authoritative else EvidenceStatus.PROVISIONAL
    velocity_evidence = tuple(
        [
            VelocityEvidenceBinding(VelocityEvidenceKind.SAMPLE_QVEL, index, source_ids[index])
            for index in range(sample_count)
        ]
        + [
            VelocityEvidenceBinding(VelocityEvidenceKind.FINITE_DIFFERENCE, index, source_ids[index])
            for index in range(1, sample_count)
        ]
    ) if status is FeasibilityStatus.FEASIBLE else ()
    diagnostics = (
        (FeasibilityDiagnostic("feasibility_clear", "fixture clear", provenance=source_ids[0]),)
        if status is FeasibilityStatus.FEASIBLE
        else (FeasibilityDiagnostic(f"{status.value}_fixture", f"fixture {status.value}"),)
    )
    policy_binding = _dynamic_policy_fingerprint(
        JOINTS,
        status=bound_status,
    ) if status is FeasibilityStatus.FEASIBLE else None
    if policy_binding is None:
        limit_source_ids = tuple(f"fixture-limit-{index}" for index in range(2 * len(JOINTS)))
        bound_evidence_ids = tuple(f"fixture-evidence-{index}" for index in range(2 * len(JOINTS)))
        policy_fingerprint = ()
    else:
        policy_fingerprint, limit_source_ids, _, bound_evidence_ids = policy_binding
    return TrajectoryFeasibilityResult(
        status,
        diagnostics[0].code,
        sample_count,
        diagnostics,
        source_ids,
        (bound_status,) * (2 * len(JOINTS)),
        JOINTS,
        POLICY_ID,
        POLICY_REVISION,
        limit_source_ids,
        bound_evidence_ids,
        (True,) * sample_count,
        (True,) * sample_count,
        velocity_evidence,
        policy_fingerprint,
        ("fixture-jacobian",) * sample_count
        if status is FeasibilityStatus.FEASIBLE
        else (),
        ("fixture-jacobian-evidence",) * sample_count
        if status is FeasibilityStatus.FEASIBLE
        else (),
    )


def _collision(status: CollisionStatus) -> CollisionCheckResult:
    pair_id = "arm|target" if status is CollisionStatus.CONTACT else "arm|floor"
    second_role = "task_object" if status is CollisionStatus.CONTACT else "environment"
    context = CollisionContext(
        robot_id="fixture-robot",
        model_id="fixture-model",
        policy_id="fixture-collision-policy",
        policy_revision="rev-1",
        inventory_id="fixture-inventory",
        inventory_revision="rev-1",
        expected_pair_ids=(pair_id,),
        inventory_fingerprint=(
            ("arm", "arm", "robot", "fixture-model"),
            (pair_id.split("|")[1], pair_id.split("|")[1], second_role, "fixture-model"),
        ),
        policy_fingerprint=("fixture-collision-policy", 0.01, 0.02, ()),
    )
    kind = CollisionKind.TASK_OBJECT_CONTACT if status is CollisionStatus.CONTACT else CollisionKind.ENVIRONMENT_COLLISION
    reason_code = {
        CollisionStatus.CLEAR: "pair_clear",
        CollisionStatus.NEAR_COLLISION: "near_collision_clearance",
        CollisionStatus.COLLISION: "environment_penetration",
        CollisionStatus.CONTACT: "task_object_contact",
        CollisionStatus.UNAVAILABLE: "collision_observation_unavailable",
        CollisionStatus.UNKNOWN: "collision_distance_unknown",
        CollisionStatus.INVALID: "invalid_collision_evidence",
    }.get(status, "pair_clear")
    distance = {
        CollisionStatus.CLEAR: 0.1,
        CollisionStatus.NEAR_COLLISION: 0.015,
        CollisionStatus.COLLISION: -0.001,
        CollisionStatus.CONTACT: 0.0,
    }.get(status)
    evaluation = CollisionEvaluation(
        pair_id=pair_id,
        kind=kind,
        status=status,
        distance_m=distance,
        clearance_m=0.01,
        reason_code=reason_code,
        provenance="fixture-collision" if status not in {CollisionStatus.UNAVAILABLE, CollisionStatus.UNKNOWN} else None,
        near_collision_margin_m=0.02,
    )
    aggregate_reason = "collision_clear" if status is CollisionStatus.CLEAR else reason_code
    return CollisionCheckResult(context, status, (evaluation,), aggregate_reason)


def _dynamic(
    status: FeasibilityStatus,
    *,
    authoritative: bool = True,
    expected_joint_names: tuple[str, ...] = JOINTS,
) -> ConfigurationFeasibilityResult:
    evidence_status = EvidenceStatus.AUTHORITATIVE if authoritative else EvidenceStatus.PROVISIONAL
    if status is FeasibilityStatus.FEASIBLE:
        policy = _dynamic_policy(expected_joint_names, status=evidence_status)
        state = ConfigurationState(
            qpos_rad=(0.0,) * len(expected_joint_names),
            qvel_rad_s=(0.0,) * len(expected_joint_names),
            jacobian=JacobianDiagnostic(
                source_id="fixture-jacobian",
                row_count=3,
                column_count=3,
                numeric_rank=3,
                effective_rank=3,
                minimum_singular_value=0.5,
                condition_number=2.0,
                evidence_reference="fixture-jacobian-evidence",
            ),
            source_id="fixture-dynamic",
        )
        return evaluate_configuration_feasibility(state, policy)
    diagnostics = (
        (FeasibilityDiagnostic("feasibility_clear", "fixture clear", provenance="fixture-dynamic"),)
        if status is FeasibilityStatus.FEASIBLE
        else (FeasibilityDiagnostic(f"{status.value}_fixture", f"fixture {status.value}"),)
    )
    if status is FeasibilityStatus.UNKNOWN:
        evidence_status = EvidenceStatus.UNKNOWN
    elif status is FeasibilityStatus.UNAVAILABLE:
        diagnostics = (
            FeasibilityDiagnostic(
                "unavailable_qvel",
                "fixture qvel evidence is unavailable",
            ),
        )
    elif status is FeasibilityStatus.INVALID:
        diagnostics = (FeasibilityDiagnostic("invalid_limit_source", "fixture invalid"),)
        evidence_status = EvidenceStatus.INVALID
    policy_binding = _dynamic_policy_fingerprint(
        expected_joint_names,
        status=evidence_status,
    ) if status is FeasibilityStatus.FEASIBLE else None
    if policy_binding is None:
        source_ids = tuple(f"fixture-limit-{index}" for index in range(len(expected_joint_names)))
        evidence_ids = tuple(f"fixture-evidence-{index}" for index in range(len(expected_joint_names)))
    else:
        _, all_source_ids, _, all_evidence_ids = policy_binding
        source_ids = all_source_ids[0::2]
        evidence_ids = all_evidence_ids[0::2]
    return ConfigurationFeasibilityResult(
        status,
        diagnostics[0].code,
        diagnostics,
        "fixture-dynamic",
        (evidence_status,) * len(expected_joint_names),
        expected_joint_names,
        POLICY_ID,
        POLICY_REVISION,
        source_ids,
        evidence_ids,
        True if status is not FeasibilityStatus.UNAVAILABLE else None,
        True if status is not FeasibilityStatus.UNAVAILABLE else None,
        policy_binding[0] if policy_binding is not None else (),
        ("fixture-jacobian",),
        ("fixture-jacobian-evidence",),
    )


def _input(
    *,
    limits: LimitResolutionStatus = LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
    collision: CollisionStatus = CollisionStatus.CLEAR,
    dynamic: FeasibilityStatus = FeasibilityStatus.FEASIBLE,
    dynamic_authoritative: bool = True,
    candidate_id: str = "candidate-001",
) -> SafetyInput:
    return SafetyInput(
        candidate_id,
        _limits(limits),
        _collision(collision),
        _dynamic(dynamic, authoritative=dynamic_authoritative),
        ("candidate-fixture",),
    )


def test_all_authoritative_clear_components_allow_with_shared_reason_identity() -> None:
    decision = evaluate_physical_safety(_input())

    assert decision.action is SafetyDecisionAction.ALLOW
    assert decision.allowed
    assert decision.reason.component is SafetyComponent.LIMIT
    assert decision.reason.identity == "limit:limit_resolution_authoritative"
    assert decision.reason.operator_message
    assert {item.component for item in decision.assessments} == {
        SafetyComponent.LIMIT,
        SafetyComponent.COLLISION,
        SafetyComponent.DYNAMIC,
    }
    assert decision.provenance == (
        "candidate-fixture",
        "fixture-collision",
        "fixture-dynamic",
        "manufacturer_document:joint_a-source@rev-1[unit=rad]",
        "manufacturer_document:joint_b-source@rev-1[unit=rad]",
    )


def test_collision_stop_is_distinct_and_wins_over_reject_or_hold() -> None:
    decision = evaluate_physical_safety(
        _input(collision=CollisionStatus.COLLISION, dynamic=FeasibilityStatus.REJECTED)
    )

    assert decision.action is SafetyDecisionAction.STOP
    assert decision.reason.component is SafetyComponent.COLLISION
    assert decision.reason.reason_code == "collision_detected"
    assert not decision.allowed


def test_unknown_unavailable_and_invalid_never_allow() -> None:
    unknown = evaluate_physical_safety(_input(limits=LimitResolutionStatus.UNKNOWN))
    assert unknown.action is SafetyDecisionAction.UNAVAILABLE
    assert unknown.reason.reason_code == "limit_resolution_unavailable"

    unavailable = evaluate_physical_safety(_input(collision=CollisionStatus.UNAVAILABLE))
    assert unavailable.action is SafetyDecisionAction.UNAVAILABLE

    invalid = evaluate_physical_safety(_input(dynamic=FeasibilityStatus.INVALID))
    assert invalid.action is SafetyDecisionAction.INVALID
    assert invalid.reason.component is SafetyComponent.DYNAMIC

    empty_collision = copy(_collision(CollisionStatus.CLEAR))
    object.__setattr__(empty_collision, "evaluations", ())
    empty_clear = evaluate_physical_safety(
        SafetyInput("empty-clear", _limits(), empty_collision, _dynamic(FeasibilityStatus.FEASIBLE))
    )
    assert empty_clear.action is SafetyDecisionAction.INVALID
    assert empty_clear.reason.reason_code == "collision_result_inconsistent"

    collision = _collision(CollisionStatus.COLLISION)
    inconsistent_clear = copy(collision)
    object.__setattr__(inconsistent_clear, "status", CollisionStatus.CLEAR)
    object.__setattr__(inconsistent_clear, "reason_code", "collision_clear")
    inconsistent_decision = evaluate_physical_safety(
        SafetyInput(
            "inconsistent-clear",
            _limits(),
            inconsistent_clear,
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )
    assert inconsistent_decision.action is SafetyDecisionAction.INVALID
    assert inconsistent_decision.reason.reason_code == "collision_result_inconsistent"


def test_provisional_evidence_holds_and_mismatch_rejects() -> None:
    provisional = evaluate_physical_safety(
        _input(limits=LimitResolutionStatus.RESOLVED_PROVISIONAL, dynamic_authoritative=False)
    )
    assert provisional.action is SafetyDecisionAction.HOLD
    assert provisional.reason.reason_code == "limit_resolution_provisional"

    mismatch = evaluate_physical_safety(_input(limits=LimitResolutionStatus.MISMATCH))
    assert mismatch.action is SafetyDecisionAction.REJECT
    assert mismatch.reason.reason_code == "limit_resolution_mismatch"


def test_mixed_match_and_unknown_limit_evidence_is_unavailable_not_invalid() -> None:
    decision = evaluate_physical_safety(
        SafetyInput(
            "mixed-limit-unknown",
            _mixed_limit_result(ParityStatus.UNKNOWN, LimitResolutionStatus.UNKNOWN),
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.UNAVAILABLE
    assert decision.reason.identity == "limit:limit_resolution_unavailable"


def test_mixed_match_and_unavailable_limit_evidence_is_unavailable_not_invalid() -> None:
    decision = evaluate_physical_safety(
        SafetyInput(
            "mixed-limit-unavailable",
            _mixed_limit_result(ParityStatus.UNAVAILABLE, LimitResolutionStatus.UNAVAILABLE),
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.UNAVAILABLE
    assert decision.reason.identity == "limit:limit_resolution_unavailable"


@pytest.mark.parametrize("source_units", (("deg",), ("deg", "deg")))
def test_matching_non_rad_resolver_result_is_unavailable_at_p5(
    source_units: tuple[str, ...],
) -> None:
    result = resolve_joint_space_bounds(
        tuple(
            _position_limit(unit, f"degree-source-{index}")
            for index, unit in enumerate(source_units)
        ),
        expected_joint_names=("joint_a",),
        robot_id="fixture-robot",
    )

    bound = result.bound_for("joint_a")
    assert bound.status is LimitResolutionStatus.UNKNOWN
    assert bound.lower_rad is None
    assert bound.upper_rad is None
    decision = evaluate_physical_safety(
        SafetyInput(
            "matching-non-rad-limit",
            result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE, expected_joint_names=("joint_a",)),
        )
    )

    assert decision.action is SafetyDecisionAction.UNAVAILABLE
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_unavailable"


def test_mixed_rad_and_non_rad_resolver_result_remains_mismatch_at_p5() -> None:
    result = resolve_joint_space_bounds(
        (
            _position_limit("rad", "rad-source"),
            _position_limit("deg", "degree-source"),
        ),
        expected_joint_names=("joint_a",),
        robot_id="fixture-robot",
    )

    bound = result.bound_for("joint_a")
    assert bound.status is LimitResolutionStatus.MISMATCH
    assert bound.reason == "limit units disagree"
    decision = evaluate_physical_safety(
        SafetyInput(
            "mixed-rad-non-rad-limit",
            result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE, expected_joint_names=("joint_a",)),
        )
    )

    assert decision.action is SafetyDecisionAction.REJECT
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_mismatch"


def test_matching_rad_resolver_result_remains_authoritative_at_p5() -> None:
    result = resolve_joint_space_bounds(
        (_position_limit("rad", "authoritative-rad", status=EvidenceStatus.AUTHORITATIVE),),
        expected_joint_names=("joint_a",),
        robot_id="fixture-robot",
    )

    assert result.bound_for("joint_a").status is LimitResolutionStatus.RESOLVED_AUTHORITATIVE
    decision = evaluate_physical_safety(
        SafetyInput(
            "matching-rad-limit",
            result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE, expected_joint_names=("joint_a",)),
        )
    )

    assert decision.action is SafetyDecisionAction.ALLOW
    assert decision.allowed


@pytest.mark.parametrize(
    ("second_unit", "second_lower", "second_upper", "reason"),
    (
        ("rad", -2.0, 2.0, "limit ranges disagree"),
        ("deg", -1.0, 1.0, "limit units disagree"),
    ),
)
def test_fully_comparable_matching_parity_mismatch_remains_rejected(
    second_unit: str,
    second_lower: float,
    second_upper: float,
    reason: str,
) -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    bound = result.bounds[0]
    first = bound.parity[0]
    second_source = _source("joint_a-second-source", EvidenceStatus.AUTHORITATIVE)
    second = LimitParityRecord(
        joint_name=first.joint_name,
        source_name=source_identity(second_source, unit=second_unit),
        status=ParityStatus.MATCH,
        lower=second_lower,
        upper=second_upper,
        unit=second_unit,
        reason=None,
        source=second_source,
    )
    mismatch_bound = ResolvedJointBound(
        joint_name=bound.joint_name,
        lower_rad=None,
        upper_rad=None,
        status=LimitResolutionStatus.MISMATCH,
        source_names=(first.source_name, second.source_name),
        parity=(first, second),
        reason=reason,
    )
    decision = evaluate_physical_safety(
        SafetyInput(
            "fully-comparable-limit-mismatch",
            replace(result, bounds=(mismatch_bound, result.bounds[1])),
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.REJECT
    assert decision.reason.identity == "limit:limit_resolution_mismatch"


def test_resolved_bound_constructor_rejects_inconsistent_aggregate_shape() -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    bound = result.bounds[0]
    unknown_parity = replace(
        bound.parity[0],
        status=ParityStatus.UNKNOWN,
        lower=None,
        upper=None,
        reason="source unknown",
    )

    with pytest.raises(ValueError, match="resolved bound requires both lower_rad and upper_rad"):
        ResolvedJointBound(
            joint_name=bound.joint_name,
            lower_rad=None,
            upper_rad=None,
            status=LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
            source_names=bound.source_names,
            parity=(unknown_parity,),
            reason=None,
        )


def test_limit_aggregate_status_must_match_parity() -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    bound = result.bounds[0]
    inconsistent_parity = copy(bound.parity[0])
    object.__setattr__(inconsistent_parity, "status", ParityStatus.UNKNOWN)
    object.__setattr__(inconsistent_parity, "lower", None)
    object.__setattr__(inconsistent_parity, "upper", None)
    object.__setattr__(inconsistent_parity, "reason", "source unknown")
    # test専用: validなimmutable boundをcopyし、P5の防御境界だけへ不整合を注入する。
    inconsistent_bound = copy(bound)
    object.__setattr__(inconsistent_bound, "parity", (inconsistent_parity,))
    object.__setattr__(inconsistent_bound, "lower_rad", None)
    object.__setattr__(inconsistent_bound, "upper_rad", None)
    inconsistent_result = copy(result)
    object.__setattr__(inconsistent_result, "bounds", (inconsistent_bound, result.bounds[1]))
    decision = evaluate_physical_safety(
        SafetyInput(
            "inconsistent-limit",
            inconsistent_result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"


def test_resolved_degree_parity_never_allows_without_conversion() -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    bound = result.bounds[0]
    degree_parity = copy(bound.parity[0])
    object.__setattr__(degree_parity, "unit", "deg")
    # test専用: validなimmutable boundのcopyへnon-rad parityを注入し、変換やauthority補完を許さない。
    malformed_bound = copy(bound)
    object.__setattr__(malformed_bound, "parity", (degree_parity,))
    malformed_result = copy(result)
    object.__setattr__(malformed_result, "bounds", (malformed_bound, result.bounds[1]))
    decision = evaluate_physical_safety(
        SafetyInput(
            "degree-limit",
            malformed_result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"
    assert malformed_bound.status is LimitResolutionStatus.RESOLVED_AUTHORITATIVE
    assert malformed_bound.lower_rad == -1.0
    assert malformed_bound.parity[0].unit == "deg"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_space", "motor"),
        ("joint_name", ""),
        ("source_name", ""),
        ("gear_ratio", 0.0),
        ("sign", 0.0),
        ("offset", float("nan")),
        ("relation_id", ""),
        ("unit", ""),
    ),
)
def test_malformed_conversion_relation_is_invalid_at_p5_boundary(field: str, value: object) -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    malformed_relation = copy(_conversion())
    # test専用: constructorを迂回してconversion relationのinvariantを壊す。
    object.__setattr__(malformed_relation, field, value)
    malformed_result = copy(result)
    object.__setattr__(malformed_result, "conversion_relations", (malformed_relation,))

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-conversion",
            malformed_result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"


def test_duplicate_conversion_relation_identity_is_invalid_at_p5_boundary() -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    malformed_result = copy(result)
    object.__setattr__(
        malformed_result,
        "conversion_relations",
        (_conversion("duplicate"), _conversion("duplicate")),
    )

    decision = evaluate_physical_safety(
        SafetyInput(
            "duplicate-conversion",
            malformed_result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"


def test_empty_limit_provenance_is_invalid_at_p5_boundary() -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    # test専用: constructorを迂回して、finite authoritative boundの証拠集合だけを空にする。
    malformed_bound = copy(result.bounds[0])
    object.__setattr__(malformed_bound, "source_names", ())
    object.__setattr__(malformed_bound, "parity", ())
    malformed_result = copy(result)
    object.__setattr__(malformed_result, "bounds", (malformed_bound, result.bounds[1]))

    decision = evaluate_physical_safety(
        SafetyInput(
            "empty-limit-provenance",
            malformed_result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"


def test_malformed_limit_source_identity_is_invalid_without_exception() -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    malformed_bound = copy(result.bounds[0])
    # test専用: constructorを迂回してsource identityへ非文字列を注入する。
    object.__setattr__(malformed_bound, "source_names", (None,))
    malformed_result = copy(result)
    object.__setattr__(malformed_result, "bounds", (malformed_bound, result.bounds[1]))

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-limit-source",
            malformed_result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"
    assert all(isinstance(item, str) for item in decision.reason.provenance)


def test_empty_limit_result_bounds_are_invalid_at_p5_boundary() -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    # test専用: immutable aggregateを迂回して、result-level boundsを空集合にする。
    malformed_result = copy(result)
    object.__setattr__(malformed_result, "bounds", ())

    decision = evaluate_physical_safety(
        SafetyInput(
            "empty-limit-result",
            malformed_result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"


def test_duplicate_limit_result_bounds_are_invalid_at_p5_boundary() -> None:
    result = _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE)
    # test専用: immutable aggregateを迂回して、同一joint boundを重複させる。
    malformed_result = copy(result)
    object.__setattr__(malformed_result, "bounds", (result.bounds[0], result.bounds[0]))

    decision = evaluate_physical_safety(
        SafetyInput(
            "duplicate-limit-result",
            malformed_result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"


def test_collision_aggregate_reason_must_match_pair_diagnostic() -> None:
    collision = _collision(CollisionStatus.CLEAR)
    malformed = copy(collision)
    object.__setattr__(malformed, "reason_code", "wrong-clear-reason")
    decision = evaluate_physical_safety(
        SafetyInput(
            "inconsistent-collision",
            _limits(),
            malformed,
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.reason.identity == "collision:collision_result_inconsistent"


def test_malformed_clear_pair_is_invalid_at_p5_boundary() -> None:
    malformed = object.__new__(CollisionEvaluation)
    object.__setattr__(malformed, "pair_id", "not-a-pair")
    object.__setattr__(malformed, "kind", CollisionKind.ENVIRONMENT_COLLISION)
    object.__setattr__(malformed, "status", CollisionStatus.CLEAR)
    object.__setattr__(malformed, "distance_m", None)
    object.__setattr__(malformed, "clearance_m", 0.01)
    object.__setattr__(malformed, "reason_code", "collision_clear")
    object.__setattr__(malformed, "provenance", None)
    malformed_result = copy(_collision(CollisionStatus.CLEAR))
    object.__setattr__(malformed_result, "evaluations", (malformed,))
    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-clear",
            _limits(),
            malformed_result,
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.reason.identity == "collision:collision_result_inconsistent"


def test_unknown_collision_kind_clear_is_invalid_at_p5_boundary() -> None:
    malformed = object.__new__(CollisionEvaluation)
    object.__setattr__(malformed, "pair_id", "fore|upper")
    object.__setattr__(malformed, "kind", CollisionKind.UNKNOWN)
    object.__setattr__(malformed, "status", CollisionStatus.CLEAR)
    object.__setattr__(malformed, "distance_m", 0.1)
    object.__setattr__(malformed, "clearance_m", 0.01)
    object.__setattr__(malformed, "reason_code", "pair_clear")
    object.__setattr__(malformed, "provenance", "fixture-collision")
    malformed_result = copy(_collision(CollisionStatus.CLEAR))
    object.__setattr__(malformed_result, "evaluations", (malformed,))
    decision = evaluate_physical_safety(
        SafetyInput(
            "unknown-kind-clear",
            _limits(),
            malformed_result,
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.reason.identity == "collision:collision_result_inconsistent"


def test_wildcard_collision_pair_is_invalid_at_p5_boundary() -> None:
    malformed = object.__new__(CollisionEvaluation)
    object.__setattr__(malformed, "pair_id", "*|upper")
    object.__setattr__(malformed, "kind", CollisionKind.ENVIRONMENT_COLLISION)
    object.__setattr__(malformed, "status", CollisionStatus.CLEAR)
    object.__setattr__(malformed, "distance_m", 0.1)
    object.__setattr__(malformed, "clearance_m", 0.01)
    object.__setattr__(malformed, "reason_code", "pair_clear")
    object.__setattr__(malformed, "provenance", "fixture-collision")
    malformed_result = copy(_collision(CollisionStatus.CLEAR))
    object.__setattr__(malformed_result, "evaluations", (malformed,))
    decision = evaluate_physical_safety(
        SafetyInput(
            "wildcard-pair",
            _limits(),
            malformed_result,
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "collision:collision_result_inconsistent"


def test_malformed_collision_provenance_is_invalid_without_exception() -> None:
    result = _collision(CollisionStatus.CLEAR)
    malformed_evaluation = copy(result.evaluations[0])
    # test専用: constructorを迂回してprovenanceを不正型へ置き換える。
    object.__setattr__(malformed_evaluation, "provenance", object())
    malformed_result = copy(result)
    object.__setattr__(malformed_result, "evaluations", (malformed_evaluation,))

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-collision-provenance",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            malformed_result,
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "collision:collision_result_inconsistent"
    assert all(isinstance(item, str) for item in decision.reason.provenance)


def test_malformed_collision_result_member_is_invalid_without_exception() -> None:
    result = _collision(CollisionStatus.CLEAR)
    malformed_result = copy(result)
    # test専用: aggregate tupleへ非CollisionEvaluation memberを注入する。
    object.__setattr__(malformed_result, "evaluations", (object(),))

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-collision-member",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            malformed_result,
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "collision:collision_result_inconsistent"


def test_malformed_collision_result_shape_is_invalid_without_exception() -> None:
    result = _collision(CollisionStatus.CLEAR)
    malformed_result = copy(result)
    # test専用: aggregate evaluationsをtuple以外へ置き換える。
    object.__setattr__(malformed_result, "evaluations", object())

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-collision-result",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            malformed_result,
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "collision:collision_result_inconsistent"


@pytest.mark.parametrize(
    ("status", "diagnostics", "reason_code"),
    (
        (
            FeasibilityStatus.FEASIBLE,
            (FeasibilityDiagnostic("rejected_dynamic_limit", "unexpected rejection"),),
            "rejected_dynamic_limit",
        ),
        (
            FeasibilityStatus.REJECTED,
            (),
            "feasibility_clear",
        ),
    ),
)
def test_dynamic_aggregate_status_and_diagnostics_must_match(
    status: FeasibilityStatus,
    diagnostics: tuple[FeasibilityDiagnostic, ...],
    reason_code: str,
) -> None:
    dynamic = copy(_dynamic(FeasibilityStatus.FEASIBLE))
    object.__setattr__(dynamic, "status", status)
    object.__setattr__(dynamic, "reason_code", reason_code)
    object.__setattr__(dynamic, "diagnostics", diagnostics)
    decision = evaluate_physical_safety(
        SafetyInput(
            "inconsistent-dynamic",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"


def test_dynamic_limit_diagnostic_must_match_bound_evidence() -> None:
    dynamic = copy(_dynamic(FeasibilityStatus.UNKNOWN))
    object.__setattr__(dynamic, "reason_code", "unknown_limit_source")
    object.__setattr__(dynamic, "diagnostics", (FeasibilityDiagnostic("unknown_limit_source", "source is unknown"),))
    object.__setattr__(dynamic, "bound_statuses", (EvidenceStatus.AUTHORITATIVE,) * len(JOINTS))
    decision = evaluate_physical_safety(
        SafetyInput(
            "inconsistent-dynamic-evidence",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"


def test_authoritative_trajectory_dynamic_result_can_allow() -> None:
    decision = evaluate_physical_safety(
        SafetyInput(
            "valid-trajectory-dynamic",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            _trajectory_dynamic(),
        )
    )

    assert decision.action is SafetyDecisionAction.ALLOW
    assert decision.reason.identity == "limit:limit_resolution_authoritative"


def test_two_sample_unavailable_acceleration_result_remains_unavailable_at_p5() -> None:
    dynamic = TrajectoryFeasibilityResult(
        FeasibilityStatus.UNAVAILABLE,
        "unavailable_acceleration",
        2,
        (
            FeasibilityDiagnostic(
                "unavailable_acceleration",
                "at least three valid samples are required for finite-difference acceleration",
            ),
        ),
        ("fixture-trajectory-0", "fixture-trajectory-1"),
        (EvidenceStatus.AUTHORITATIVE,) * (2 * len(JOINTS)),
        JOINTS,
        POLICY_ID,
        POLICY_REVISION,
        tuple(f"fixture-limit-{index}" for index in range(2 * len(JOINTS))),
        tuple(f"fixture-evidence-{index}" for index in range(2 * len(JOINTS))),
        (False, False),
        (True, True),
        (),
    )
    decision = evaluate_physical_safety(
        SafetyInput(
            "two-sample-trajectory",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.UNAVAILABLE
    assert not decision.allowed


def test_two_sample_feasible_without_unavailable_acceleration_is_invalid_at_p5() -> None:
    dynamic = copy(_trajectory_dynamic())
    object.__setattr__(dynamic, "sample_count", 2)
    object.__setattr__(dynamic, "source_ids", ("fixture-trajectory-0", "fixture-trajectory-1"))
    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-two-sample-trajectory",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"


def test_empty_trajectory_source_ids_are_invalid_at_p5_boundary() -> None:
    dynamic = copy(_trajectory_dynamic())
    # test専用: constructorを迂回し、dynamic source identityを空集合へ置き換える。
    object.__setattr__(dynamic, "source_ids", ())

    decision = evaluate_physical_safety(
        SafetyInput(
            "empty-trajectory-sources",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"
    assert all(isinstance(item, str) for item in decision.reason.provenance)


@pytest.mark.parametrize("diagnostics", (object(), (object(),)))
def test_malformed_trajectory_diagnostics_are_invalid_without_exception(diagnostics: object) -> None:
    dynamic = copy(_trajectory_dynamic())
    # test専用: immutable resultのdiagnostics tuple/memberを壊す。
    object.__setattr__(dynamic, "diagnostics", diagnostics)

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-trajectory-diagnostics",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"


def test_malformed_trajectory_diagnostic_fields_are_invalid_without_exception() -> None:
    dynamic = copy(_trajectory_dynamic())
    diagnostic = copy(FeasibilityDiagnostic("rejected_fixture", "fixture rejection"))
    # test専用: constructorを迂回し、diagnosticの有限値を非有限値へ置き換える。
    object.__setattr__(diagnostic, "observed", float("nan"))
    object.__setattr__(dynamic, "diagnostics", (diagnostic,))

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-trajectory-diagnostic-fields",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"


def test_malformed_configuration_source_id_is_invalid_without_exception() -> None:
    dynamic = copy(_dynamic(FeasibilityStatus.FEASIBLE))
    # test専用: immutable configuration resultのsource identityを空文字へ置き換える。
    object.__setattr__(dynamic, "source_id", "")

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-configuration-source",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"
    assert all(isinstance(item, str) for item in decision.reason.provenance)


def test_malformed_dynamic_bound_statuses_are_invalid_without_exception() -> None:
    dynamic = copy(_trajectory_dynamic())
    # test専用: constructorを迂回し、bound evidence tupleを不正型へ置き換える。
    object.__setattr__(dynamic, "bound_statuses", object())

    decision = evaluate_physical_safety(
        SafetyInput(
            "malformed-dynamic-evidence",
            _limits(LimitResolutionStatus.RESOLVED_AUTHORITATIVE),
            _collision(CollisionStatus.CLEAR),
            dynamic,
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"


def test_near_collision_and_task_contact_are_hold_not_allow() -> None:
    near = evaluate_physical_safety(_input(collision=CollisionStatus.NEAR_COLLISION))
    assert near.action is SafetyDecisionAction.HOLD
    assert near.reason.reason_code == "near_collision_detected"

    contact = evaluate_physical_safety(_input(collision=CollisionStatus.CONTACT))
    assert contact.action is SafetyDecisionAction.HOLD
    assert contact.reason.reason_code == "task_object_contact"


def test_limit_expected_joint_deletion_never_allows() -> None:
    result = copy(_limits())
    object.__setattr__(result, "expected_joint_names", ("joint_a",))

    decision = evaluate_physical_safety(
        SafetyInput(
            "deleted-joint",
            result,
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE, expected_joint_names=("joint_a",)),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "limit:limit_resolution_inconsistent"


def test_synthetic_authoritative_parity_never_allows() -> None:
    result = _limits()
    bound = copy(result.bounds[0])
    parity = copy(bound.parity[0])
    synthetic_source = copy(parity.source)
    object.__setattr__(synthetic_source, "source_kind", "software_config")
    object.__setattr__(parity, "source", synthetic_source)
    object.__setattr__(bound, "parity", (parity,))
    malformed = copy(result)
    object.__setattr__(malformed, "bounds", (bound, result.bounds[1]))

    decision = evaluate_physical_safety(
        SafetyInput("synthetic-authority", malformed, _collision(CollisionStatus.CLEAR), _dynamic(FeasibilityStatus.FEASIBLE))
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed


def test_limit_source_value_disagreement_within_p2_tolerance_is_not_invalid() -> None:
    result = _limits(parity_delta=LIMIT_TOLERANCE_RAD / 2.0, tolerance=LIMIT_TOLERANCE_RAD)
    decision = evaluate_physical_safety(
        SafetyInput("within-tolerance", result, _collision(CollisionStatus.CLEAR), _dynamic(FeasibilityStatus.FEASIBLE))
    )

    assert decision.action is SafetyDecisionAction.ALLOW
    assert decision.reason.identity == "limit:limit_resolution_authoritative"


def test_collision_expected_pair_deletion_never_allows() -> None:
    result = copy(_collision(CollisionStatus.CLEAR))
    object.__setattr__(result, "evaluations", ())

    decision = evaluate_physical_safety(
        SafetyInput("deleted-collision-pair", _limits(), result, _dynamic(FeasibilityStatus.FEASIBLE))
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "collision:collision_result_inconsistent"


def test_collision_identity_tamper_is_invalid_without_exception() -> None:
    for field_name in ("robot_id", "model_id", "policy_id", "inventory_id"):
        result = _collision(CollisionStatus.CLEAR)
        context = copy(result.context)
        object.__setattr__(context, field_name, f"tampered-{field_name}")
        object.__setattr__(result, "context", context)

        decision = evaluate_physical_safety(
            SafetyInput("collision-identity-tamper", _limits(), result, _dynamic(FeasibilityStatus.FEASIBLE))
        )

        assert decision.action is SafetyDecisionAction.INVALID
        assert not decision.allowed
        if field_name == "robot_id":
            assert decision.candidate_id == "invalid-input"
            assert decision.reason.identity == "input:invalid_safety_input"
        else:
            assert decision.candidate_id == "collision-identity-tamper"
            assert decision.reason.identity == "collision:collision_result_inconsistent"


def test_dynamic_synthetic_feasible_never_allows() -> None:
    dynamic = copy(_dynamic(FeasibilityStatus.FEASIBLE))
    object.__setattr__(dynamic, "bound_evidence_ids", ())

    decision = evaluate_physical_safety(
        SafetyInput("synthetic-feasible", _limits(), _collision(CollisionStatus.CLEAR), dynamic)
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"


def test_dynamic_policy_fingerprint_tamper_never_allows() -> None:
    dynamic = copy(_dynamic(FeasibilityStatus.FEASIBLE))
    object.__setattr__(dynamic, "policy_fingerprint", ("forged-policy",))

    decision = evaluate_physical_safety(
        SafetyInput("synthetic-policy-fingerprint", _limits(), _collision(CollisionStatus.CLEAR), dynamic)
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "dynamic:dynamic_result_inconsistent"


def test_complete_provisional_dynamic_evidence_maps_to_hold() -> None:
    decision = evaluate_physical_safety(
        _input(dynamic_authoritative=False)
    )

    assert decision.action is SafetyDecisionAction.HOLD
    assert decision.reason.identity == "dynamic:dynamic_result_provisional"


def test_configuration_unavailable_qvel_remains_unavailable() -> None:
    decision = evaluate_physical_safety(
        _input(dynamic=FeasibilityStatus.UNAVAILABLE)
    )

    assert decision.action is SafetyDecisionAction.UNAVAILABLE
    assert decision.reason.identity == "dynamic:dynamic_result_unavailable"


def test_non_feasible_dynamic_result_may_omit_unavailable_limit_inventory() -> None:
    dynamic = ConfigurationFeasibilityResult(
        FeasibilityStatus.UNKNOWN,
        "unknown_limit_source",
        (FeasibilityDiagnostic("unknown_limit_source", "dynamic limit source is unavailable"),),
        "fixture-dynamic",
        (EvidenceStatus.UNKNOWN,),
        JOINTS,
        POLICY_ID,
        POLICY_REVISION,
        ("missing-limit-source",),
        ("missing-limit-evidence",),
        None,
        None,
    )

    decision = evaluate_physical_safety(
        SafetyInput("partial-dynamic-evidence", _limits(), _collision(CollisionStatus.CLEAR), dynamic)
    )

    assert decision.action is SafetyDecisionAction.UNAVAILABLE
    assert decision.reason.identity == "dynamic:dynamic_result_unknown"


def test_limit_collision_robot_identity_mismatch_is_invalid() -> None:
    decision = evaluate_physical_safety(
        SafetyInput(
            "robot-binding-mismatch",
            _limits(robot_id="limit-robot"),
            _collision(CollisionStatus.CLEAR),
            _dynamic(FeasibilityStatus.FEASIBLE),
        )
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.candidate_id == "invalid-input"
    assert decision.reason.identity == "input:invalid_safety_input"


def test_limit_dynamic_joint_inventory_mismatch_is_invalid() -> None:
    dynamic = copy(_dynamic(FeasibilityStatus.FEASIBLE))
    object.__setattr__(dynamic, "expected_joint_names", ("joint_a",))

    decision = evaluate_physical_safety(
        SafetyInput("joint-binding-mismatch", _limits(), _collision(CollisionStatus.CLEAR), dynamic)
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.candidate_id == "invalid-input"
    assert decision.reason.identity == "input:invalid_safety_input"


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("candidate_id", ""),
        ("candidate_id", object()),
        ("provenance", object()),
        ("provenance", ("duplicate", "duplicate")),
    ),
)
def test_malformed_safety_input_fields_return_invalid_without_exception(
    field_name: str,
    value: object,
) -> None:
    malformed = _input()
    object.__setattr__(malformed, field_name, value)

    decision = evaluate_physical_safety(malformed)

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.candidate_id == "invalid-input"
    assert decision.reason.identity == "input:invalid_safety_input"


def test_constructor_bypassed_nested_dynamic_dto_returns_invalid_without_exception() -> None:
    malformed_dynamic = object.__new__(ConfigurationFeasibilityResult)
    malformed = _input()
    object.__setattr__(malformed, "dynamic", malformed_dynamic)

    decision = evaluate_physical_safety(malformed)

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.candidate_id == "invalid-input"
    assert decision.reason.identity == "input:invalid_safety_input"


def test_bounded_sampling_stops_at_first_non_allow() -> None:
    result = evaluate_bounded_safety_samples(
        (
            _input(candidate_id="sample-0"),
            _input(candidate_id="sample-1", dynamic=FeasibilityStatus.REJECTED),
            _input(candidate_id="sample-2", dynamic=FeasibilityStatus.INVALID),
        )
    )

    assert result.first_non_allow_index == 1
    assert len(result.decisions) == 2
    assert result.action is SafetyDecisionAction.REJECT


def test_invalid_empty_sampling_input_is_explicit() -> None:
    result = evaluate_bounded_safety_samples(())

    assert result.first_non_allow_index == 0
    assert result.action is SafetyDecisionAction.INVALID


def test_safety_decision_constructor_and_public_validator_bind_aggregate() -> None:
    valid = evaluate_physical_safety(_input())
    assert validate_safety_decision(valid) is valid

    with pytest.raises(ValueError, match="highest-priority assessment"):
        SafetyDecision(
            valid.candidate_id,
            SafetyDecisionAction.REJECT,
            valid.reason,
            valid.assessments,
            valid.provenance,
        )

    tampered = copy(valid)
    object.__setattr__(tampered, "action", SafetyDecisionAction.REJECT)
    assert not tampered.allowed
    with pytest.raises(ValueError):
        validate_safety_decision(tampered)

    nested_tampered = copy(valid)
    reason = copy(nested_tampered.reason)
    object.__setattr__(reason, "provenance", ("tampered-provenance",))
    object.__setattr__(nested_tampered, "reason", reason)
    assert not nested_tampered.allowed

    assessment_tampered = copy(valid)
    assessment = copy(assessment_tampered.assessments[0])
    object.__setattr__(assessment, "action", SafetyDecisionAction.REJECT)
    object.__setattr__(assessment_tampered, "assessments", (assessment, *assessment_tampered.assessments[1:]))
    assert not assessment_tampered.allowed


def test_canonical_allow_dtos_require_evaluator_composition_origin() -> None:
    valid = evaluate_physical_safety(_input())
    limit_reason = valid.assessments[0].reason

    with pytest.raises(ValueError, match="composition origin"):
        SafetyComponentAssessment(
            SafetyComponent.LIMIT,
            SafetyDecisionAction.ALLOW,
            limit_reason,
        )
    with pytest.raises(ValueError, match="composition origin"):
        SafetyDecision(
            valid.candidate_id,
            SafetyDecisionAction.ALLOW,
            valid.reason,
            valid.assessments,
            valid.provenance,
        )


def test_same_semantic_nested_reason_replacement_cannot_remain_allow() -> None:
    valid = evaluate_physical_safety(_input())
    replacement = copy(valid.reason)
    malformed = copy(valid)
    object.__setattr__(malformed, "reason", replacement)

    assert not malformed.allowed
    with pytest.raises(ValueError):
        validate_safety_decision(malformed)


@pytest.mark.parametrize(
    ("action", "reason_identity", "provenance"),
    (
        (
            SafetyDecisionAction.ALLOW,
            "limit:limit_resolution_authoritative",
            ("limit-source",),
        ),
        (
            SafetyDecisionAction.HOLD,
            "limit:limit_resolution_authoritative",
            ("limit-source",),
        ),
    ),
)
def test_public_safety_projection_uses_single_canonical_mapping(
    action: SafetyDecisionAction,
    reason_identity: str,
    provenance: tuple[str, ...],
) -> None:
    if action is SafetyDecisionAction.ALLOW:
        assert validate_safety_projection(action, reason_identity, provenance) == (
            action,
            reason_identity,
            provenance,
        )
    else:
        with pytest.raises(ValueError, match="canonical reason mapping"):
            validate_safety_projection(action, reason_identity, provenance)


def test_public_safety_projection_rejects_unknown_or_empty_allow_evidence() -> None:
    with pytest.raises(ValueError, match="unknown"):
        validate_safety_projection(
            SafetyDecisionAction.ALLOW,
            "limit:arbitrary_reason",
            ("limit-source",),
        )
    with pytest.raises(ValueError, match="concrete provenance"):
        validate_safety_projection(
            SafetyDecisionAction.ALLOW,
            "limit:limit_resolution_authoritative",
            (),
        )


def test_bounded_sampling_constructor_and_properties_fail_closed_after_tamper() -> None:
    valid = evaluate_bounded_safety_samples(
        (_input(candidate_id="sample-0"), _input(candidate_id="sample-1", dynamic=FeasibilityStatus.REJECTED))
    )
    assert validate_bounded_safety_sampling_result(valid) is valid
    assert valid.reason is valid.decisions[valid.first_non_allow_index].reason
    assert valid.provenance == valid.decisions[valid.first_non_allow_index].provenance

    with pytest.raises(ValueError, match="first non-allow"):
        BoundedSafetySamplingResult(valid.decisions, None)

    tampered = copy(valid)
    object.__setattr__(tampered, "first_non_allow_index", 0)
    assert tampered.action is SafetyDecisionAction.INVALID
    with pytest.raises(ValueError):
        validate_bounded_safety_sampling_result(tampered)

    nested_tampered = copy(valid)
    decision = copy(nested_tampered.decisions[1])
    object.__setattr__(decision, "action", SafetyDecisionAction.ALLOW)
    object.__setattr__(nested_tampered, "decisions", (nested_tampered.decisions[0], decision))
    assert nested_tampered.action is SafetyDecisionAction.INVALID


@pytest.mark.parametrize(
    ("component", "reason_code"),
    (
        (SafetyComponent.LIMIT, "limit_resolution_unavailable"),
        (SafetyComponent.COLLISION, "collision_result_unavailable"),
        (SafetyComponent.DYNAMIC, "dynamic_result_unavailable"),
    ),
)
def test_component_assessment_rejects_synthetic_allow_for_unavailable_reason(
    component: SafetyComponent,
    reason_code: str,
) -> None:
    reason = SafetyReason(reason_code, component, "evidence is unavailable")

    with pytest.raises(ValueError, match="canonical reason mapping"):
        SafetyComponentAssessment(component, SafetyDecisionAction.ALLOW, reason)


def test_component_assessment_rejects_unknown_reason_code() -> None:
    reason = SafetyReason("arbitrary_reason", SafetyComponent.COLLISION, "arbitrary")

    with pytest.raises(ValueError, match="unknown for component"):
        SafetyComponentAssessment(
            SafetyComponent.COLLISION,
            SafetyDecisionAction.ALLOW,
            reason,
        )


def test_safety_reason_identity_revalidates_direct_tamper_and_bypass() -> None:
    reason = SafetyReason("arbitrary_reason", SafetyComponent.COLLISION, "arbitrary")
    original = {
        "reason_code": reason.reason_code,
        "component": reason.component,
        "operator_message": reason.operator_message,
        "provenance": reason.provenance,
    }

    for field_name, value in (
        ("reason_code", "tampered_reason"),
        ("component", SafetyComponent.LIMIT),
        ("provenance", ("tampered-provenance",)),
    ):
        object.__setattr__(reason, field_name, value)
        with pytest.raises((TypeError, ValueError)):
            _ = reason.identity
        object.__setattr__(reason, field_name, original[field_name])
        assert reason.identity == "collision:arbitrary_reason"

    object.__setattr__(reason, "reason_code", "tampered_reason")
    object.__setattr__(
        reason,
        "_binding_fingerprint",
        ("tampered_reason", reason.component, reason.operator_message, reason.provenance),
    )
    with pytest.raises(ValueError):
        _ = reason.identity

    bypassed = object.__new__(SafetyReason)
    with pytest.raises((TypeError, ValueError)):
        _ = bypassed.identity


@pytest.mark.parametrize(
    ("component", "reason_code"),
    (
        (SafetyComponent.LIMIT, "limit_resolution_authoritative"),
        (SafetyComponent.COLLISION, "collision_clear"),
        (SafetyComponent.DYNAMIC, "dynamic_feasibility_clear"),
    ),
)
def test_canonical_allow_assessment_requires_concrete_provenance(
    component: SafetyComponent,
    reason_code: str,
) -> None:
    with pytest.raises(ValueError, match="composition origin"):
        SafetyReason(reason_code, component, "evidence is clear", ("evidence",))


def test_direct_all_allow_decision_rejects_bypassed_unavailable_assessments() -> None:
    assessments = []
    for component in (
        SafetyComponent.LIMIT,
        SafetyComponent.COLLISION,
        SafetyComponent.DYNAMIC,
    ):
        reason = SafetyReason(
            {
                SafetyComponent.LIMIT: "limit_resolution_unavailable",
                SafetyComponent.COLLISION: "collision_result_unavailable",
                SafetyComponent.DYNAMIC: "dynamic_result_unavailable",
            }[component],
            component,
            "evidence is unavailable",
        )
        assessment = object.__new__(SafetyComponentAssessment)
        object.__setattr__(assessment, "component", component)
        object.__setattr__(assessment, "action", SafetyDecisionAction.ALLOW)
        object.__setattr__(assessment, "reason", reason)
        assessments.append(assessment)

    with pytest.raises(ValueError, match="canonical reason mapping"):
        SafetyDecision(
            "synthetic-all-allow",
            SafetyDecisionAction.ALLOW,
            SafetyReason(
                "limit_resolution_unavailable",
                SafetyComponent.LIMIT,
                "evidence is unavailable",
            ),
            tuple(assessments),
            (),
        )


def test_bounded_sampling_rejects_trailing_decisions_after_first_non_allow() -> None:
    allow = evaluate_physical_safety(_input(candidate_id="sample-allow"))
    reject = evaluate_physical_safety(
        _input(candidate_id="sample-reject", dynamic=FeasibilityStatus.REJECTED)
    )

    with pytest.raises(ValueError, match="stop at first non-allow"):
        BoundedSafetySamplingResult((allow, reject, allow), 1)


def test_safety_input_rejects_nested_dto_replacement_after_construction() -> None:
    malformed = _input()
    object.__setattr__(malformed, "dynamic", _dynamic(FeasibilityStatus.FEASIBLE))

    decision = evaluate_physical_safety(malformed)

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.candidate_id == "invalid-input"
    assert decision.reason.identity == "input:invalid_safety_input"


def test_safety_decision_rejects_reordered_assessments() -> None:
    valid = evaluate_physical_safety(_input())

    with pytest.raises(ValueError, match="canonical component order"):
        SafetyDecision(
            valid.candidate_id,
            valid.action,
            valid.reason,
            tuple(reversed(valid.assessments)),
            valid.provenance,
        )


def test_bounded_sampling_rejects_non_exact_first_index_type() -> None:
    allow = evaluate_physical_safety(_input(candidate_id="first-index-allow"))
    reject = evaluate_physical_safety(
        _input(candidate_id="first-index-reject", dynamic=FeasibilityStatus.REJECTED)
    )

    for malformed_index in (True, 1.0):
        with pytest.raises((TypeError, ValueError), match="first_non_allow_index"):
            BoundedSafetySamplingResult((allow, reject), malformed_index)


def test_safety_input_external_seal_rejects_coherent_private_rewrite() -> None:
    malformed = _input()
    object.__setattr__(malformed, "candidate_id", "rewritten-candidate")
    nested_ids = tuple(
        id(nested) if nested is not None else None
        for nested in (malformed.limit_resolution, malformed.collision, malformed.dynamic)
    )
    object.__setattr__(
        malformed,
        "_binding_fingerprint",
        (malformed.candidate_id, malformed.provenance, nested_ids),
    )

    decision = evaluate_physical_safety(malformed)

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.reason.identity == "input:invalid_safety_input"


def test_constructor_bypassed_safety_input_is_invalid_without_exception() -> None:
    malformed = object.__new__(SafetyInput)

    decision = evaluate_physical_safety(malformed)

    assert decision.action is SafetyDecisionAction.INVALID
    assert decision.reason.identity == "input:invalid_safety_input"


def test_public_safety_input_validator_deep_revalidates_nested_result() -> None:
    malformed = _input()
    limit_result = copy(malformed.limit_resolution)
    object.__setattr__(limit_result, "expected_joint_names", ("joint_a",))
    object.__setattr__(malformed, "limit_resolution", limit_result)

    with pytest.raises(ValueError):
        validate_safety_input(malformed)


def test_safety_decision_external_seal_rejects_coherent_private_rewrite() -> None:
    valid = evaluate_physical_safety(_input())
    malformed = copy(valid)
    object.__setattr__(malformed, "candidate_id", "rewritten-decision")
    object.__setattr__(
        malformed,
        "_binding_fingerprint",
        (
            malformed.candidate_id,
            malformed.action,
            malformed.reason._binding_fingerprint,
            tuple(item._binding_fingerprint for item in malformed.assessments),
            malformed.provenance,
        ),
    )

    assert not malformed.allowed
    with pytest.raises(ValueError):
        validate_safety_decision(malformed)


def test_constructor_bypassed_safety_decision_and_bounded_result_fail_closed() -> None:
    malformed_decision = object.__new__(SafetyDecision)
    assert not malformed_decision.allowed
    with pytest.raises(Exception):
        validate_safety_decision(malformed_decision)

    malformed_bounded = object.__new__(BoundedSafetySamplingResult)
    assert malformed_bounded.action is SafetyDecisionAction.INVALID
    with pytest.raises(Exception):
        validate_bounded_safety_sampling_result(malformed_bounded)


def test_collision_near_to_clear_nested_tamper_is_invalid_at_p5_boundary() -> None:
    collision = _collision(CollisionStatus.NEAR_COLLISION)
    malformed_evaluation = copy(collision.evaluations[0])
    object.__setattr__(malformed_evaluation, "status", CollisionStatus.CLEAR)
    object.__setattr__(malformed_evaluation, "distance_m", 0.1)
    object.__setattr__(malformed_evaluation, "reason_code", "pair_clear")
    malformed = copy(collision)
    object.__setattr__(malformed, "evaluations", (malformed_evaluation,))

    decision = evaluate_physical_safety(
        SafetyInput("nested-collision-tamper", _limits(), malformed, _dynamic(FeasibilityStatus.FEASIBLE))
    )

    assert decision.action is SafetyDecisionAction.INVALID
    assert not decision.allowed
    assert decision.reason.identity == "collision:collision_result_inconsistent"
