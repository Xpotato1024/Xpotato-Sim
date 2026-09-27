"""Typedなphysical output requestへP5 safety decisionを結合する。\n\ncanonical physical-safety-core resultを再利用し、limit、collision、dynamic feasibilityの\nformulaを複製せず、transportやhardware operationも実行しない。\n"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from numbers import Real
from typing import Literal

from xpotato_sim.runtime.safety.physical_safety_core import (
    SafetyDecision,
    SafetyDecisionAction,
    SafetyInput,
    evaluate_physical_safety,
    validate_safety_decision,
    validate_safety_input,
)
from xpotato_sim.schemas import JointPositionCommand, PhysicalOutputRequest
from xpotato_sim.runtime.safety.collision_policy import CollisionCheckResult
from xpotato_sim.runtime.safety.limit_resolution import LimitResolutionResult
from xpotato_sim.runtime.safety.trajectory_feasibility import (
    ConfigurationState,
    JacobianDiagnostic,
    TrajectoryFeasibilityPolicy,
    evaluate_configuration_feasibility,
)


PHYSICAL_OUTPUT_SAFETY_BINDING_SCHEMA_VERSION = "physical-output-safety-binding/v1"
PHYSICAL_OUTPUT_SAFETY_EVIDENCE_SCHEMA_VERSION = "physical-output-safety-evidence/v1"
PHYSICAL_OUTPUT_CANDIDATE_ID_PREFIX = "physical-output-candidate/v1:sha256:"

PhysicalOutputSafetyStatus = Literal[
    "allowed",
    "held",
    "rejected",
    "stopped",
    "unavailable",
    "invalid",
]
_PHYSICAL_OUTPUT_SAFETY_STATUSES = frozenset(
    {"allowed", "held", "rejected", "stopped", "unavailable", "invalid"}
)
_ACTION_STATUS: dict[SafetyDecisionAction, PhysicalOutputSafetyStatus] = {
    SafetyDecisionAction.ALLOW: "allowed",
    SafetyDecisionAction.HOLD: "held",
    SafetyDecisionAction.REJECT: "rejected",
    SafetyDecisionAction.STOP: "stopped",
    SafetyDecisionAction.UNAVAILABLE: "unavailable",
    SafetyDecisionAction.INVALID: "invalid",
}


def _finite_timestamp(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be numeric")
    timestamp = float(value)
    if not isfinite(timestamp):
        raise ValueError(f"{name} must be finite")
    return timestamp


def _identifier(name: str, value: object, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def physical_output_candidate_id(request: PhysicalOutputRequest) -> str:
    """Canonicalなoutput request bytesからversioned candidate identityを作る。"""

    if not isinstance(request, PhysicalOutputRequest):
        raise TypeError("candidate identity requires PhysicalOutputRequest")
    return (
        f"{PHYSICAL_OUTPUT_CANDIDATE_ID_PREFIX}"
        f"{sha256(request.to_json_bytes()).hexdigest()}"
    )


def _evaluated_candidate_error(request: PhysicalOutputRequest, safety_input: SafetyInput) -> str | None:
    """一意に解決できるjoint targetだけをowner由来の評価値へ照合する。"""
    if not isinstance(request.command, JointPositionCommand):
        return "physical_safety_candidate_semantics_unresolved"
    collision = safety_input.collision
    dynamic = safety_input.dynamic
    if collision is None or dynamic is None:
        return "physical_safety_evaluated_candidate_missing"
    candidate = collision.evaluated_candidate
    if candidate is None or dynamic.evaluated_candidate is None:
        return "physical_safety_evaluated_candidate_missing"
    if candidate != dynamic.evaluated_candidate:
        return "physical_safety_evaluated_candidate_mismatch"
    if candidate.joint_route is None or candidate.joint_route.endpoint_id != request.endpoint_id:
        return "physical_safety_evaluated_endpoint_mismatch"
    if candidate.timestamps_s or len(candidate.configurations) != 1:
        return "physical_safety_candidate_semantics_unresolved"
    if candidate.configurations[0][0] != request.command.joint_angles_rad:
        return "physical_safety_evaluated_candidate_mismatch"
    return None


def compose_physical_output_safety_input(
    request: PhysicalOutputRequest,
    limit_resolution: LimitResolutionResult,
    collision: CollisionCheckResult,
    dynamic_policy: TrajectoryFeasibilityPolicy,
    jacobian: JacobianDiagnostic | None,
) -> SafetyInput:
    """P3が実観測したrequest targetの同一configurationをP4へ渡す。"""
    candidate = collision.evaluated_candidate
    if not isinstance(request.command, JointPositionCommand):
        raise ValueError("physical output candidate semantics are unresolved")
    if candidate is None or candidate.timestamps_s or len(candidate.configurations) != 1:
        raise ValueError("physical output requires a producer-bound configuration")
    if candidate.joint_route is None or candidate.joint_route.endpoint_id != request.endpoint_id:
        raise ValueError("physical output evaluated endpoint mismatch")
    qpos, qvel = candidate.configurations[0]
    if qpos != request.command.joint_angles_rad:
        raise ValueError("physical output evaluated candidate mismatch")
    if candidate.joint_names != dynamic_policy.joint_names:
        raise ValueError("physical output evaluated joint inventory mismatch")
    dynamic = evaluate_configuration_feasibility(
        ConfigurationState(qpos, qvel, jacobian, "physical-output-evaluated-configuration", candidate.joint_route),
        dynamic_policy,
    )
    return SafetyInput(
        physical_output_candidate_id(request),
        limit_resolution,
        collision,
        dynamic,
        (f"software_revision:{request.software_revision}",),
    )


def _typed_safety_value(value: object) -> object:
    """Serialize validated P2/P3/P4 DTOs by their public typed content."""

    value_type = type(value)
    if value is None or value_type in {str, bool, int}:
        return value
    if value_type is float:
        if not isfinite(value):
            raise ValueError("safety input contains a non-finite number")
        return value
    if isinstance(value, Enum):
        if not type(value).__module__.startswith("xpotato_sim.runtime.safety."):
            raise TypeError("safety input contains an unsupported enum")
        return {
            "enum_type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _typed_safety_value(value.value),
        }
    if value_type in {tuple, list}:
        return [_typed_safety_value(item) for item in value]
    if value_type is dict:
        if any(type(key) is not str for key in value):
            raise TypeError("safety input mappings require string keys")
        return {
            key: _typed_safety_value(value[key])
            for key in sorted(value)
        }
    if is_dataclass(value) and not isinstance(value, type):
        if not value_type.__module__.startswith("xpotato_sim.runtime.safety."):
            raise TypeError("safety input contains an unsupported DTO")
        public_fields = {
            item.name: _typed_safety_value(getattr(value, item.name))
            for item in fields(value)
            if not item.name.startswith("_")
        }
        if hasattr(value, "evaluated_candidate"):
            public_fields["evaluated_candidate"] = _typed_safety_value(value.evaluated_candidate)
        return {
            "dto_type": f"{value_type.__module__}.{value_type.__qualname__}",
            "fields": public_fields,
        }
    raise TypeError(
        "safety input contains a value outside the validated P2/P3/P4 DTO contract"
    )


def _decision_projection(decision: SafetyDecision) -> dict[str, object]:
    return {
        "action": decision.action.value,
        "assessments": [
            {
                "action": item.action.value,
                "component": item.component.value,
                "reason": item.reason.identity,
                "reason_provenance": item.reason.provenance,
            }
            for item in decision.assessments
        ],
        "candidate_id": decision.candidate_id,
        "provenance": decision.provenance,
        "reason": decision.reason.identity,
        "reason_message": decision.reason.operator_message,
        "reason_provenance": decision.reason.provenance,
        "schema_version": PHYSICAL_OUTPUT_SAFETY_BINDING_SCHEMA_VERSION,
    }


@dataclass(frozen=True, slots=True)
class PhysicalOutputSafetyTraceEvidence:
    """Output safety decisionのlifecycle traceへ保持する事実。"""

    safety_action: str
    gate_status: PhysicalOutputSafetyStatus
    gate_reason: str
    safety_reason_identity: str
    safety_provenance: tuple[str, ...]
    candidate_id: str | None
    target_robot_id: str
    software_revision: str
    checked_at_s: float
    request_sha256: str
    safety_input_sha256: str | None
    decision_sha256: str | None
    binding_sha256: str
    schema_version: str = PHYSICAL_OUTPUT_SAFETY_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PHYSICAL_OUTPUT_SAFETY_EVIDENCE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported physical output safety evidence schema_version: "
                f"{self.schema_version!r}"
            )
        if self.safety_action not in {action.value for action in SafetyDecisionAction}:
            raise ValueError("physical output safety evidence action is invalid")
        if self.gate_status not in _PHYSICAL_OUTPUT_SAFETY_STATUSES:
            raise ValueError("physical output safety evidence gate_status is invalid")
        _identifier("gate_reason", self.gate_reason)
        _identifier("safety_reason_identity", self.safety_reason_identity)
        if not isinstance(self.safety_provenance, tuple) or not all(
            isinstance(item, str) and item and item == item.strip()
            for item in self.safety_provenance
        ):
            raise TypeError("safety_provenance must contain non-empty strings")
        if len(set(self.safety_provenance)) != len(self.safety_provenance):
            raise ValueError("safety_provenance must be unique")
        _identifier("candidate_id", self.candidate_id, allow_none=True)
        _identifier("target_robot_id", self.target_robot_id)
        _identifier("software_revision", self.software_revision)
        object.__setattr__(
            self,
            "checked_at_s",
            _finite_timestamp("checked_at_s", self.checked_at_s),
        )
        for name in ("request_sha256", "binding_sha256"):
            digest = getattr(self, name)
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or digest.lower() != digest
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        for name in ("safety_input_sha256", "decision_sha256"):
            digest = getattr(self, name)
            if digest is not None and (
                not isinstance(digest, str)
                or len(digest) != 64
                or digest.lower() != digest
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(f"{name} must be null or a lowercase SHA-256 digest")

    def to_json_value(self) -> dict[str, object]:
        return {
            "binding_sha256": self.binding_sha256,
            "candidate_id": self.candidate_id,
            "checked_at_s": self.checked_at_s,
            "gate_reason": self.gate_reason,
            "gate_status": self.gate_status,
            "request_sha256": self.request_sha256,
            "safety_input_sha256": self.safety_input_sha256,
            "decision_sha256": self.decision_sha256,
            "safety_action": self.safety_action,
            "safety_provenance": list(self.safety_provenance),
            "safety_reason_identity": self.safety_reason_identity,
            "schema_version": self.schema_version,
            "software_revision": self.software_revision,
            "target_robot_id": self.target_robot_id,
        }

    @classmethod
    def from_json_value(cls, value: object) -> "PhysicalOutputSafetyTraceEvidence":
        if not isinstance(value, dict):
            raise ValueError("physical output safety evidence must be an object")
        expected = {
            "binding_sha256",
            "candidate_id",
            "checked_at_s",
            "gate_reason",
            "gate_status",
            "request_sha256",
            "safety_input_sha256",
            "decision_sha256",
            "safety_action",
            "safety_provenance",
            "safety_reason_identity",
            "schema_version",
            "software_revision",
            "target_robot_id",
        }
        if set(value) != expected:
            raise ValueError("physical output safety evidence fields are incomplete or unknown")
        provenance = value["safety_provenance"]
        if type(provenance) is not list or not all(type(item) is str for item in provenance):
            raise ValueError("physical output safety provenance must be a string array")
        checked_at_s = value["checked_at_s"]
        if isinstance(checked_at_s, bool) or not isinstance(checked_at_s, Real):
            raise ValueError("physical output safety checked_at_s must be numeric")
        if not all(
            type(value[name]) is str
            for name in (
                "binding_sha256",
                "gate_reason",
                "gate_status",
                "request_sha256",
                "safety_action",
                "safety_reason_identity",
                "schema_version",
                "software_revision",
                "target_robot_id",
            )
        ):
            raise ValueError("physical output safety evidence string fields are invalid")
        candidate_id = value["candidate_id"]
        if candidate_id is not None and type(candidate_id) is not str:
            raise ValueError("physical output safety candidate_id must be string or null")
        for name in ("safety_input_sha256", "decision_sha256"):
            if value[name] is not None and type(value[name]) is not str:
                raise ValueError(f"{name} must be string or null")
        return cls(
            safety_action=value["safety_action"],
            gate_status=value["gate_status"],
            gate_reason=value["gate_reason"],
            safety_reason_identity=value["safety_reason_identity"],
            safety_provenance=tuple(provenance),
            candidate_id=candidate_id,
            target_robot_id=value["target_robot_id"],
            software_revision=value["software_revision"],
            checked_at_s=checked_at_s,
            request_sha256=value["request_sha256"],
            safety_input_sha256=value["safety_input_sha256"],
            decision_sha256=value["decision_sha256"],
            binding_sha256=value["binding_sha256"],
            schema_version=value["schema_version"],
        )


@dataclass(frozen=True, slots=True)
class PhysicalOutputSafetyEvaluation:
    """canonical request bytesとupstream typed evidenceへ結合したP5 decision。"""

    request: PhysicalOutputRequest
    safety_input: SafetyInput | None
    decision: SafetyDecision | None
    checked_at_s: float
    status: PhysicalOutputSafetyStatus = field(init=False)
    reason: str = field(init=False)
    safety_action: SafetyDecisionAction = field(init=False)
    safety_reason_identity: str = field(init=False)
    safety_provenance: tuple[str, ...] = field(init=False)
    candidate_id: str | None = field(init=False)
    target_robot_id: str = field(init=False)
    software_revision: str = field(init=False)
    request_sha256: str = field(init=False)
    safety_input_sha256: str | None = field(init=False)
    decision_sha256: str | None = field(init=False)
    binding_sha256: str = field(init=False)
    _validation_error: str | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.request, PhysicalOutputRequest):
            raise TypeError("physical output safety evaluation requires PhysicalOutputRequest")
        checked_at_s = _finite_timestamp("checked_at_s", self.checked_at_s)
        request_bytes = self.request.to_json_bytes()
        request_sha256 = sha256(request_bytes).hexdigest()
        expected_candidate_id = physical_output_candidate_id(self.request)
        target_robot_id = self.request.target_robot_id
        software_revision = self.request.software_revision

        decision: SafetyDecision | None = None
        decision_error: str | None = None
        if self.decision is not None:
            if not isinstance(self.decision, SafetyDecision):
                raise TypeError("decision must be SafetyDecision or None")
            try:
                validate_safety_decision(self.decision)
                decision = self.decision
            except Exception:
                decision_error = "physical_safety_decision_invalid"

        safety_input: SafetyInput | None = None
        input_error: str | None = None
        if self.safety_input is not None:
            if not isinstance(self.safety_input, SafetyInput):
                raise TypeError("safety_input must be SafetyInput or None")
            try:
                validate_safety_input(self.safety_input)
                safety_input = self.safety_input
            except Exception:
                input_error = "physical_safety_input_invalid"

        safety_action = (
            decision.action if decision is not None else SafetyDecisionAction.INVALID
        )
        safety_reason_identity = (
            decision.reason.identity
            if decision is not None
            else "input:invalid_safety_input"
        )
        safety_provenance = decision.provenance if decision is not None else ()
        candidate_id = (
            safety_input.candidate_id
            if safety_input is not None
            else decision.candidate_id
            if decision is not None
            else None
        )
        safety_input_sha256 = (
            _digest(_typed_safety_value(safety_input))
            if safety_input is not None
            else None
        )
        decision_sha256 = (
            _digest(_decision_projection(decision)) if decision is not None else None
        )

        validation_error = input_error or decision_error
        candidate_mismatch = False
        decision_mismatch = False
        if validation_error is None and safety_input is not None and decision is not None:
            candidate_mismatch = (
                safety_input.candidate_id != expected_candidate_id
                or decision.candidate_id != expected_candidate_id
            )
        if validation_error is None and safety_input is not None and decision is not None:
            expected = evaluate_physical_safety(safety_input)
            try:
                validate_safety_decision(expected)
            except Exception:
                validation_error = "physical_safety_decision_invalid"
            if validation_error is None and expected != decision:
                decision_mismatch = True

        status: PhysicalOutputSafetyStatus
        reason: str
        if validation_error is not None:
            status = "invalid"
            reason = validation_error
        elif safety_input is None or decision is None:
            status = "invalid"
            reason = "physical_safety_binding_missing"
            validation_error = reason
        elif candidate_mismatch:
            status = "rejected"
            reason = "physical_safety_candidate_mismatch"
            validation_error = reason
        elif decision_mismatch:
            status = "invalid"
            reason = "physical_safety_decision_mismatch"
            validation_error = reason
        else:
            p2_robot = (
                safety_input.limit_resolution.robot_id
                if safety_input.limit_resolution is not None
                else None
            )
            p3_robot = (
                safety_input.collision.context.robot_id
                if safety_input.collision is not None
                else None
            )
            if p2_robot is None or p3_robot is None:
                status = "unavailable"
                reason = "physical_safety_robot_identity_unavailable"
                validation_error = reason
            elif p2_robot != p3_robot or p2_robot != target_robot_id:
                status = "rejected"
                reason = "physical_safety_target_robot_mismatch"
                validation_error = reason
            else:
                revision_token = f"software_revision:{software_revision}"
                if (
                    revision_token not in safety_input.provenance
                    or revision_token not in decision.provenance
                ):
                    status = "rejected"
                    reason = "physical_safety_software_revision_mismatch"
                    validation_error = reason
                else:
                    status = _ACTION_STATUS[decision.action]
                    reason = (
                        decision.reason.identity
                        if decision.action is not SafetyDecisionAction.ALLOW
                        else "physical_safety_allowed"
                    )
                    if decision.action is SafetyDecisionAction.ALLOW:
                        candidate_error = _evaluated_candidate_error(self.request, safety_input)
                        if candidate_error is not None:
                            status = "rejected"
                            reason = candidate_error
                            validation_error = reason

        binding_sha256 = _digest(
            {
                "binding_schema_version": PHYSICAL_OUTPUT_SAFETY_BINDING_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "checked_at_s": checked_at_s,
                "decision_sha256": decision_sha256,
                "reason": reason,
                "request_sha256": request_sha256,
                "safety_action": safety_action.value,
                "safety_input_sha256": safety_input_sha256,
                "status": status,
                "target_robot_id": target_robot_id,
                "software_revision": software_revision,
            }
        )

        object.__setattr__(self, "checked_at_s", checked_at_s)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "safety_action", safety_action)
        object.__setattr__(self, "safety_reason_identity", safety_reason_identity)
        object.__setattr__(self, "safety_provenance", safety_provenance)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "target_robot_id", target_robot_id)
        object.__setattr__(self, "software_revision", software_revision)
        object.__setattr__(self, "request_sha256", request_sha256)
        object.__setattr__(self, "safety_input_sha256", safety_input_sha256)
        object.__setattr__(self, "decision_sha256", decision_sha256)
        object.__setattr__(self, "binding_sha256", binding_sha256)
        object.__setattr__(self, "_validation_error", validation_error)

    @property
    def sendable(self) -> bool:
        try:
            validate_physical_output_safety_evaluation(self)
        except Exception:
            return False
        return self.status == "allowed"

    def to_sendable_request(self) -> "PhysicalOutputSendableRequest":
        validate_physical_output_safety_evaluation(self)
        if self.status != "allowed" or self.safety_action is not SafetyDecisionAction.ALLOW:
            raise ValueError("physical output request lacks an explicit safety allow")
        return PhysicalOutputSendableRequest(self)

    def to_trace_evidence(self) -> PhysicalOutputSafetyTraceEvidence:
        validate_physical_output_safety_evaluation(self)
        return PhysicalOutputSafetyTraceEvidence(
            safety_action=self.safety_action.value,
            gate_status=self.status,
            gate_reason=self.reason,
            safety_reason_identity=self.safety_reason_identity,
            safety_provenance=self.safety_provenance,
            candidate_id=self.candidate_id,
            target_robot_id=self.target_robot_id,
            software_revision=self.software_revision,
            checked_at_s=self.checked_at_s,
            request_sha256=self.request_sha256,
            safety_input_sha256=self.safety_input_sha256,
            decision_sha256=self.decision_sha256,
            binding_sha256=self.binding_sha256,
        )


@dataclass(frozen=True, slots=True)
class PhysicalOutputSendableRequest:
    """P5 allow bindingを持つoutput intent。ここでは送信しない。"""

    evaluation: PhysicalOutputSafetyEvaluation

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation, PhysicalOutputSafetyEvaluation):
            raise TypeError(
                "physical output sendable request requires PhysicalOutputSafetyEvaluation"
            )
        validate_physical_output_safety_evaluation(self.evaluation)
        if not self.evaluation.sendable:
            raise ValueError("physical output sendable request requires explicit P5 allow")

    @property
    def request(self) -> PhysicalOutputRequest:
        return self.evaluation.request

    @property
    def request_sha256(self) -> str:
        return self.evaluation.request_sha256

    @property
    def binding_sha256(self) -> str:
        return self.evaluation.binding_sha256


def bind_physical_output_safety(
    request: PhysicalOutputRequest,
    safety_input: SafetyInput,
    decision: SafetyDecision,
    *,
    checked_at_s: float,
) -> PhysicalOutputSafetyEvaluation:
    """canonicalなP5 evaluationをexact output request bytesへ結合する。"""

    if not isinstance(request, PhysicalOutputRequest):
        raise TypeError("safety binding requires PhysicalOutputRequest")
    if not isinstance(safety_input, SafetyInput):
        raise TypeError("safety binding requires SafetyInput")
    if not isinstance(decision, SafetyDecision):
        raise TypeError("safety binding requires SafetyDecision")
    return PhysicalOutputSafetyEvaluation(
        request=request,
        safety_input=safety_input,
        decision=decision,
        checked_at_s=checked_at_s,
    )


def evaluate_and_bind_physical_output_safety(
    request: PhysicalOutputRequest,
    safety_input: SafetyInput,
    *,
    checked_at_s: float,
) -> PhysicalOutputSafetyEvaluation:
    """P5を一度composeし、そのresultをoutput requestへ結合する。"""

    decision = evaluate_physical_safety(safety_input)
    return bind_physical_output_safety(
        request,
        safety_input,
        decision,
        checked_at_s=checked_at_s,
    )


def validate_physical_output_safety_evaluation(
    evaluation: PhysicalOutputSafetyEvaluation,
) -> PhysicalOutputSafetyEvaluation:
    """request/P5 bindingのpublic fieldsを再計算して検証する。"""

    if not isinstance(evaluation, PhysicalOutputSafetyEvaluation):
        raise TypeError("expected PhysicalOutputSafetyEvaluation")
    canonical = PhysicalOutputSafetyEvaluation(
        request=evaluation.request,
        safety_input=evaluation.safety_input,
        decision=evaluation.decision,
        checked_at_s=evaluation.checked_at_s,
    )
    if canonical != evaluation:
        raise ValueError("physical output safety evaluation binding is inconsistent")
    return evaluation


def validate_physical_output_sendable_request(
    request: PhysicalOutputSendableRequest,
) -> PhysicalOutputSendableRequest:
    """lifecycle受理前にallow専用output wrapperを再検証する。"""

    if not isinstance(request, PhysicalOutputSendableRequest):
        raise TypeError("expected PhysicalOutputSendableRequest")
    validate_physical_output_safety_evaluation(request.evaluation)
    if request.evaluation.status != "allowed":
        raise ValueError("physical output request lacks an explicit safety allow")
    return request


__all__ = [
    "PHYSICAL_OUTPUT_CANDIDATE_ID_PREFIX",
    "PHYSICAL_OUTPUT_SAFETY_BINDING_SCHEMA_VERSION",
    "PHYSICAL_OUTPUT_SAFETY_EVIDENCE_SCHEMA_VERSION",
    "PhysicalOutputSafetyEvaluation",
    "PhysicalOutputSafetyStatus",
    "PhysicalOutputSafetyTraceEvidence",
    "PhysicalOutputSendableRequest",
    "bind_physical_output_safety",
    "compose_physical_output_safety_input",
    "evaluate_and_bind_physical_output_safety",
    "physical_output_candidate_id",
    "validate_physical_output_safety_evaluation",
    "validate_physical_output_sendable_request",
]
