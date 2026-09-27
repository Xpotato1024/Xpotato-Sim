"""Physical-safety-core composition for bounded software decisions.

P2 limit resolution、P3 collision、P4 dynamic feasibilityを一つのimmutable decisionへ
composeする。ここは個別checkerの内部を再実装せず、unknown / unavailable / invalidを
allowへfallbackしないphysical-output前のruntime boundaryである。
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, field
from enum import Enum
import weakref
from threading import RLock

from xpotato_sim.runtime.safety.collision_policy import (
    CollisionContractViolation,
    CollisionContext,
    CollisionCheckResult,
    CollisionEvaluation,
    CollisionKind,
    CollisionStatus,
    validate_collision_check_result,
    validate_collision_context,
    validate_collision_evaluation,
)
from xpotato_sim.runtime.safety.limit_resolution import (
    JointSpaceConversion,
    LimitParityRecord,
    LimitResolutionResult,
    LimitResolutionStatus,
    ParityStatus,
    ResolvedJointBound,
    authoritative_position_bound_violations,
    validate_limit_resolution_result,
)
from xpotato_sim.runtime.safety.trajectory_feasibility import (
    ConfigurationFeasibilityResult,
    FeasibilityDiagnostic,
    FeasibilityStatus,
    TrajectoryFeasibilityResult,
    VelocityEvidenceBinding,
    VelocityEvidenceKind,
    validate_configuration_feasibility_result,
    validate_trajectory_feasibility_result,
)
from xpotato_sim.runtime.safety.physical_limits import (
    EvidenceStatus,
    LimitSourceProvenance,
    LimitSpace,
)


class SafetyDecisionAction(str, Enum):
    """physical output前のclosed action vocabulary。"""

    ALLOW = "allow"
    HOLD = "hold"
    REJECT = "reject"
    STOP = "stop"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class SafetyComponent(str, Enum):
    """decisionを発生させたchecker owner。"""

    LIMIT = "limit"
    COLLISION = "collision"
    DYNAMIC = "dynamic"
    INPUT = "input"


# P5のauthorityはDTOのprivate fingerprintだけに依存しない。Pythonのpublic
# dataclass fieldsとprivate hintを同時に改変するcallerでも更新できない、owner-local
# weak identity sealを保持する。registryはweakref callbackで掃除し、id再利用時は
# 必ずreference identityも照合する。
_P5_SEALS: dict[
    int, tuple[weakref.ReferenceType[object], tuple[object, ...]]
] = {}
_P5_SEALS_LOCK = RLock()

# canonical allow DTOはpublic constructorを直接authority sourceにしない。
# composition factoryだけがこのprivate tokenを短時間設定し、生成物は別の
# weak identity registryへ登録する。tokenやprivate fingerprintをcallerが
# 書き換えても、origin registryは更新されない。
_P5_CONSTRUCTION_CONTEXT: ContextVar[object | None] = ContextVar(
    "_P5_CONSTRUCTION_CONTEXT",
    default=None,
)
_P5_CONSTRUCTION_TOKEN = object()
_P5_ORIGINS: dict[int, tuple[weakref.ReferenceType[object], str]] = {}
_P5_ORIGINS_LOCK = RLock()


def _release_p5_seal(
    key: int,
    reference: weakref.ReferenceType[object],
) -> None:
    with _P5_SEALS_LOCK:
        entry = _P5_SEALS.get(key)
        if entry is not None and entry[0] is reference:
            _P5_SEALS.pop(key, None)


def _register_p5_seal(value: object, snapshot: tuple[object, ...]) -> None:
    key = id(value)
    reference = weakref.ref(
        value,
        lambda ref, key=key: _release_p5_seal(key, ref),
    )
    with _P5_SEALS_LOCK:
        _P5_SEALS[key] = (reference, snapshot)


def _validate_p5_seal(value: object, snapshot: tuple[object, ...]) -> None:
    key = id(value)
    with _P5_SEALS_LOCK:
        entry = _P5_SEALS.get(key)
        if entry is None or entry[0]() is not value or entry[1] != snapshot:
            raise ValueError("physical safety DTO is not constructor-sealed")


def _release_p5_origin(
    key: int,
    reference: weakref.ReferenceType[object],
) -> None:
    with _P5_ORIGINS_LOCK:
        entry = _P5_ORIGINS.get(key)
        if entry is not None and entry[0] is reference:
            _P5_ORIGINS.pop(key, None)


def _register_p5_origin(value: object, kind: str) -> None:
    key = id(value)
    reference = weakref.ref(
        value,
        lambda ref, key=key: _release_p5_origin(key, ref),
    )
    with _P5_ORIGINS_LOCK:
        _P5_ORIGINS[key] = (reference, kind)


def _validate_p5_origin(value: object, kind: str) -> None:
    key = id(value)
    with _P5_ORIGINS_LOCK:
        entry = _P5_ORIGINS.get(key)
        if entry is None or entry[0]() is not value or entry[1] != kind:
            raise ValueError(f"{kind} canonical allow value lacks composition origin")


@contextmanager
def _p5_construction_scope():
    token = _P5_CONSTRUCTION_CONTEXT.set(_P5_CONSTRUCTION_TOKEN)
    try:
        yield
    finally:
        _P5_CONSTRUCTION_CONTEXT.reset(token)


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SafetyReason:
    """machine reasonとoperator messageを同一identityへ束ねる。"""

    reason_code: str
    component: SafetyComponent
    operator_message: str
    provenance: tuple[str, ...] = ()
    _binding_fingerprint: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _text("reason_code", self.reason_code)
        if not isinstance(self.component, SafetyComponent):
            object.__setattr__(self, "component", SafetyComponent(self.component))
        _text("operator_message", self.operator_message)
        if not isinstance(self.provenance, tuple) or not all(
            isinstance(item, str) and item == item.strip() and item for item in self.provenance
        ):
            raise TypeError("provenance must contain non-empty strings")
        if len(set(self.provenance)) != len(self.provenance):
            raise ValueError("provenance must be unique")
        _validate_safety_reason(self, initialize=True)

    @property
    def identity(self) -> str:
        _validate_safety_reason(self)
        return f"{self.component.value}:{self.reason_code}"


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SafetyInput:
    """1 candidate state / bounded trajectoryのchecker outputs。"""

    candidate_id: str
    limit_resolution: LimitResolutionResult | None
    collision: CollisionCheckResult | None
    dynamic: ConfigurationFeasibilityResult | TrajectoryFeasibilityResult | None
    provenance: tuple[str, ...] = ()
    _binding_fingerprint: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _text("candidate_id", self.candidate_id)
        if self.limit_resolution is not None and not isinstance(self.limit_resolution, LimitResolutionResult):
            raise TypeError("limit_resolution must be LimitResolutionResult or None")
        if self.collision is not None and not isinstance(self.collision, CollisionCheckResult):
            raise TypeError("collision must be CollisionCheckResult or None")
        if self.dynamic is not None and not isinstance(
            self.dynamic, (ConfigurationFeasibilityResult, TrajectoryFeasibilityResult)
        ):
            raise TypeError("dynamic must be a P4 feasibility result or None")
        if not isinstance(self.provenance, tuple) or not all(
            isinstance(item, str) and item == item.strip() and item for item in self.provenance
        ):
            raise TypeError("provenance must contain non-empty strings")
        if len(set(self.provenance)) != len(self.provenance):
            raise ValueError("provenance must be unique")
        _validate_safety_input_contract(self, initialize=True)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SafetyComponentAssessment:
    """各ownerのstatus/actionとreason。"""

    component: SafetyComponent
    action: SafetyDecisionAction
    reason: SafetyReason
    _binding_fingerprint: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.component, SafetyComponent):
            object.__setattr__(self, "component", SafetyComponent(self.component))
        if not isinstance(self.action, SafetyDecisionAction):
            object.__setattr__(self, "action", SafetyDecisionAction(self.action))
        if not isinstance(self.reason, SafetyReason):
            raise TypeError("reason must be SafetyReason")
        _validate_safety_component_assessment(self, initialize=True)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SafetyDecision:
    """P2 / P3 / P4をcomposeしたimmutable machine/operator decision。"""

    candidate_id: str
    action: SafetyDecisionAction
    reason: SafetyReason
    assessments: tuple[SafetyComponentAssessment, ...]
    provenance: tuple[str, ...]
    _binding_fingerprint: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _text("candidate_id", self.candidate_id)
        if not isinstance(self.action, SafetyDecisionAction):
            object.__setattr__(self, "action", SafetyDecisionAction(self.action))
        if not isinstance(self.reason, SafetyReason):
            raise TypeError("reason must be SafetyReason")
        if not isinstance(self.assessments, tuple) or not all(
            isinstance(item, SafetyComponentAssessment) for item in self.assessments
        ):
            raise TypeError("assessments must contain SafetyComponentAssessment values")
        if not isinstance(self.provenance, tuple) or not all(
            isinstance(item, str) and item for item in self.provenance
        ):
            raise TypeError("provenance must contain non-empty strings")
        _validate_safety_decision(self, initialize=True)

    @property
    def allowed(self) -> bool:
        try:
            _validate_safety_decision(self)
        except Exception:
            return False
        return self.action is SafetyDecisionAction.ALLOW


@dataclass(frozen=True, slots=True, weakref_slot=True)
class BoundedSafetySamplingResult:
    """有限candidate sequenceのfirst non-allow decision。"""

    decisions: tuple[SafetyDecision, ...]
    first_non_allow_index: int | None
    reason: SafetyReason | None = None
    provenance: tuple[str, ...] = ()
    _binding_fingerprint: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _validate_bounded_safety_result(self, initialize=True)

    @property
    def action(self) -> SafetyDecisionAction:
        try:
            _validate_bounded_safety_result(self)
        except Exception:
            return SafetyDecisionAction.INVALID
        selected = (
            self.decisions[-1]
            if self.first_non_allow_index is None
            else self.decisions[self.first_non_allow_index]
        )
        return selected.action

    @property
    def allowed(self) -> bool:
        return self.action is SafetyDecisionAction.ALLOW


def _reason(
    component: SafetyComponent,
    reason_code: str,
    message: str,
    provenance: Sequence[str] = (),
) -> SafetyReason:
    return SafetyReason(reason_code, component, message, tuple(sorted(set(provenance))))


def _valid_text(value: object) -> bool:
    """壊れたprovider objectから安全に使える文字列identityだけを通す。"""

    try:
        return type(value) is str and bool(value) and value == value.strip()
    except Exception:
        return False


def _valid_identity(value: object) -> bool:
    """P3/P4が要求するplaceholderでないidentityを安全に検査する。"""

    placeholders = {
        "n-a",
        "n/a",
        "na",
        "n_a",
        "nil",
        "none",
        "not-applicable",
        "not_available",
        "null",
        "placeholder",
        "unknown",
        "unavailable",
    }
    try:
        return _valid_text(value) and value.casefold() not in placeholders
    except Exception:
        return False


def _safe_text_tuple(value: object) -> tuple[str, ...]:
    """tupleの内容を例外なく走査し、妥当な文字列だけを返す。"""

    if type(value) is not tuple:
        return ()
    try:
        return tuple(item for item in value if _valid_text(item))
    except Exception:
        return ()


def _safe_limit_provenance(result: object) -> tuple[str, ...]:
    """malformed boundからのprovenance参照をfail-closedにする。"""

    try:
        bounds = result.bounds
    except Exception:
        return ()
    if type(bounds) is not tuple:
        return ()
    provenance: list[str] = []
    try:
        for bound in bounds:
            source_names = bound.source_names
            provenance.extend(_safe_text_tuple(source_names))
    except Exception:
        return tuple(provenance)
    return tuple(provenance)


def _safe_collision_provenance(result: object) -> tuple[str, ...]:
    """malformed collision evaluationからのprovenance参照をfail-closedにする。"""

    try:
        evaluations = result.evaluations
    except Exception:
        return ()
    if type(evaluations) is not tuple:
        return ()
    provenance: list[str] = []
    try:
        for evaluation in evaluations:
            value = evaluation.provenance
            if _valid_text(value):
                provenance.append(value)
    except Exception:
        return tuple(provenance)
    return tuple(provenance)


def _safe_dynamic_provenance(result: object) -> tuple[str, ...]:
    """malformed dynamic resultからのprovenance参照をfail-closedにする。"""

    try:
        if type(result) is TrajectoryFeasibilityResult:
            return _safe_text_tuple(result.source_ids)
        if type(result) is ConfigurationFeasibilityResult:
            source_id = result.source_id
            return (source_id,) if _valid_text(source_id) else ()
    except Exception:
        return ()
    return ()


def _reason_semantic_snapshot(reason: SafetyReason) -> tuple[object, ...]:
    """SafetyReasonのpublic semantic contentを外部seal用に固定する。"""

    return (
        reason.reason_code,
        reason.component,
        reason.operator_message,
        reason.provenance,
    )


def _nested_result_semantic_snapshot(value: object) -> tuple[object, ...]:
    """P2/P3/P4 nested DTOのidentityに依存しない最小semantic seal。"""

    try:
        if type(value) is LimitResolutionResult:
            return (
                "limit",
                value.robot_id,
                value.expected_joint_names,
                tuple(
                    (
                        id(bound),
                        bound.joint_name,
                        bound.status,
                        bound.lower_rad,
                        bound.upper_rad,
                        bound.source_names,
                    )
                    for bound in value.bounds
                ),
                tuple(
                    (
                        id(relation),
                        relation.source_space,
                        relation.joint_name,
                        relation.source_name,
                        relation.relation_id,
                    )
                    for relation in value.conversion_relations
                ),
            )
        if type(value) is CollisionCheckResult:
            context = value.context
            return (
                "collision",
                id(context),
                context.robot_id,
                context.model_id,
                context.policy_id,
                context.inventory_id,
                context.expected_pair_ids,
                value.status,
                tuple(
                    (
                        id(evaluation),
                        evaluation.pair_id,
                        evaluation.kind,
                        evaluation.status,
                        evaluation.distance_m,
                        evaluation.reason_code,
                        evaluation.provenance,
                    )
                    for evaluation in value.evaluations
                ),
            )
        if type(value) is ConfigurationFeasibilityResult:
            return (
                "configuration",
                value.status,
                value.reason_code,
                value.source_id,
                value.expected_joint_names,
                value.policy_id,
                value.policy_revision,
                value.limit_source_ids,
                value.bound_statuses,
                value.bound_evidence_ids,
            )
        if type(value) is TrajectoryFeasibilityResult:
            return (
                "trajectory",
                value.status,
                value.reason_code,
                value.sample_count,
                value.source_ids,
                value.expected_joint_names,
                value.policy_id,
                value.policy_revision,
                value.limit_source_ids,
                value.bound_statuses,
                value.bound_evidence_ids,
                tuple(
                    (id(evidence), evidence.kind, evidence.sample_index, evidence.source_id)
                    for evidence in value.velocity_evidence
                ),
            )
    except Exception:
        return ("unreadable", type(value))
    return ("unsupported", type(value))


def _input_semantic_snapshot(value: SafetyInput) -> tuple[object, ...]:
    """SafetyInputの候補/provenance/nested object identityを外部sealする。"""

    return (
        value.candidate_id,
        value.provenance,
        tuple(
            (
                id(nested),
                _nested_result_semantic_snapshot(nested),
            )
            if nested is not None
            else None
            for nested in (value.limit_resolution, value.collision, value.dynamic)
        ),
    )


def _assessment_semantic_snapshot(
    assessment: SafetyComponentAssessment,
) -> tuple[object, ...]:
    return (
        assessment.component,
        assessment.action,
        (id(assessment.reason), _reason_semantic_snapshot(assessment.reason)),
    )


def _decision_semantic_snapshot(decision: SafetyDecision) -> tuple[object, ...]:
    return (
        decision.candidate_id,
        decision.action,
        (id(decision.reason), _reason_semantic_snapshot(decision.reason)),
        tuple(
            (id(item), _assessment_semantic_snapshot(item))
            for item in decision.assessments
        ),
        decision.provenance,
    )


def _bounded_semantic_snapshot(
    result: BoundedSafetySamplingResult,
) -> tuple[object, ...]:
    return (
        tuple(
            (id(item), _decision_semantic_snapshot(item))
            for item in result.decisions
        ),
        result.first_non_allow_index,
        (
            (id(result.reason), _reason_semantic_snapshot(result.reason))
            if result.reason is not None
            else None
        ),
        result.provenance,
    )


def _invoke_post_init(
    value: object,
    post_init: object,
    failure: str,
) -> str | None:
    """既存DTOのinvariantをcopy上で実行し、入力objectを変更しない。"""

    try:
        post_init(copy(value))  # type: ignore[operator]
    except Exception:
        return failure
    return None


def _limit_result_inconsistency(result: LimitResolutionResult) -> str | None:
    """P2のDTO invariantを再利用し、P5固有のtyped boundaryだけを確認する。"""

    if type(result) is not LimitResolutionResult:
        return "limit resolution result has an invalid type"
    try:
        validated = validate_limit_resolution_result(result)
    except Exception:
        return "limit resolution result failed canonical validation"
    if validated is not result:
        return "limit resolution validator returned a different result"
    try:
        schema_version = result.schema_version
        robot_id = result.robot_id
        bounds = result.bounds
        conversion_relations = result.conversion_relations
        expected_joint_names = result.expected_joint_names
    except Exception:
        return "limit resolution result is structurally incomplete"

    # P2 constructorの暗黙変換をP5で許可せず、typed status / identityだけを受け付ける。
    if type(schema_version) is not int or schema_version != 1:
        return "limit resolution schema version is invalid"
    if not _valid_identity(robot_id):
        return "limit resolution robot identity is invalid"
    if type(expected_joint_names) is not tuple or not expected_joint_names:
        return "limit resolution expected joint inventory is invalid"
    if any(not _valid_identity(name) for name in expected_joint_names):
        return "limit resolution expected joint identity is invalid"
    if len(set(expected_joint_names)) != len(expected_joint_names):
        return "limit resolution expected joint identities are duplicated"
    if type(bounds) is not tuple or not bounds:
        return "limit resolution bounds must be a non-empty tuple"
    if any(type(bound) is not ResolvedJointBound for bound in bounds):
        return "limit resolution contains an invalid bound"
    if type(conversion_relations) is not tuple:
        return "limit resolution conversion relations must be a tuple"
    if any(type(relation) is not JointSpaceConversion for relation in conversion_relations):
        return "limit resolution contains an invalid conversion relation"

    for relation in conversion_relations:
        try:
            if type(relation.source_space) is not LimitSpace:
                return "limit resolution conversion relation source space is invalid"
        except Exception:
            return "limit resolution conversion relation is structurally incomplete"
        failure = _invoke_post_init(
            relation,
            JointSpaceConversion.__post_init__,
            "limit resolution conversion relation is invalid",
        )
        if failure is not None:
            return failure

    bound_names: list[str] = []
    for bound in bounds:
        try:
            joint_name = bound.joint_name
            status = bound.status
            source_names = bound.source_names
            parity = bound.parity
        except Exception:
            return "limit resolution bound is structurally incomplete"
        if not _valid_identity(joint_name):
            return "limit resolution contains an invalid joint identity"
        bound_names.append(joint_name)
        if type(status) is not LimitResolutionStatus:
            return f"limit status is invalid for {joint_name}"
        if type(source_names) is not tuple or not source_names:
            return f"limit source identity is empty for {joint_name}"
        if any(not _valid_identity(name) for name in source_names):
            return f"limit source identity is invalid for {joint_name}"
        if len(set(source_names)) != len(source_names):
            return f"limit source identity is duplicated for {joint_name}"
        if type(parity) is not tuple or not parity:
            return f"limit parity is empty for {joint_name}"
        if any(type(item) is not LimitParityRecord for item in parity):
            return f"limit parity contains an invalid record for {joint_name}"
        if len(source_names) != len(parity):
            return f"limit source/parity coverage is incomplete for {joint_name}"

        for item in parity:
            try:
                item_joint_name = item.joint_name
                item_source_name = item.source_name
                item_status = item.status
                source = item.source
                source_status = item.source_status
            except Exception:
                return f"limit parity is structurally incomplete for {joint_name}"
            if not _valid_identity(item_joint_name) or item_joint_name != joint_name:
                return f"limit parity joint identity does not match {joint_name}"
            if not _valid_identity(item_source_name):
                return f"limit parity source identity is invalid for {joint_name}"
            if type(item_status) is not ParityStatus:
                return f"limit parity status is invalid for {joint_name}"
            if source is None or type(source) is not LimitSourceProvenance:
                return f"limit parity provenance is invalid for {joint_name}"
            try:
                if type(source.status) is not EvidenceStatus:
                    return f"limit parity provenance status is invalid for {joint_name}"
            except Exception:
                return f"limit parity provenance is structurally incomplete for {joint_name}"
            failure = _invoke_post_init(
                source,
                LimitSourceProvenance.__post_init__,
                f"limit parity provenance is invalid for {joint_name}",
            )
            if failure is not None:
                return failure
            if source_status is not None and type(source_status) is not EvidenceStatus:
                return f"limit parity source status is invalid for {joint_name}"
            if source_status is not None and source_status is not source.status:
                return f"limit parity source status does not match provenance for {joint_name}"
            failure = _invoke_post_init(
                item,
                LimitParityRecord.__post_init__,
                f"limit parity is invalid for {joint_name}",
            )
            if failure is not None:
                return failure
        try:
            if tuple(item.source_name for item in parity) != source_names:
                return f"limit parity source identity does not match {joint_name}"
        except Exception:
            return f"limit parity is structurally incomplete for {joint_name}"
        failure = _invoke_post_init(
            bound,
            ResolvedJointBound.__post_init__,
            f"limit bound is invalid for {joint_name}",
        )
        if failure is not None:
            return failure

    if len(bound_names) != len(expected_joint_names) or set(bound_names) != set(expected_joint_names):
        return "limit resolution bounds must exactly cover expected_joint_names"

    # Aggregate invariantはP2のcanonical methodへ委譲し、range/parity formulaを複製しない。
    return _invoke_post_init(
        result,
        LimitResolutionResult.__post_init__,
        "limit resolution aggregate is invalid",
    )


def _collision_result_inconsistency(result: CollisionCheckResult) -> str | None:
    """P3のcontext/evaluation/aggregate canonical invariantを再利用する。"""

    if type(result) is not CollisionCheckResult:
        return "collision result has an invalid type"
    try:
        validate_collision_context(result.context)
        for evaluation in result.evaluations:
            validate_collision_evaluation(evaluation)
        validated = validate_collision_check_result(result)
    except Exception:
        return "collision result failed canonical validation"
    if validated is not result:
        return "collision validator returned a different result"
    try:
        context = result.context
        status = result.status
        evaluations = result.evaluations
        reason_code = result.reason_code
    except Exception:
        return "collision result is structurally incomplete"
    if type(context) is not CollisionContext:
        return "collision result context is invalid"
    if type(status) is not CollisionStatus:
        return "collision result status is invalid"
    if type(evaluations) is not tuple:
        return "collision result evaluations must be a tuple"
    if any(type(item) is not CollisionEvaluation for item in evaluations):
        return "collision result contains an invalid evaluation"
    if not _valid_text(reason_code):
        return "collision result reason is invalid"
    try:
        identities = (
            context.robot_id,
            context.model_id,
            context.policy_id,
            context.policy_revision,
            context.inventory_id,
            context.inventory_revision,
        )
        expected_pair_ids = context.expected_pair_ids
        context._binding_fingerprint
    except Exception:
        return "collision result context is structurally incomplete"
    if any(not _valid_identity(identity) for identity in identities):
        return "collision result context identity is invalid"
    if type(expected_pair_ids) is not tuple or not expected_pair_ids:
        return "collision result expected pair inventory is invalid"
    if any(not _valid_text(pair_id) for pair_id in expected_pair_ids):
        return "collision result expected pair identity is invalid"

    for evaluation in evaluations:
        try:
            if type(evaluation.kind) is not CollisionKind:
                return "collision evaluation kind is invalid"
            if type(evaluation.status) is not CollisionStatus:
                return "collision evaluation status is invalid"
        except Exception:
            return "collision evaluation is structurally incomplete"
        failure = _invoke_post_init(
            evaluation,
            CollisionEvaluation.__post_init__,
            "collision evaluation is invalid",
        )
        if failure is not None:
            return failure

    # P3の__post_init__はcontext binding fingerprint、exact pair coverage、aggregate
    # status/reason、およびevaluation evidenceを同一canonical pathで検証する。
    return _invoke_post_init(
        result,
        CollisionCheckResult.__post_init__,
        "collision result aggregate is invalid",
    )


def _dynamic_result_inconsistency(
    result: ConfigurationFeasibilityResult | TrajectoryFeasibilityResult,
) -> str | None:
    """P4公開validatorへbinding/evidence検証を委譲し、DTOだけ補強検査する。"""

    if type(result) is ConfigurationFeasibilityResult:
        validator = validate_configuration_feasibility_result
    elif type(result) is TrajectoryFeasibilityResult:
        validator = validate_trajectory_feasibility_result
    else:
        return "dynamic result has an invalid type"

    try:
        status = result.status
        reason_code = result.reason_code
        diagnostics = result.diagnostics
    except Exception:
        return "dynamic result is structurally incomplete"
    if type(status) is not FeasibilityStatus:
        return "dynamic result status is invalid"
    if not _valid_text(reason_code):
        return "dynamic result reason is invalid"
    if type(diagnostics) is not tuple:
        return "dynamic diagnostics must be a tuple"
    if any(type(item) is not FeasibilityDiagnostic for item in diagnostics):
        return "dynamic diagnostics contain an invalid member"
    for diagnostic in diagnostics:
        failure = _invoke_post_init(
            diagnostic,
            FeasibilityDiagnostic.__post_init__,
            "dynamic diagnostic is invalid",
        )
        if failure is not None:
            return failure

    if type(result) is TrajectoryFeasibilityResult:
        try:
            velocity_evidence = result.velocity_evidence
        except Exception:
            return "dynamic velocity evidence is structurally incomplete"
        if type(velocity_evidence) is not tuple:
            return "dynamic velocity evidence must be a tuple"
        if any(type(item) is not VelocityEvidenceBinding for item in velocity_evidence):
            return "dynamic velocity evidence contains an invalid member"
        for item in velocity_evidence:
            try:
                if type(item.kind) is not VelocityEvidenceKind:
                    return "dynamic velocity evidence kind is invalid"
            except Exception:
                return "dynamic velocity evidence is structurally incomplete"
            failure = _invoke_post_init(
                item,
                VelocityEvidenceBinding.__post_init__,
                "dynamic velocity evidence is invalid",
            )
            if failure is not None:
                return failure

    try:
        expected_joint_names = result.expected_joint_names
        policy_id = result.policy_id
        policy_revision = result.policy_revision
        limit_source_ids = result.limit_source_ids
        bound_statuses = result.bound_statuses
        bound_evidence_ids = result.bound_evidence_ids
        qvel_available = result.qvel_available
        jacobian_available = result.jacobian_available
    except Exception:
        return "dynamic result binding is structurally incomplete"
    if type(expected_joint_names) is not tuple or not expected_joint_names:
        return "dynamic expected joint inventory is invalid"
    if any(not _valid_identity(name) for name in expected_joint_names):
        return "dynamic expected joint identity is invalid"
    if len(set(expected_joint_names)) != len(expected_joint_names):
        return "dynamic expected joint identities are duplicated"
    if not _valid_identity(policy_id) or not _valid_identity(policy_revision):
        return "dynamic policy identity is invalid"
    if type(limit_source_ids) is not tuple or any(
        not _valid_identity(item) for item in limit_source_ids
    ):
        return "dynamic limit source binding is invalid"
    if type(bound_evidence_ids) is not tuple or any(
        not _valid_identity(item) for item in bound_evidence_ids
    ):
        return "dynamic bound evidence binding is invalid"
    if type(bound_statuses) is not tuple or any(
        type(item) is not EvidenceStatus for item in bound_statuses
    ):
        return "dynamic bound evidence statuses are invalid"
    required_limit_count = len(expected_joint_names) * (
        1 if type(result) is ConfigurationFeasibilityResult else 2
    )
    if status is FeasibilityStatus.FEASIBLE:
        if (
            len(limit_source_ids) != required_limit_count
            or len(bound_statuses) != required_limit_count
            or len(bound_evidence_ids) != required_limit_count
        ):
            return "dynamic limit and evidence bindings are incomplete"
    elif not (
        len(limit_source_ids) == len(bound_statuses)
        and len(bound_statuses) == len(bound_evidence_ids)
    ):
        return "dynamic limit and evidence bindings are length-inconsistent"
    if type(result) is ConfigurationFeasibilityResult:
        try:
            source_id = result.source_id
        except Exception:
            return "dynamic source identity is structurally incomplete"
        if not _valid_identity(source_id):
            return "dynamic source identity is invalid"
        if qvel_available is not None and type(qvel_available) is not bool:
            return "dynamic qvel availability is invalid"
        if jacobian_available is not None and type(jacobian_available) is not bool:
            return "dynamic Jacobian availability is invalid"
    else:
        try:
            sample_count = result.sample_count
            source_ids = result.source_ids
        except Exception:
            return "dynamic trajectory binding is structurally incomplete"
        if (
            type(sample_count) is not int
            or sample_count < 0
            or type(source_ids) is not tuple
            or len(source_ids) != sample_count
            or any(not _valid_identity(item) for item in source_ids)
        ):
            return "dynamic trajectory sample/source binding is invalid"
        for name, availability in (
            ("qvel", qvel_available),
            ("Jacobian", jacobian_available),
        ):
            if availability is not None and (
                type(availability) is not tuple
                or len(availability) != sample_count
                or any(type(item) is not bool for item in availability)
            ):
                return f"dynamic trajectory {name} availability is invalid"

    try:
        validated = validator(result)
    except Exception:
        return "dynamic feasibility result failed canonical validation"
    if validated is not result:
        return "dynamic feasibility validator returned a different result"
    evidence_prefix = {
        EvidenceStatus.INVALID: "invalid_limit_",
        EvidenceStatus.UNAVAILABLE: "unavailable_limit_",
        EvidenceStatus.UNKNOWN: "unknown_limit_",
        EvidenceStatus.CONFLICT: "unknown_limit_",
    }
    for evidence_status, prefix in evidence_prefix.items():
        if evidence_status in bound_statuses and not any(
            item.code.startswith(prefix) for item in diagnostics
        ):
            return "dynamic bound evidence status has no matching diagnostic"
    diagnostic_evidence = {
        "invalid_limit_source": {EvidenceStatus.INVALID},
        "unavailable_limit_source": {EvidenceStatus.UNAVAILABLE},
        "unknown_limit_source": {EvidenceStatus.UNKNOWN, EvidenceStatus.CONFLICT},
    }
    for diagnostic in diagnostics:
        required_evidence = diagnostic_evidence.get(diagnostic.code)
        if required_evidence is not None and not any(
            item in required_evidence for item in bound_statuses
        ):
            return "dynamic limit diagnostic has no matching evidence status"
    try:
        authoritative = result.authoritative
    except Exception:
        return "dynamic result authority is unreadable"
    if type(authoritative) is not bool:
        return "dynamic result authority is invalid"
    if status is not FeasibilityStatus.FEASIBLE and authoritative:
        return "non-feasible dynamic result is spuriously authoritative"
    return None


def _safety_input_inconsistency(value: SafetyInput) -> str | None:
    """top-level SafetyInput fieldsを評価前にfail-closedで検証する。"""

    try:
        candidate_id = value.candidate_id
        provenance = value.provenance
        limit_resolution = value.limit_resolution
        collision = value.collision
        dynamic = value.dynamic
    except Exception:
        return "safety input is structurally incomplete"
    if not _valid_identity(candidate_id):
        return "safety input candidate identity is invalid"
    if type(provenance) is not tuple:
        return "safety input provenance must be a tuple"
    if any(not _valid_identity(item) for item in provenance):
        return "safety input provenance is invalid"
    if len(set(provenance)) != len(provenance):
        return "safety input provenance is duplicated"
    if limit_resolution is not None and type(limit_resolution) is not LimitResolutionResult:
        return "safety input limit result is invalid"
    if collision is not None and type(collision) is not CollisionCheckResult:
        return "safety input collision result is invalid"
    if dynamic is not None and type(dynamic) not in {
        ConfigurationFeasibilityResult,
        TrajectoryFeasibilityResult,
    }:
        return "safety input dynamic result is invalid"
    try:
        _validate_safety_input_contract(value)
    except Exception:
        return "safety input binding is invalid"
    return None


def _cross_component_inconsistency(value: SafetyInput) -> str | None:
    """P2/P3/P4の共有identityをcompose時に突き合わせる。"""

    try:
        limit_resolution = value.limit_resolution
        collision = value.collision
        dynamic = value.dynamic
    except Exception:
        return "cross-component binding is structurally incomplete"
    if limit_resolution is not None and collision is not None:
        try:
            if limit_resolution.robot_id != collision.context.robot_id:
                return "limit and collision robot identities do not match"
        except Exception:
            return "limit and collision robot identities are unreadable"
    if limit_resolution is not None and dynamic is not None:
        try:
            if limit_resolution.expected_joint_names != dynamic.expected_joint_names:
                return "limit and dynamic joint inventories do not match"
        except Exception:
            return "limit and dynamic joint inventories are unreadable"
    return None


def _limit_assessment(result: LimitResolutionResult | None) -> SafetyComponentAssessment:
    component = SafetyComponent.LIMIT
    if result is None:
        return _assessment_from_reason(
            component,
            "limit_resolution_unavailable",
            "physical limit resolution is unavailable",
        )
    inconsistency = _limit_result_inconsistency(result)
    if inconsistency is not None:
        return _assessment_from_reason(
            component,
            "limit_resolution_inconsistent",
            inconsistency,
            _safe_limit_provenance(result),
        )
    try:
        bounds = result.bounds
        statuses = tuple(bound.status for bound in bounds)
        bounded = tuple(
            bound.lower_rad is not None and bound.upper_rad is not None
            for bound in bounds
        )
    except Exception:
        return _assessment_from_reason(
            component,
            "limit_resolution_inconsistent",
            "limit resolution result became unreadable after validation",
            _safe_limit_provenance(result),
        )
    provenance = _safe_limit_provenance(result)
    if any(
        status in {
            LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
            LimitResolutionStatus.RESOLVED_PROVISIONAL,
        }
        and not is_bounded
        for is_bounded, status in zip(bounded, statuses, strict=True)
    ):
        action = SafetyDecisionAction.INVALID
        code = "limit_resolution_unbounded"
        message = "resolved physical limit bounds are incomplete"
    elif any(status is LimitResolutionStatus.INVALID for status in statuses):
        action = SafetyDecisionAction.INVALID
        code = "limit_resolution_invalid"
        message = "physical limit resolution is invalid"
    elif any(status in {LimitResolutionStatus.UNKNOWN, LimitResolutionStatus.UNAVAILABLE} for status in statuses):
        action = SafetyDecisionAction.UNAVAILABLE
        code = "limit_resolution_unavailable"
        message = "physical limit evidence is unknown or unavailable"
    elif any(status is LimitResolutionStatus.MISMATCH for status in statuses):
        action = SafetyDecisionAction.REJECT
        code = "limit_resolution_mismatch"
        message = "physical limit sources do not agree"
    elif any(status is LimitResolutionStatus.RESOLVED_PROVISIONAL for status in statuses):
        action = SafetyDecisionAction.HOLD
        code = "limit_resolution_provisional"
        message = "only provisional software limit evidence is resolved"
    elif statuses and all(status is LimitResolutionStatus.RESOLVED_AUTHORITATIVE for status in statuses):
        action = SafetyDecisionAction.ALLOW
        code = "limit_resolution_authoritative"
        message = "authoritative physical limits are resolved"
    else:
        action = SafetyDecisionAction.UNAVAILABLE
        code = "limit_resolution_unavailable"
        message = "physical limit resolution has no complete evidence"
    return _assessment_from_reason(component, code, message, provenance)


def _collision_assessment(result: CollisionCheckResult | None) -> SafetyComponentAssessment:
    component = SafetyComponent.COLLISION
    if result is None:
        return _assessment_from_reason(
            component,
            "collision_result_unavailable",
            "collision result is unavailable",
        )
    inconsistency = _collision_result_inconsistency(result)
    if inconsistency is not None:
        return _assessment_from_reason(
            component,
            "collision_result_inconsistent",
            inconsistency,
            _safe_collision_provenance(result),
        )
    try:
        result_status = result.status
        evaluations = tuple(result.evaluations)
        evaluation_statuses = tuple(evaluation.status for evaluation in evaluations)
    except Exception:
        return _assessment_from_reason(
            component,
            "collision_result_inconsistent",
            "collision result became unreadable after validation",
            _safe_collision_provenance(result),
        )
    if result_status is CollisionStatus.CLEAR and not evaluations:
        action, code, message = SafetyDecisionAction.UNAVAILABLE, "collision_result_unavailable", "collision result has no pair evidence"
    elif result_status is CollisionStatus.CLEAR and any(
        status is not CollisionStatus.CLEAR for status in evaluation_statuses
    ):
        action, code, message = SafetyDecisionAction.INVALID, "collision_result_inconsistent", "collision result status does not match pair evidence"
    elif result_status is CollisionStatus.INVALID:
        action, code, message = SafetyDecisionAction.INVALID, "collision_result_invalid", "collision result is invalid"
    elif result_status is CollisionStatus.UNAVAILABLE:
        action, code, message = SafetyDecisionAction.UNAVAILABLE, "collision_result_unavailable", "collision observation is unavailable"
    elif result_status is CollisionStatus.UNKNOWN:
        action, code, message = SafetyDecisionAction.UNAVAILABLE, "collision_result_unknown", "collision distance evidence is unknown"
    elif result_status is CollisionStatus.COLLISION:
        action, code, message = SafetyDecisionAction.STOP, "collision_detected", "collision or penetration requires stop"
    elif result_status is CollisionStatus.NEAR_COLLISION:
        action, code, message = SafetyDecisionAction.HOLD, "near_collision_detected", "clearance is inside the near-collision margin"
    elif result_status is CollisionStatus.CONTACT:
        action, code, message = SafetyDecisionAction.HOLD, "task_object_contact", "task-object contact requires a held decision"
    else:
        action, code, message = SafetyDecisionAction.ALLOW, "collision_clear", "collision evidence is clear"
    provenance = _safe_collision_provenance(result)
    return _assessment_from_reason(component, code, message, provenance)


def _dynamic_assessment(
    result: ConfigurationFeasibilityResult | TrajectoryFeasibilityResult | None,
) -> SafetyComponentAssessment:
    component = SafetyComponent.DYNAMIC
    if result is None:
        return _assessment_from_reason(
            component,
            "dynamic_result_unavailable",
            "dynamic feasibility result is unavailable",
        )
    inconsistency = _dynamic_result_inconsistency(result)
    if inconsistency is not None:
        return _assessment_from_reason(
            component,
            "dynamic_result_inconsistent",
            inconsistency,
            _safe_dynamic_provenance(result),
        )
    provenance = _safe_dynamic_provenance(result)
    try:
        result_status = result.status
        authoritative = result.authoritative
    except Exception:
        return _assessment_from_reason(
            component,
            "dynamic_result_inconsistent",
            "dynamic result became unreadable after validation",
            provenance,
        )
    if result_status is FeasibilityStatus.INVALID:
        action, code, message = SafetyDecisionAction.INVALID, "dynamic_result_invalid", "dynamic feasibility result is invalid"
    elif result_status is FeasibilityStatus.UNAVAILABLE:
        action, code, message = SafetyDecisionAction.UNAVAILABLE, "dynamic_result_unavailable", "dynamic feasibility evidence is unavailable"
    elif result_status is FeasibilityStatus.UNKNOWN:
        action, code, message = SafetyDecisionAction.UNAVAILABLE, "dynamic_result_unknown", "dynamic feasibility evidence is unknown"
    elif result_status is FeasibilityStatus.REJECTED:
        action, code, message = SafetyDecisionAction.REJECT, "dynamic_feasibility_rejected", "velocity, acceleration, or numerical feasibility was rejected"
    elif not authoritative:
        action, code, message = SafetyDecisionAction.HOLD, "dynamic_result_provisional", "dynamic result relies on provisional evidence"
    else:
        action, code, message = SafetyDecisionAction.ALLOW, "dynamic_feasibility_clear", "dynamic feasibility is clear"
    return _assessment_from_reason(component, code, message, provenance)


_ACTION_PRIORITY = {
    SafetyDecisionAction.ALLOW: 0,
    SafetyDecisionAction.HOLD: 1,
    SafetyDecisionAction.REJECT: 2,
    SafetyDecisionAction.UNAVAILABLE: 3,
    SafetyDecisionAction.STOP: 4,
    SafetyDecisionAction.INVALID: 5,
}


# componentごとのreason/actionは、factoryとpublic DTO validatorが共有する
# 単一のcanonical mappingとする。unknown reason codeはcomposition authorityに
# 到達する前に拒否し、callerがALLOWを自己申告する経路を残さない。
_COMPONENT_REASON_ACTIONS = {
    SafetyComponent.LIMIT: {
        "limit_resolution_unavailable": SafetyDecisionAction.UNAVAILABLE,
        "limit_resolution_inconsistent": SafetyDecisionAction.INVALID,
        "limit_resolution_unbounded": SafetyDecisionAction.INVALID,
        "limit_resolution_invalid": SafetyDecisionAction.INVALID,
        "limit_resolution_mismatch": SafetyDecisionAction.REJECT,
        "limit_candidate_out_of_bounds": SafetyDecisionAction.REJECT,
        "limit_resolution_provisional": SafetyDecisionAction.HOLD,
        "limit_resolution_authoritative": SafetyDecisionAction.ALLOW,
    },
    SafetyComponent.COLLISION: {
        "collision_result_unavailable": SafetyDecisionAction.UNAVAILABLE,
        "collision_result_inconsistent": SafetyDecisionAction.INVALID,
        "collision_result_invalid": SafetyDecisionAction.INVALID,
        "collision_result_unknown": SafetyDecisionAction.UNAVAILABLE,
        "collision_detected": SafetyDecisionAction.STOP,
        "near_collision_detected": SafetyDecisionAction.HOLD,
        "task_object_contact": SafetyDecisionAction.HOLD,
        "collision_clear": SafetyDecisionAction.ALLOW,
    },
    SafetyComponent.DYNAMIC: {
        "dynamic_result_unavailable": SafetyDecisionAction.UNAVAILABLE,
        "dynamic_result_inconsistent": SafetyDecisionAction.INVALID,
        "dynamic_result_invalid": SafetyDecisionAction.INVALID,
        "dynamic_result_unknown": SafetyDecisionAction.UNAVAILABLE,
        "dynamic_feasibility_rejected": SafetyDecisionAction.REJECT,
        "dynamic_result_provisional": SafetyDecisionAction.HOLD,
        "dynamic_feasibility_clear": SafetyDecisionAction.ALLOW,
    },
}

_CANONICAL_ALLOW_REASON_CODES = frozenset({
    "limit_resolution_authoritative",
    "collision_clear",
    "dynamic_feasibility_clear",
})
_CANONICAL_ASSESSMENT_ORDER = (
    SafetyComponent.LIMIT,
    SafetyComponent.COLLISION,
    SafetyComponent.DYNAMIC,
)


def _assessment_from_reason(
    component: SafetyComponent,
    reason_code: str,
    message: str,
    provenance: Sequence[str] = (),
) -> SafetyComponentAssessment:
    """factoryがcanonical reason/action mappingからassessmentを生成する。"""

    try:
        action = _COMPONENT_REASON_ACTIONS[component][reason_code]
    except (KeyError, TypeError) as exc:
        raise ValueError("unknown component reason code") from exc
    with _p5_construction_scope():
        return SafetyComponentAssessment(
            component,
            action,
            _reason(component, reason_code, message, provenance),
        )


_INVALID_INPUT_REASON_CODES = frozenset({
    "invalid_safety_input",
    "invalid_safety_samples",
})
_COMPONENTS = frozenset({
    SafetyComponent.LIMIT,
    SafetyComponent.COLLISION,
    SafetyComponent.DYNAMIC,
})


def _canonical_provenance(value: object, name: str) -> tuple[str, ...]:
    """provenanceをtyped identityとして正規化する。"""

    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    values = tuple(value)
    if any(not _valid_identity(item) for item in values):
        raise ValueError(f"{name} must contain concrete identities")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")
    return tuple(sorted(values))


def _validate_safety_reason(
    reason: SafetyReason,
    *,
    initialize: bool = False,
) -> None:
    """SafetyReasonのconstructor/bypass後に同一canonical invariantを適用する。"""

    if type(reason) is not SafetyReason:
        raise TypeError("reason must be SafetyReason")
    reason_code = getattr(reason, "reason_code", None)
    component = getattr(reason, "component", None)
    operator_message = getattr(reason, "operator_message", None)
    provenance = getattr(reason, "provenance", None)
    if not _valid_identity(reason_code):
        raise ValueError("reason_code must be a concrete identity")
    if type(component) is not SafetyComponent:
        raise TypeError("reason component must be SafetyComponent")
    if not _valid_text(operator_message):
        raise ValueError("operator_message must be a non-empty string")
    canonical_provenance = _canonical_provenance(provenance, "reason provenance")
    fingerprint = (reason_code, component, operator_message, canonical_provenance)
    canonical_allow = reason_code in _CANONICAL_ALLOW_REASON_CODES
    if initialize:
        if canonical_allow and _P5_CONSTRUCTION_CONTEXT.get() is not _P5_CONSTRUCTION_TOKEN:
            raise ValueError("canonical allow reason requires composition origin")
        object.__setattr__(reason, "provenance", canonical_provenance)
        object.__setattr__(reason, "_binding_fingerprint", fingerprint)
        _register_p5_seal(reason, _reason_semantic_snapshot(reason))
        if canonical_allow:
            _register_p5_origin(reason, "reason")
        return
    try:
        bound = reason._binding_fingerprint
    except AttributeError as exc:
        raise ValueError("reason binding fingerprint is missing") from exc
    if bound != fingerprint:
        raise ValueError("reason binding was mutated")
    _validate_p5_seal(reason, _reason_semantic_snapshot(reason))
    if canonical_allow:
        _validate_p5_origin(reason, "reason")


def _validate_safety_input_contract(
    value: SafetyInput,
    *,
    initialize: bool = False,
) -> None:
    """candidate/provenanceのpublic DTO invariantをconstructorとconsumerで共有する。"""

    if type(value) is not SafetyInput:
        raise TypeError("value must be SafetyInput")
    candidate_id = getattr(value, "candidate_id", None)
    provenance = getattr(value, "provenance", None)
    if not _valid_identity(candidate_id):
        raise ValueError("candidate_id must be a concrete identity")
    canonical_provenance = _canonical_provenance(provenance, "safety input provenance")
    nested_values = tuple(
        getattr(value, name, None)
        for name in ("limit_resolution", "collision", "dynamic")
    )
    for (name, expected_type), nested in zip(
        (
            ("limit_resolution", LimitResolutionResult),
            ("collision", CollisionCheckResult),
            ("dynamic", (ConfigurationFeasibilityResult, TrajectoryFeasibilityResult)),
        ),
        nested_values,
        strict=True,
    ):
        if nested is not None and type(nested) not in (
            expected_type if isinstance(expected_type, tuple) else (expected_type,)
        ):
            raise TypeError(f"{name} has an invalid type")
    # DTO差替えもconstructor時のbindingから外れた入力として扱う。同一DTO内の
    # field mutationは各upstream public validatorがdeep revalidateする。
    nested_fingerprint = tuple(
        id(nested) if nested is not None else None
        for nested in nested_values
    )
    fingerprint = (candidate_id, canonical_provenance, nested_fingerprint)
    if initialize:
        object.__setattr__(value, "provenance", canonical_provenance)
        object.__setattr__(value, "_binding_fingerprint", fingerprint)
        _register_p5_seal(value, _input_semantic_snapshot(value))
        return
    try:
        bound = value._binding_fingerprint
    except AttributeError as exc:
        raise ValueError("safety input binding fingerprint is missing") from exc
    if bound != fingerprint:
        raise ValueError("safety input binding was mutated")
    _validate_p5_seal(value, _input_semantic_snapshot(value))


def _validate_safety_component_assessment(
    assessment: SafetyComponentAssessment,
    *,
    initialize: bool = False,
) -> None:
    """component action/reason bindingをdeep revalidateする。"""

    if type(assessment) is not SafetyComponentAssessment:
        raise TypeError("assessment must be SafetyComponentAssessment")
    component = getattr(assessment, "component", None)
    action = getattr(assessment, "action", None)
    reason = getattr(assessment, "reason", None)
    if type(component) is not SafetyComponent:
        raise TypeError("assessment component must be SafetyComponent")
    if type(action) is not SafetyDecisionAction:
        raise TypeError("assessment action must be SafetyDecisionAction")
    if type(reason) is not SafetyReason:
        raise TypeError("assessment reason must be SafetyReason")
    _validate_safety_reason(reason)
    if reason.component is not component:
        raise ValueError("assessment reason component must match assessment component")
    expected_action = _COMPONENT_REASON_ACTIONS.get(component, {}).get(reason.reason_code)
    if expected_action is None:
        raise ValueError("assessment reason code is unknown for component")
    if action is not expected_action:
        raise ValueError("assessment action does not match canonical reason mapping")
    if reason.reason_code in _CANONICAL_ALLOW_REASON_CODES and not reason.provenance:
        raise ValueError("canonical allow assessment requires concrete provenance")
    fingerprint = (component, action, reason._binding_fingerprint)
    canonical_allow = action is SafetyDecisionAction.ALLOW
    if initialize:
        if canonical_allow and _P5_CONSTRUCTION_CONTEXT.get() is not _P5_CONSTRUCTION_TOKEN:
            raise ValueError("canonical allow assessment requires composition origin")
        object.__setattr__(assessment, "_binding_fingerprint", fingerprint)
        _register_p5_seal(assessment, _assessment_semantic_snapshot(assessment))
        if canonical_allow:
            _register_p5_origin(assessment, "assessment")
        return
    try:
        bound = assessment._binding_fingerprint
    except AttributeError as exc:
        raise ValueError("assessment binding fingerprint is missing") from exc
    if bound != fingerprint:
        raise ValueError("assessment binding was mutated")
    _validate_p5_seal(assessment, _assessment_semantic_snapshot(assessment))
    if canonical_allow:
        _validate_p5_origin(assessment, "assessment")


def _validate_safety_decision(
    decision: SafetyDecision,
    *,
    initialize: bool = False,
) -> None:
    """SafetyDecisionのaggregate/action/provenance invariantを一つの経路で検証する。"""

    if type(decision) is not SafetyDecision:
        raise TypeError("decision must be SafetyDecision")
    candidate_id = getattr(decision, "candidate_id", None)
    action = getattr(decision, "action", None)
    reason = getattr(decision, "reason", None)
    assessments = getattr(decision, "assessments", None)
    provenance = getattr(decision, "provenance", None)
    if not _valid_identity(candidate_id):
        raise ValueError("decision candidate_id is invalid")
    if type(action) is not SafetyDecisionAction:
        raise TypeError("decision action must be SafetyDecisionAction")
    if type(reason) is not SafetyReason:
        raise TypeError("decision reason must be SafetyReason")
    _validate_safety_reason(reason)
    if type(assessments) is not tuple or any(
        type(item) is not SafetyComponentAssessment for item in assessments
    ):
        raise TypeError("decision assessments must contain SafetyComponentAssessment values")
    if not assessments:
        if not (
            action is SafetyDecisionAction.INVALID
            and reason.component is SafetyComponent.INPUT
            and reason.reason_code in _INVALID_INPUT_REASON_CODES
        ):
            raise ValueError("decision without component assessments must be invalid input")
        expected_provenance = reason.provenance
    else:
        if len(assessments) != len(_COMPONENTS) or {
            item.component for item in assessments
        } != _COMPONENTS:
            raise ValueError("decision assessments must cover limit, collision, and dynamic exactly")
        if tuple(item.component for item in assessments) != _CANONICAL_ASSESSMENT_ORDER:
            raise ValueError("decision assessments must use canonical component order")
        for item in assessments:
            _validate_safety_component_assessment(item)
        selected = max(assessments, key=lambda item: _ACTION_PRIORITY[item.action])
        if action is not selected.action or reason != selected.reason:
            raise ValueError("decision aggregate must match the highest-priority assessment")
        expected_provenance = tuple(
            sorted(
                {
                    item
                    for assessment in assessments
                    for item in assessment.reason.provenance
                }
                | set(reason.provenance)
            )
        )
    canonical_provenance = _canonical_provenance(provenance, "decision provenance")
    if not set(expected_provenance).issubset(canonical_provenance):
        raise ValueError("decision provenance must include all assessment evidence")
    fingerprint = (
        candidate_id,
        action,
        reason._binding_fingerprint,
        tuple(item._binding_fingerprint for item in assessments),
        canonical_provenance,
    )
    canonical_allow = action is SafetyDecisionAction.ALLOW
    if initialize:
        if canonical_allow and _P5_CONSTRUCTION_CONTEXT.get() is not _P5_CONSTRUCTION_TOKEN:
            raise ValueError("canonical allow decision requires composition origin")
        object.__setattr__(decision, "provenance", canonical_provenance)
        object.__setattr__(decision, "_binding_fingerprint", fingerprint)
        _register_p5_seal(decision, _decision_semantic_snapshot(decision))
        if canonical_allow:
            _register_p5_origin(decision, "decision")
        return
    try:
        bound = decision._binding_fingerprint
    except AttributeError as exc:
        raise ValueError("decision binding fingerprint is missing") from exc
    if bound != fingerprint:
        raise ValueError("decision binding was mutated")
    _validate_p5_seal(decision, _decision_semantic_snapshot(decision))
    if canonical_allow:
        _validate_p5_origin(decision, "decision")


def _validate_bounded_safety_result(
    result: BoundedSafetySamplingResult,
    *,
    initialize: bool = False,
) -> None:
    """bounded samplingのcoverage/first-stop/aggregateをcanonicalに検証する。"""

    if type(result) is not BoundedSafetySamplingResult:
        raise TypeError("result must be BoundedSafetySamplingResult")
    decisions = getattr(result, "decisions", None)
    first_non_allow_index = getattr(result, "first_non_allow_index", None)
    if type(decisions) is not tuple or not decisions:
        raise ValueError("bounded decisions must be a non-empty tuple")
    if any(type(item) is not SafetyDecision for item in decisions):
        raise TypeError("bounded decisions must contain SafetyDecision values")
    for decision in decisions:
        _validate_safety_decision(decision)
    non_allow_indices = tuple(
        index
        for index, decision in enumerate(decisions)
        if decision.action is not SafetyDecisionAction.ALLOW
    )
    expected_index = non_allow_indices[0] if non_allow_indices else None
    if first_non_allow_index is not None and type(first_non_allow_index) is not int:
        raise TypeError("first_non_allow_index must be an integer or None")
    if first_non_allow_index != expected_index:
        raise ValueError("first_non_allow_index must identify the first non-allow decision")
    if expected_index is not None and len(decisions) != expected_index + 1:
        raise ValueError("bounded decisions must stop at first non-allow decision")
    selected = decisions[-1] if expected_index is None else decisions[expected_index]
    reason = getattr(result, "reason", None)
    provenance = getattr(result, "provenance", None)
    if initialize and reason is None:
        reason = selected.reason
        object.__setattr__(result, "reason", reason)
    if type(reason) is not SafetyReason:
        raise TypeError("bounded aggregate reason must be SafetyReason")
    _validate_safety_reason(reason)
    if reason != selected.reason:
        raise ValueError("bounded aggregate reason must match the selected decision")
    canonical_provenance = _canonical_provenance(provenance, "bounded provenance")
    if initialize and not canonical_provenance and selected.provenance:
        canonical_provenance = selected.provenance
        object.__setattr__(result, "provenance", canonical_provenance)
    if canonical_provenance != selected.provenance:
        raise ValueError("bounded aggregate provenance must match the selected decision")
    fingerprint = (
        tuple(item._binding_fingerprint for item in decisions),
        expected_index,
        reason._binding_fingerprint,
        canonical_provenance,
    )
    if initialize:
        object.__setattr__(result, "_binding_fingerprint", fingerprint)
        _register_p5_seal(result, _bounded_semantic_snapshot(result))
        return
    try:
        bound = result._binding_fingerprint
    except AttributeError as exc:
        raise ValueError("bounded sampling binding fingerprint is missing") from exc
    if bound != fingerprint:
        raise ValueError("bounded sampling binding was mutated")
    _validate_p5_seal(result, _bounded_semantic_snapshot(result))


def evaluate_physical_safety(safety_input: SafetyInput) -> SafetyDecision:
    """P2/P3/P4 resultを一意のphysical safety decisionへcomposeする。"""

    def invalid_input() -> SafetyDecision:
        reason = _reason(
            SafetyComponent.INPUT,
            "invalid_safety_input",
            "physical safety input is invalid",
        )
        return SafetyDecision(
            "invalid-input",
            SafetyDecisionAction.INVALID,
            reason,
            (),
            (),
        )

    if not isinstance(safety_input, SafetyInput):
        return invalid_input()
    try:
        if _safety_input_inconsistency(safety_input) is not None:
            return invalid_input()
        if _cross_component_inconsistency(safety_input) is not None:
            return invalid_input()
        assessments = (
            _limit_assessment(safety_input.limit_resolution),
            _collision_assessment(safety_input.collision),
            _dynamic_assessment(safety_input.dynamic),
        )
        if all(item.action is SafetyDecisionAction.ALLOW for item in assessments):
            collision_candidate = safety_input.collision.evaluated_candidate
            if collision_candidate is not None:
                if collision_candidate != safety_input.dynamic.evaluated_candidate:
                    return invalid_input()
                if not collision_candidate.configurations:
                    return invalid_input()
                violating_joints: list[str] = []
                for qpos_rad, _ in collision_candidate.configurations:
                    for joint_name in authoritative_position_bound_violations(
                        safety_input.limit_resolution,
                        joint_names=collision_candidate.joint_names,
                        qpos_rad=qpos_rad,
                    ):
                        if joint_name not in violating_joints:
                            violating_joints.append(joint_name)
                if violating_joints:
                    assessments = (
                        _assessment_from_reason(
                            SafetyComponent.LIMIT,
                            "limit_candidate_out_of_bounds",
                            "candidate joint position is outside authoritative physical limits: "
                            + ", ".join(violating_joints),
                            assessments[0].reason.provenance,
                        ),
                        assessments[1],
                        assessments[2],
                    )
        selected = max(assessments, key=lambda item: _ACTION_PRIORITY[item.action])
        provenance = tuple(
            sorted(
                set(safety_input.provenance)
                | {
                    item
                    for assessment in assessments
                    for item in assessment.reason.provenance
                }
            )
        )
        with _p5_construction_scope():
            return SafetyDecision(
                safety_input.candidate_id,
                selected.action,
                selected.reason,
                assessments,
                provenance,
            )
    except CollisionContractViolation:
        # malformed P3 context/evaluation is user data at this boundary.
        return invalid_input()
    except Exception:
        # user/data-derived malformed fields must never escape the safety boundary.
        return invalid_input()


def evaluate_bounded_safety_samples(samples: Sequence[SafetyInput]) -> BoundedSafetySamplingResult:
    """有限candidate列を順に検査し、最初のnon-allowでbounded stopする。"""

    def invalid_samples() -> BoundedSafetySamplingResult:
        reason = _reason(SafetyComponent.INPUT, "invalid_safety_samples", "bounded safety samples are invalid")
        decision = SafetyDecision("invalid-samples", SafetyDecisionAction.INVALID, reason, (), reason.provenance)
        return BoundedSafetySamplingResult((decision,), 0)

    try:
        if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)) or not samples:
            return invalid_samples()
    except Exception:
        return invalid_samples()
    decisions: list[SafetyDecision] = []
    try:
        for index, sample in enumerate(samples):
            decision = evaluate_physical_safety(sample)
            decisions.append(decision)
            if decision.action is not SafetyDecisionAction.ALLOW:
                return BoundedSafetySamplingResult(tuple(decisions), index)
        return BoundedSafetySamplingResult(tuple(decisions), None)
    except Exception:
        return invalid_samples()


def validate_safety_reason(reason: SafetyReason) -> SafetyReason:
    """SafetyReasonのpublic canonical validator。"""

    _validate_safety_reason(reason)
    return reason


def validate_safety_projection(
    action: SafetyDecisionAction | str,
    reason_identity: str,
    provenance: tuple[str, ...],
) -> tuple[SafetyDecisionAction, str, tuple[str, ...]]:
    """P5 assessmentのaction / reason identity / provenanceを公開検証する。"""

    try:
        canonical_action = (
            action
            if type(action) is SafetyDecisionAction
            else SafetyDecisionAction(action)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("projection action is invalid") from exc
    if type(reason_identity) is not str or not reason_identity or reason_identity != reason_identity.strip():
        raise ValueError("projection reason identity is invalid")
    parts = reason_identity.split(":")
    if len(parts) != 2 or not all(parts):
        raise ValueError("projection reason identity must be component:reason_code")
    try:
        component = SafetyComponent(parts[0])
    except ValueError as exc:
        raise ValueError("projection component is invalid") from exc
    if component is SafetyComponent.INPUT:
        raise ValueError("projection must identify a component assessment")
    reason_code = parts[1]
    if not all(
        character.isascii()
        and (character.islower() or character.isdigit() or character == "_")
        for character in reason_code
    ) or not reason_code[0].islower():
        raise ValueError("projection reason code must use lowercase underscore notation")
    expected_action = _COMPONENT_REASON_ACTIONS.get(component, {}).get(reason_code)
    if expected_action is None:
        raise ValueError("projection reason code is unknown for component")
    if canonical_action is not expected_action:
        raise ValueError("projection action does not match canonical reason mapping")
    canonical_provenance = _canonical_provenance(provenance, "projection provenance")
    if canonical_action is SafetyDecisionAction.ALLOW and not canonical_provenance:
        raise ValueError("allow projection requires concrete provenance")
    return canonical_action, reason_identity, canonical_provenance


# downstream operator-validationが名称を明示する場合も同じvalidatorを使う。
validate_safety_decision_projection = validate_safety_projection


def validate_safety_input(value: SafetyInput) -> SafetyInput:
    """SafetyInputとnested upstream resultを再検証するpublic boundary。"""

    _validate_safety_input_contract(value)
    if value.limit_resolution is not None:
        validate_limit_resolution_result(value.limit_resolution)
    if value.collision is not None:
        validate_collision_context(value.collision.context)
        for evaluation in value.collision.evaluations:
            validate_collision_evaluation(evaluation)
        validate_collision_check_result(value.collision)
    if value.dynamic is not None:
        if type(value.dynamic) is ConfigurationFeasibilityResult:
            validate_configuration_feasibility_result(value.dynamic)
        else:
            validate_trajectory_feasibility_result(value.dynamic)
    cross_component_error = _cross_component_inconsistency(value)
    if cross_component_error is not None:
        raise ValueError(cross_component_error)
    return value


def validate_safety_decision(decision: SafetyDecision) -> SafetyDecision:
    """SafetyDecisionのpublic aggregate validator。"""

    _validate_safety_decision(decision)
    return decision


def validate_bounded_safety_sampling_result(
    result: BoundedSafetySamplingResult,
) -> BoundedSafetySamplingResult:
    """bounded sampling結果のpublic canonical validator。"""

    _validate_bounded_safety_result(result)
    return result


__all__ = [
    "BoundedSafetySamplingResult",
    "SafetyComponent",
    "SafetyComponentAssessment",
    "SafetyDecision",
    "SafetyDecisionAction",
    "SafetyInput",
    "SafetyReason",
    "evaluate_bounded_safety_samples",
    "evaluate_physical_safety",
    "validate_bounded_safety_sampling_result",
    "validate_safety_decision",
    "validate_safety_decision_projection",
    "validate_safety_input",
    "validate_safety_projection",
    "validate_safety_reason",
]
