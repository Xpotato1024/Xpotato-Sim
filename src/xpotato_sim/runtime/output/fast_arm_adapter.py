"""FastArm固有の出力、二重permission、#509 evidenceを結ぶruntime owner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from numbers import Real
from threading import RLock
from time import monotonic
from typing import Literal

from xpotato_sim.plugins.robots.fast_arm.adapter.physical_output import (
    FAST_ARM_JOINT_POSITION_SEMANTICS,
    FastArmJointWireCommand,
    FastArmOutputMapping,
    build_fast_arm_joint_wire_command,
)
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.output.lifecycle import PhysicalOutputLifecycle, PhysicalOutputLifecycleResult
from xpotato_sim.runtime.output.fast_arm_observation import (
    FAST_ARM_ACK_STATUS,
    FastArmAcknowledgementEvidence,
    FastArmPendingObservation as _PendingAcknowledgement,
    expired_fast_arm_acknowledgement,
    pending_fast_arm_acknowledgement,
    resolve_fast_arm_router_datagram,
)
from xpotato_sim.runtime.output.safety_gate import (
    PhysicalOutputSafetyEvaluation,
    PhysicalOutputSendableRequest,
    physical_output_candidate_id,
    validate_physical_output_safety_evaluation,
)
from xpotato_sim.runtime.output.transport_adapter import (
    PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2,
    PhysicalOutputCodecIdentity,
    PhysicalOutputTransportAdapter,
    PhysicalOutputTransportAuthorizationGrant,
    PhysicalOutputTransportConfig,
    PhysicalOutputTransportResult,
    PhysicalOutputTransportPreparedDispatch,
    PhysicalOutputWireMessage,
    _create_physical_output_transport_authorization_grant,
)
from xpotato_sim.runtime.safety.physical_limits import (
    EvidenceStatus,
    LimitQuantity,
    LimitSpace,
    PhysicalSafetyEnvelope,
)
from xpotato_sim.runtime.safety.physical_safety_core import SafetyInput
from xpotato_sim.schemas import PhysicalOutputPermission
from xpotato_sim.transport.osc import OscMessage


FAST_ARM_PHYSICAL_EVIDENCE_SCHEMA_VERSION = "fast-arm-physical-evidence-acceptance/v1"
FAST_ARM_PHYSICAL_EVIDENCE_HANDOFF_SCHEMA_VERSION = "fast-arm-physical-evidence-handoff/v1"
FAST_ARM_SESSION_STATE = Literal["disarmed", "armed", "active", "stopped", "aborted", "failed"]


def _identifier(name: str, value: object) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty canonical string")
    return value


def _digest(name: str, value: object) -> str:
    if type(value) is not str or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _timestamp(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _json_digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def fast_arm_envelope_provenance_token(envelope: PhysicalSafetyEnvelope, envelope_sha256: str) -> str:
    _digest("envelope_sha256", envelope_sha256)
    return f"physical_safety_envelope:{envelope.envelope_id}:sha256:{envelope_sha256}"


def _reject_duplicate_handoff_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field in FastArm physical evidence handoff: {key!r}")
        result[key] = value
    return result


def _reject_handoff_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


@dataclass(frozen=True, slots=True)
class FastArmPhysicalEvidenceHandoff:
    """#509の受理済み物理観測を結ぶcanonical handoff文書。"""

    acceptance_reference: str
    target_robot_id: str
    profile_id: str
    profile_contract_version: int
    model_contract_version: str
    envelope_sha256: str
    joint_measurement_references: tuple[tuple[str, str], ...]
    accepted_at_s: float
    issue_id: str = "#509"
    status: Literal["accepted"] = "accepted"
    observation_class: Literal["physical_measurement"] = "physical_measurement"
    schema_version: str = FAST_ARM_PHYSICAL_EVIDENCE_HANDOFF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            self.schema_version != FAST_ARM_PHYSICAL_EVIDENCE_HANDOFF_SCHEMA_VERSION
            or self.issue_id != "#509"
            or self.status != "accepted"
            or self.observation_class != "physical_measurement"
        ):
            raise ValueError("#509 accepted physical-measurement handoff is required")
        for name, value in (
            ("acceptance_reference", self.acceptance_reference),
            ("target_robot_id", self.target_robot_id),
            ("profile_id", self.profile_id),
            ("model_contract_version", self.model_contract_version),
        ):
            _identifier(name, value)
        _digest("envelope_sha256", self.envelope_sha256)
        if type(self.profile_contract_version) is not int or self.profile_contract_version < 1:
            raise ValueError("profile_contract_version must be a positive integer")
        if type(self.joint_measurement_references) is not tuple or not self.joint_measurement_references:
            raise TypeError("joint_measurement_references must be a non-empty tuple")
        if any(type(pair) is not tuple or len(pair) != 2 for pair in self.joint_measurement_references):
            raise TypeError("joint_measurement_references must contain joint/reference pairs")
        pairs = tuple(
            (_identifier("joint_name", joint), _identifier("measurement reference", reference))
            for joint, reference in self.joint_measurement_references
        )
        if len({joint for joint, _ in pairs}) != len(pairs) or len({reference for _, reference in pairs}) != len(pairs):
            raise ValueError("measurement references must map unique joints and sources")
        object.__setattr__(self, "joint_measurement_references", pairs)
        object.__setattr__(self, "accepted_at_s", _timestamp("accepted_at_s", self.accepted_at_s))

    def to_dict(self) -> dict[str, object]:
        return {
            "acceptance_reference": self.acceptance_reference,
            "accepted_at_s": self.accepted_at_s,
            "envelope_sha256": self.envelope_sha256,
            "issue_id": self.issue_id,
            "joint_measurement_references": [
                {"joint_name": joint, "measurement_reference": reference}
                for joint, reference in self.joint_measurement_references
            ],
            "model_contract_version": self.model_contract_version,
            "observation_class": self.observation_class,
            "profile_contract_version": self.profile_contract_version,
            "profile_id": self.profile_id,
            "schema_version": self.schema_version,
            "status": self.status,
            "target_robot_id": self.target_robot_id,
        }

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, document: bytes) -> FastArmPhysicalEvidenceHandoff:
        if type(document) is not bytes:
            raise TypeError("FastArm physical evidence handoff must be UTF-8 bytes")
        if document.startswith(b"\xef\xbb\xbf"):
            raise ValueError("FastArm physical evidence handoff must not contain a UTF-8 BOM")
        try:
            text = document.decode("utf-8", errors="strict")
            raw = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_handoff_keys,
                parse_constant=_reject_handoff_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("FastArm physical evidence handoff must be valid UTF-8 JSON") from exc
        if type(raw) is not dict:
            raise ValueError("FastArm physical evidence handoff must be a JSON object")
        expected_fields = {
            "acceptance_reference",
            "accepted_at_s",
            "envelope_sha256",
            "issue_id",
            "joint_measurement_references",
            "model_contract_version",
            "observation_class",
            "profile_contract_version",
            "profile_id",
            "schema_version",
            "status",
            "target_robot_id",
        }
        if set(raw) != expected_fields:
            raise ValueError("FastArm physical evidence handoff has missing or unknown fields")
        raw_references = raw["joint_measurement_references"]
        if type(raw_references) is not list:
            raise ValueError("joint_measurement_references must be a JSON array")
        references: list[tuple[str, str]] = []
        for item in raw_references:
            if type(item) is not dict or set(item) != {"joint_name", "measurement_reference"}:
                raise ValueError("joint measurement reference entry has missing or unknown fields")
            references.append((item["joint_name"], item["measurement_reference"]))
        handoff = cls(
            acceptance_reference=raw["acceptance_reference"],
            target_robot_id=raw["target_robot_id"],
            profile_id=raw["profile_id"],
            profile_contract_version=raw["profile_contract_version"],
            model_contract_version=raw["model_contract_version"],
            envelope_sha256=raw["envelope_sha256"],
            joint_measurement_references=tuple(references),
            accepted_at_s=raw["accepted_at_s"],
            issue_id=raw["issue_id"],
            status=raw["status"],
            observation_class=raw["observation_class"],
            schema_version=raw["schema_version"],
        )
        if handoff.to_json_bytes() != document:
            raise ValueError("FastArm physical evidence handoff must use canonical JSON bytes")
        return handoff


@dataclass(frozen=True, slots=True)
class FastArmPhysicalEvidenceAcceptance:
    """digest照合済みの#509 handoffとtyped envelope。"""

    acceptance_reference: str
    acceptance_sha256: str
    handoff_bytes: bytes
    target_robot_id: str
    profile_id: str
    profile_contract_version: int
    model_contract_version: str
    observation_class: Literal["physical_measurement"]
    envelope: PhysicalSafetyEnvelope
    envelope_sha256: str
    joint_measurement_references: tuple[tuple[str, str], ...]
    accepted_at_s: float
    issue_id: str = "#509"
    status: Literal["accepted"] = "accepted"
    schema_version: str = FAST_ARM_PHYSICAL_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FAST_ARM_PHYSICAL_EVIDENCE_SCHEMA_VERSION or self.issue_id != "#509" or self.status != "accepted":
            raise ValueError("#509 accepted physical evidence is required")
        for name, value in (("acceptance_reference", self.acceptance_reference), ("target_robot_id", self.target_robot_id), ("profile_id", self.profile_id), ("model_contract_version", self.model_contract_version)):
            _identifier(name, value)
        _digest("acceptance_sha256", self.acceptance_sha256)
        _digest("envelope_sha256", self.envelope_sha256)
        if type(self.profile_contract_version) is not int or self.profile_contract_version < 1:
            raise ValueError("profile_contract_version must be a positive integer")
        if type(self.envelope) is not PhysicalSafetyEnvelope:
            raise TypeError("accepted evidence requires PhysicalSafetyEnvelope")
        if sha256(self.envelope.to_json_bytes()).hexdigest() != self.envelope_sha256:
            raise ValueError("accepted envelope digest does not match typed envelope")
        if self.observation_class != "physical_measurement":
            raise ValueError("accepted evidence must use physical_measurement observations")
        if type(self.joint_measurement_references) is not tuple or not self.joint_measurement_references:
            raise TypeError("joint_measurement_references must be a non-empty tuple")
        if any(type(pair) is not tuple or len(pair) != 2 for pair in self.joint_measurement_references):
            raise TypeError("joint_measurement_references must contain joint/reference pairs")
        pairs = tuple((_identifier("joint_name", j), _identifier("measurement reference", ref)) for j, ref in self.joint_measurement_references)
        if len({j for j, _ in pairs}) != len(pairs) or len({ref for _, ref in pairs}) != len(pairs):
            raise ValueError("measurement references must map unique joints and sources")
        object.__setattr__(self, "joint_measurement_references", pairs)
        object.__setattr__(self, "accepted_at_s", _timestamp("accepted_at_s", self.accepted_at_s))
        if type(self.handoff_bytes) is not bytes:
            raise TypeError("accepted evidence requires the exact handoff bytes")
        if sha256(self.handoff_bytes).hexdigest() != self.acceptance_sha256:
            raise ValueError("accepted handoff digest does not match its bytes")
        handoff = FastArmPhysicalEvidenceHandoff.from_json_bytes(self.handoff_bytes)
        expected_handoff_fields = (
            self.acceptance_reference,
            self.target_robot_id,
            self.profile_id,
            self.profile_contract_version,
            self.model_contract_version,
            self.envelope_sha256,
            self.joint_measurement_references,
            self.accepted_at_s,
            self.issue_id,
            self.status,
            self.observation_class,
        )
        actual_handoff_fields = (
            handoff.acceptance_reference,
            handoff.target_robot_id,
            handoff.profile_id,
            handoff.profile_contract_version,
            handoff.model_contract_version,
            handoff.envelope_sha256,
            handoff.joint_measurement_references,
            handoff.accepted_at_s,
            handoff.issue_id,
            handoff.status,
            handoff.observation_class,
        )
        if actual_handoff_fields != expected_handoff_fields:
            raise ValueError("accepted handoff bytes do not match typed physical evidence")

    @property
    def measurement_reference_by_joint(self) -> dict[str, str]:
        return dict(self.joint_measurement_references)


@dataclass(frozen=True, slots=True)
class FastArmPhysicalOutputResult:
    """transport resultとFastArm側router observationを別々に返す。"""

    status: Literal["accepted", "rejected", "transmission_attempted", "stopped", "aborted", "failed"]
    reason: str | None = None
    transport_result: PhysicalOutputTransportResult | None = None
    acknowledgement: FastArmAcknowledgementEvidence = FastArmAcknowledgementEvidence("not_applicable", "no_transport_attempt")
    lifecycle_result: PhysicalOutputLifecycleResult | None = None

    def __post_init__(self) -> None:
        if self.status not in {"accepted", "rejected", "transmission_attempted", "stopped", "aborted", "failed"}:
            raise ValueError("unknown FastArm output result status")
        if self.reason is not None:
            _identifier("result reason", self.reason)


class FastArmWireEncoder:
    """generic transportへFastArm wire semanticsを渡すcodec。"""

    requires_external_authorization = True

    def __init__(self, mapping: FastArmOutputMapping, identity: PhysicalOutputCodecIdentity) -> None:
        if type(mapping) is not FastArmOutputMapping or type(identity) is not PhysicalOutputCodecIdentity:
            raise TypeError("FastArm encoder requires typed mapping and codec identity")
        if identity != fast_arm_codec_identity(mapping):
            raise ValueError("FastArm encoder identity does not match mapping")
        self.mapping = mapping
        self.identity = identity

    def encode(self, envelope: PhysicalOutputWireMessage) -> OscMessage:
        if type(envelope) is not PhysicalOutputWireMessage:
            raise TypeError("FastArm codec requires PhysicalOutputWireMessage")
        command = build_fast_arm_joint_wire_command(envelope.request, self.mapping, attempt_id=envelope.attempt_id)
        return OscMessage(command.address, command.osc_arguments)


def fast_arm_codec_identity(mapping: FastArmOutputMapping) -> PhysicalOutputCodecIdentity:
    if type(mapping) is not FastArmOutputMapping:
        raise TypeError("FastArm codec identity requires FastArmOutputMapping")
    return PhysicalOutputCodecIdentity.from_settings(
        "fast-arm-joint-osc",
        "v1",
        {
            "angle_input_unit": mapping.angle_input_unit,
            "angle_output_unit": mapping.angle_output_unit,
            "command_semantics": mapping.command_semantics,
            "joint_map": [[source, target] for source, target in mapping.joint_map],
            "mapping_sha256": mapping.identity_sha256,
            "router_ack_schema": "fast-arm-router-observation/v1",
            "wire_schema": "fast-arm-joint-wire-command/v1",
        },
    )


def create_fast_arm_wire_encoder(mapping: FastArmOutputMapping) -> FastArmWireEncoder:
    return FastArmWireEncoder(mapping, fast_arm_codec_identity(mapping))

@dataclass(frozen=True, slots=True)
class FastArmPreparedSubmission:
    """当該sessionだけが消費できる要求。コピー・再使用・別session流用を拒否。"""
    owner: object
    prepared: PhysicalOutputTransportPreparedDispatch
    sendable: PhysicalOutputSendableRequest
    grant: PhysicalOutputTransportAuthorizationGrant
    generation: int
    transmission_permission: PhysicalOutputPermission


class FastArmPhysicalOutputSession:
    """明示gate後にだけFastArm generic transportへ一回分を渡す。"""

    def __init__(
        self,
        *,
        profile: RobotProfile,
        runtime_plugin_id: str,
        runtime_robot_id: str,
        target_robot_id: str,
        endpoint_id: str,
        session_id: str,
        mapping: FastArmOutputMapping,
        accepted_evidence: FastArmPhysicalEvidenceAcceptance,
        transport_config: PhysicalOutputTransportConfig,
        transport_adapter: PhysicalOutputTransportAdapter,
        cadence_s: float,
        authorization_ttl_s: float,
        acknowledgement_timeout_s: float,
        clock: object | None = None,
    ) -> None:
        if type(profile) is not RobotProfile or type(mapping) is not FastArmOutputMapping:
            raise TypeError("FastArm session requires typed Robot Profile and mapping")
        if type(accepted_evidence) is not FastArmPhysicalEvidenceAcceptance:
            raise TypeError("#509 accepted evidence is required")
        if type(transport_config) is not PhysicalOutputTransportConfig or type(transport_adapter) is not PhysicalOutputTransportAdapter:
            raise TypeError("FastArm session requires typed generic transport")
        if transport_adapter.config is not transport_config:
            raise ValueError("transport adapter must use the exact supplied config")
        if (
            transport_config.schema_version != PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2
            or not transport_config.external_authorization_required
            or transport_config.mode != "transmission_enabled"
        ):
            raise ValueError("FastArm transmission requires transport config v2 external authorization")
        for name, value in (("runtime_plugin_id", runtime_plugin_id), ("runtime_robot_id", runtime_robot_id), ("target_robot_id", target_robot_id), ("endpoint_id", endpoint_id), ("session_id", session_id)):
            _identifier(name, value)
        for name, value in (("cadence_s", cadence_s), ("authorization_ttl_s", authorization_ttl_s), ("acknowledgement_timeout_s", acknowledgement_timeout_s)):
            if type(value) not in {int, float} or not isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        if (
            runtime_plugin_id != profile.profile_id
            or runtime_robot_id != target_robot_id
            or target_robot_id != transport_config.target_robot_id
            or endpoint_id != transport_config.endpoint.endpoint_id
        ):
            raise ValueError("FastArm runtime, profile, target, and endpoint identities differ")
        if (
            mapping.profile_id != profile.profile_id
            or mapping.profile_contract_version != profile.profile_contract_version
            or mapping.model_contract_version != profile.model_contract_version
            or mapping.profile_joint_order != profile.canonical_joint_names
        ):
            raise ValueError("FastArm mapping does not match the resolved Robot Profile")
        if (
            accepted_evidence.target_robot_id != target_robot_id
            or accepted_evidence.profile_id != profile.profile_id
            or accepted_evidence.profile_contract_version != profile.profile_contract_version
            or accepted_evidence.model_contract_version != profile.model_contract_version
            or accepted_evidence.envelope.robot_id != target_robot_id
            or accepted_evidence.envelope.model_id != profile.model_contract_version
        ):
            raise ValueError("accepted #509 evidence does not match FastArm profile and target")
        if transport_config.expected_codec_identity != fast_arm_codec_identity(mapping):
            raise ValueError("transport codec identity does not match FastArm mapping")
        if transport_config.minimum_cadence_s < float(cadence_s):
            raise ValueError("transport cadence gate is weaker than FastArm session cadence")
        self.profile = profile
        self.runtime_plugin_id = runtime_plugin_id
        self.runtime_robot_id = runtime_robot_id
        self.target_robot_id = target_robot_id
        self.endpoint_id = endpoint_id
        self.session_id = session_id
        self.mapping = mapping
        self.accepted_evidence = accepted_evidence
        self.transport_config = transport_config
        self.transport_adapter = transport_adapter
        self.cadence_s = float(cadence_s)
        self.authorization_ttl_s = float(authorization_ttl_s)
        self.acknowledgement_timeout_s = float(acknowledgement_timeout_s)
        self._clock = monotonic if clock is None else clock
        self.lifecycle = PhysicalOutputLifecycle(session_id, clock=self._clock)
        self._lock = RLock()
        self._generation = 0
        self._state: FAST_ARM_SESSION_STATE = "disarmed"
        self._dispatch_inflight = False
        self._prepared_submission: FastArmPreparedSubmission | None = None
        self._active_grant: PhysicalOutputTransportAuthorizationGrant | None = None
        self._pending_acknowledgement: _PendingAcknowledgement | None = None
        self._physical_permission: PhysicalOutputPermission | None = None
        self._transmission_permission: PhysicalOutputPermission | None = None
        self._authorization_context_sha256: str | None = None
        self._last_acknowledgement = FastArmAcknowledgementEvidence("not_applicable", "no_transport_attempt")

    @property
    def state(self) -> FAST_ARM_SESSION_STATE:
        with self._lock:
            return self._state

    @property
    def latest_sendable_request(self) -> PhysicalOutputSendableRequest | None:
        return self.lifecycle.latest_sendable_request

    @property
    def pending_acknowledgement(self) -> FastArmAcknowledgementEvidence:
        with self._lock:
            pending = self._pending_acknowledgement
            if pending is None:
                return self._last_acknowledgement
            return pending_fast_arm_acknowledgement(pending)

    def _now(self, override: float | None) -> float:
        return _timestamp("clock", self._clock() if override is None else override)

    def _failure_timestamp(self) -> float:
        try:
            return _timestamp("failure clock", self._clock())
        except Exception:
            return monotonic()

    def _fail_closed_locked(
        self,
        reason: str,
        *,
        acknowledgement_reason: str | None = None,
    ) -> PhysicalOutputLifecycleResult:
        pending = self._pending_acknowledgement
        self._invalidate_locked()
        timestamp_s = self._failure_timestamp()
        result = self.lifecycle.fail(reason, timestamp_s=timestamp_s)
        self._state = "failed"
        self._last_acknowledgement = FastArmAcknowledgementEvidence(
            "unavailable",
            acknowledgement_reason or reason,
            attempt_id=None if pending is None else pending.attempt_id,
            source_token=(
                None
                if pending is None
                else pending.expected_command.source_token
            ),
            target_robot_id=(
                None
                if pending is None
                else pending.expected_command.target_robot_id
            ),
        )
        return result

    def arm(self, physical_permission: PhysicalOutputPermission, transmission_permission: PhysicalOutputPermission, *, now_s: float | None = None) -> PhysicalOutputLifecycleResult:
        with self._lock:
            try:
                now = self._now(now_s)
            except Exception:
                return self._fail_closed_locked("fast_arm_clock_invalid")
            if self._state != "disarmed":
                return self.lifecycle.fail("fast_arm_rearm_requires_new_session", timestamp_s=now)
            if type(physical_permission) is not PhysicalOutputPermission or physical_permission.mode != "physical_actuation" or not physical_permission.allows_physical_actuation:
                return self.lifecycle.fail("fast_arm_physical_actuation_permission_required", timestamp_s=now)
            if type(transmission_permission) is not PhysicalOutputPermission or transmission_permission.mode != "transmission_enabled" or not transmission_permission.allows_transmission:
                return self.lifecycle.fail("fast_arm_transmission_permission_required", timestamp_s=now)
            gate = self.transport_config.operator_enable
            if gate is None or gate.operator_id != transmission_permission.operator_id or gate.enable_token_id != transmission_permission.enable_token_id:
                return self.lifecycle.fail("fast_arm_transport_operator_enable_mismatch", timestamp_s=now)
            self._physical_permission = physical_permission
            self._transmission_permission = transmission_permission
            self._authorization_context_sha256 = self._authorization_context_digest(physical_permission, transmission_permission)
            result = self.lifecycle.arm(transmission_permission, timestamp_s=now)
            if result.accepted:
                self._state = "armed"
                self._generation += 1
            return result

    def _authorization_context_digest(self, physical_permission: PhysicalOutputPermission, transmission_permission: PhysicalOutputPermission) -> str:
        return _json_digest({
            "accepted_evidence_sha256": self.accepted_evidence.acceptance_sha256,
            "envelope_sha256": self.accepted_evidence.envelope_sha256,
            "mapping_sha256": self.mapping.identity_sha256,
            "physical_permission_sha256": sha256(physical_permission.to_json_bytes()).hexdigest(),
            "profile_contract_version": self.profile.profile_contract_version,
            "profile_id": self.profile.profile_id,
            "runtime_plugin_id": self.runtime_plugin_id,
            "runtime_robot_id": self.runtime_robot_id,
            "session_id": self.session_id,
            "target_robot_id": self.target_robot_id,
            "transmission_permission_sha256": sha256(transmission_permission.to_json_bytes()).hexdigest(),
            "transport_config_sha256": sha256(self.transport_config.to_json_bytes()).hexdigest(),
        })

    def _validate_physical_evidence(self, evaluation: PhysicalOutputSafetyEvaluation) -> str | None:
        try:
            validate_physical_output_safety_evaluation(evaluation)
        except Exception:
            return "fast_arm_safety_evaluation_invalid"
        if evaluation.status != "allowed" or evaluation.safety_input is None or evaluation.decision is None:
            return "fast_arm_safety_allow_required"
        request = evaluation.request
        candidate = physical_output_candidate_id(request)
        if (
            request.target_robot_id != self.target_robot_id
            or request.endpoint_id != self.endpoint_id
            or request.software_revision != self.transport_config.software_revision
            or request.session_id != self.session_id
            or request.command_semantics != FAST_ARM_JOINT_POSITION_SEMANTICS
            or request.cadence_s != self.cadence_s
            or evaluation.candidate_id != candidate
            or evaluation.safety_input.candidate_id != candidate
            or evaluation.decision.candidate_id != candidate
        ):
            return "fast_arm_request_identity_or_semantics_mismatch"
        try:
            build_fast_arm_joint_wire_command(request, self.mapping, attempt_id="preflight-token")
        except Exception:
            return "fast_arm_joint_mapping_invalid"
        safety_input = evaluation.safety_input
        if type(safety_input) is not SafetyInput or safety_input.limit_resolution is None:
            return "fast_arm_physical_measurement_evidence_missing"
        resolution = safety_input.limit_resolution
        envelope = self.accepted_evidence.envelope
        expected_names = self.profile.canonical_joint_names
        if resolution.robot_id != self.target_robot_id or resolution.expected_joint_names != expected_names or not resolution.authoritative:
            return "fast_arm_physical_measurement_resolution_mismatch"
        if safety_input.collision is None or safety_input.collision.context.robot_id != self.target_robot_id or safety_input.collision.context.model_id != envelope.model_id:
            return "fast_arm_collision_identity_mismatch"
        token = fast_arm_envelope_provenance_token(envelope, self.accepted_evidence.envelope_sha256)
        if token not in safety_input.provenance or token not in evaluation.decision.provenance:
            return "fast_arm_envelope_provenance_mismatch"
        refs = self.accepted_evidence.measurement_reference_by_joint
        if tuple(refs) != expected_names:
            return "fast_arm_measurement_reference_order_mismatch"
        for joint_name in expected_names:
            try:
                physical_limit = envelope.limit_for(joint_name, quantity=LimitQuantity.POSITION, space=LimitSpace.JOINT)
                bound = resolution.bound_for(joint_name)
            except (KeyError, TypeError, ValueError):
                return "fast_arm_joint_limit_missing"
            source = physical_limit.source
            if (
                physical_limit.status is not EvidenceStatus.AUTHORITATIVE
                or source.source_kind != "physical_measurement"
                or not source.is_physical_evidence
                or source.evidence_reference != refs[joint_name]
                or physical_limit.unit != "rad"
                or bound.status.value != "resolved_authoritative"
                or bound.lower_rad != physical_limit.lower
                or bound.upper_rad != physical_limit.upper
                or not any(item.source == source and item.status.value == "match" and item.lower == physical_limit.lower and item.upper == physical_limit.upper for item in bound.parity)
            ):
                return "fast_arm_joint_physical_measurement_mismatch"
        dynamic = safety_input.dynamic
        joint_names = getattr(dynamic, "joint_names", None)
        if joint_names is None:
            joint_names = getattr(dynamic, "expected_joint_names", None)
        if joint_names != expected_names:
            return "fast_arm_dynamic_joint_identity_mismatch"
        return None

    def submit(self, evaluation: PhysicalOutputSafetyEvaluation, *, now_s: float | None = None,
               pre_dispatch_check: Callable[[], bool] | None = None) -> FastArmPhysicalOutputResult:
        """旧単腕入口を維持。callbackは追加vetoのみで許可を生成しない。"""
        if pre_dispatch_check is not None and not callable(pre_dispatch_check):
            raise TypeError("pre_dispatch_check must be callable or None")
        ticket = self.prepare_submission(evaluation, now_s=now_s)
        if type(ticket) is not FastArmPreparedSubmission:
            return ticket
        grant = ticket.grant
        if pre_dispatch_check is not None:
            try:
                ready = pre_dispatch_check()
            except Exception as failure:
                with self._lock:
                    grant.revoke()
                    if self._state in {"armed", "active"}:
                        try:
                            self._fail_closed_locked("fast_arm_pre_dispatch_check_failed")
                        except Exception as cleanup:
                            failure.add_note(f"pre-dispatch cleanup failed: {cleanup!r}")
                raise
            if ready is not True:
                with self._lock:
                    grant.revoke()
                    life = (
                        self._fail_closed_locked("fast_arm_pre_dispatch_check_rejected")
                        if self._state in {"armed", "active"} else None
                    )
                return FastArmPhysicalOutputResult("rejected", "fast_arm_pre_dispatch_check_rejected", lifecycle_result=life)
        return self.dispatch_submission(ticket, now_s=now_s)

    def prepare_submission(self, evaluation: PhysicalOutputSafetyEvaluation, *, now_s: float | None = None
                           ) -> FastArmPreparedSubmission | FastArmPhysicalOutputResult:
        """既存permission/evidence/lifecycle/transport検査を全て通す。送信はまだ行わない。"""
        try:
            now = self._now(now_s)
        except Exception:
            with self._lock:
                result = self._fail_closed_locked("fast_arm_clock_invalid")
                return FastArmPhysicalOutputResult("failed", "fast_arm_clock_invalid", lifecycle_result=result)
        with self._lock:
            if self._state not in {"armed", "active"} or self.lifecycle.state not in {"armed", "active"}:
                return FastArmPhysicalOutputResult("rejected", "fast_arm_session_not_armed")
            if self._dispatch_inflight:
                return FastArmPhysicalOutputResult("rejected", "fast_arm_dispatch_preflight_in_progress")
            if self._pending_acknowledgement is not None:
                return FastArmPhysicalOutputResult("rejected", "fast_arm_router_acknowledgement_pending", acknowledgement=self.pending_acknowledgement)
            if self._physical_permission is None or self._transmission_permission is None:
                self._invalidate_locked()
                result = self.lifecycle.fail("fast_arm_operator_permissions_unavailable", timestamp_s=now)
                self._state = "failed"
                return FastArmPhysicalOutputResult("failed", "fast_arm_operator_permissions_unavailable", lifecycle_result=result)
            if not isinstance(evaluation, PhysicalOutputSafetyEvaluation):
                self._invalidate_locked()
                result = self.lifecycle.fail("fast_arm_safety_evaluation_required", timestamp_s=now)
                self._state = "failed"
                return FastArmPhysicalOutputResult("failed", "fast_arm_safety_evaluation_required", lifecycle_result=result)
            try:
                validate_physical_output_safety_evaluation(evaluation)
            except Exception:
                self._invalidate_locked()
                result = self.lifecycle.fail("fast_arm_safety_evaluation_invalid", timestamp_s=now)
                self._state = "failed"
                return FastArmPhysicalOutputResult("failed", "fast_arm_safety_evaluation_invalid", lifecycle_result=result)
            if evaluation.status != "allowed":
                self._invalidate_locked()
                life = self.lifecycle.submit(evaluation, now_s=now, max_age_s=self.transport_config.max_request_age_s, max_safety_age_s=self.transport_config.max_safety_age_s)
                self._state = "failed" if life.state in {"failed", "aborted", "stopped"} else "armed"
                return FastArmPhysicalOutputResult("rejected", evaluation.reason, lifecycle_result=life)
            gate_error = self._validate_physical_evidence(evaluation)
            if gate_error is not None:
                self._invalidate_locked()
                life = self.lifecycle.fail(gate_error, timestamp_s=now)
                self._state = "failed"
                return FastArmPhysicalOutputResult("failed", gate_error, lifecycle_result=life)
            life = self.lifecycle.submit(evaluation, now_s=now, max_age_s=self.transport_config.max_request_age_s, max_safety_age_s=self.transport_config.max_safety_age_s)
            if not life.accepted or life.sendable_request is None:
                self._invalidate_locked()
                self._state = "failed" if life.state in {"failed", "aborted", "stopped"} else "armed"
                return FastArmPhysicalOutputResult("rejected", life.reason or "fast_arm_lifecycle_request_rejected", lifecycle_result=life)
            sendable = life.sendable_request
            context_digest = self._authorization_context_sha256
            assert context_digest is not None and self._transmission_permission is not None and self._physical_permission is not None
            grant = _create_physical_output_transport_authorization_grant(
                target_robot_id=sendable.request.target_robot_id,
                endpoint_id=sendable.request.endpoint_id,
                software_revision=sendable.request.software_revision,
                session_id=sendable.request.session_id,
                sequence=sendable.request.sequence,
                request_sha256=sendable.request_sha256,
                safety_binding_sha256=sendable.binding_sha256,
                candidate_id=sendable.evaluation.candidate_id or "",
                codec_identity_sha256=self.transport_config.expected_codec_identity.identity_sha256,
                config_sha256=sha256(self.transport_config.to_json_bytes()).hexdigest(),
                transmission_permission_sha256=sha256(self._transmission_permission.to_json_bytes()).hexdigest(),
                actuation_permission_sha256=sha256(self._physical_permission.to_json_bytes()).hexdigest(),
                authorization_context_sha256=context_digest,
                issued_at_s=now,
                expires_at_s=now + self.authorization_ttl_s,
            )
            self._active_grant = grant
            self._dispatch_inflight = True
            self._generation += 1
            generation = self._generation
            transmission_permission = self._transmission_permission

        try:
            prepared = self.transport_adapter.prepare_dispatch(self.lifecycle, sendable, now_s=now_s, authorization_grant=grant, authorization_context_sha256=context_digest)
        except Exception:
            with self._lock:
                grant.revoke()
                self._fail_closed_locked("fast_arm_prepare_exception")
            raise
        if type(prepared) is PhysicalOutputTransportResult:
            with self._lock:
                if generation == self._generation:
                    self._invalidate_locked()
                    self._state = "failed" if self.lifecycle.state in {"failed", "aborted", "stopped"} else "armed"
            return FastArmPhysicalOutputResult("failed" if prepared.status == "rejected" else "rejected", prepared.reason or "fast_arm_transport_preflight_rejected", transport_result=prepared)
        with self._lock:
            if generation != self._generation or self._state not in {"armed", "active"} or self._active_grant is not grant:
                grant.revoke()
                return FastArmPhysicalOutputResult("rejected", "fast_arm_operator_state_changed_during_preflight")
            ticket = FastArmPreparedSubmission(self, prepared, sendable, grant, generation, transmission_permission)
            self._prepared_submission = ticket
            return ticket

    def dispatch_submission(self, ticket: FastArmPreparedSubmission, *, now_s: float | None = None
                            ) -> FastArmPhysicalOutputResult:
        """同一objectを一度だけ消費し、既存transportの最終検査へ渡す。"""
        with self._lock:
            if type(ticket) is not FastArmPreparedSubmission or ticket.owner is not self or self._prepared_submission is not ticket:
                return FastArmPhysicalOutputResult("rejected", "foreign_consumed_or_stale_prepared_submission")
            self._prepared_submission = None
        prepared, sendable, grant = ticket.prepared, ticket.sendable, ticket.grant
        generation, transmission_permission = ticket.generation, ticket.transmission_permission
        try:
            now = self._now(now_s)
        except Exception:
            with self._lock:
                grant.revoke()
                life = self._fail_closed_locked("fast_arm_clock_invalid")
            return FastArmPhysicalOutputResult("failed", "fast_arm_clock_invalid", lifecycle_result=life)
        with self._lock:
            if generation != self._generation or not self._dispatch_inflight or self._state not in {"armed", "active"} or self.lifecycle.latest_sendable_request is not sendable or self._active_grant is not grant or self._pending_acknowledgement is not None or self._transmission_permission != transmission_permission:
                grant.revoke()
                return FastArmPhysicalOutputResult("rejected", "fast_arm_operator_state_changed_during_preflight", acknowledgement=FastArmAcknowledgementEvidence("unavailable", "transport_not_attempted"))
            try:
                result = self.transport_adapter.dispatch_prepared(prepared, now_s=now_s)
            except Exception:
                grant.revoke()
                self._fail_closed_locked("fast_arm_dispatch_exception")
                raise
            self._active_grant = None
            self._dispatch_inflight = False
            self._state = "active" if self.lifecycle.state == "active" else "failed"
            if (
                result.status == "transmission_attempted"
                and result.attempt is not None
                and result.local_send_result.status
                in {"accepted_by_local_socket", "simulated_acceptance"}
            ):
                command = build_fast_arm_joint_wire_command(sendable.request, self.mapping, attempt_id=result.attempt.attempt_id)
                deadline = result.attempt.started_at_s + self.acknowledgement_timeout_s
                if not isfinite(deadline) or deadline <= result.attempt.started_at_s:
                    self._invalidate_locked()
                    life = self.lifecycle.fail("fast_arm_acknowledgement_deadline_invalid", timestamp_s=now)
                    self._state = "failed"
                    return FastArmPhysicalOutputResult("failed", "fast_arm_acknowledgement_deadline_invalid", transport_result=result, lifecycle_result=life)
                evidence_kind = (
                    "simulated"
                    if result.local_send_result.evidence_kind == "simulated"
                    else "local_socket"
                )
                pending_reason = (
                    "awaiting_simulated_router_observation"
                    if evidence_kind == "simulated"
                    else "awaiting_correlated_router_command_observation"
                )
                self._pending_acknowledgement = _PendingAcknowledgement(
                    result.attempt.attempt_id,
                    command,
                    deadline,
                    evidence_kind,
                    started_at_s=result.attempt.started_at_s,
                )
                self._last_acknowledgement = FastArmAcknowledgementEvidence(
                    "pending",
                    pending_reason,
                    attempt_id=result.attempt.attempt_id,
                    source_token=command.source_token,
                    target_robot_id=command.target_robot_id,
                )
                return FastArmPhysicalOutputResult("transmission_attempted", transport_result=result, acknowledgement=self.pending_acknowledgement)
            if result.status == "rejected":
                self._state = "failed" if self.lifecycle.state in {"failed", "aborted", "stopped"} else "armed"
            if result.reason is not None:
                return FastArmPhysicalOutputResult("failed" if self.lifecycle.state in {"failed", "aborted", "stopped"} else "rejected", result.reason, transport_result=result, acknowledgement=self._last_acknowledgement)
            return FastArmPhysicalOutputResult("transmission_attempted", transport_result=result, acknowledgement=self._last_acknowledgement)

    def observe_router_datagram(self, datagram: bytes, *, now_s: float | None = None) -> FastArmAcknowledgementEvidence:
        """共通parser/判定だけを用い、状態と許可の無効化はsessionが所有する。"""
        with self._lock:
            try:
                now = self._now(now_s)
                resolution = resolve_fast_arm_router_datagram(
                    self._pending_acknowledgement, datagram, now_s=now,
                )
            except Exception:
                self._fail_closed_locked("fast_arm_clock_invalid")
                return self._last_acknowledgement
            if resolution.disposition == "expire":
                self._expire_acknowledgement_locked(now)
                return self._last_acknowledgement
            if resolution.disposition == "clear":
                self._pending_acknowledgement = None
                self._last_acknowledgement = resolution.evidence
            return resolution.evidence

    def expire_acknowledgement(self, *, now_s: float | None = None) -> FastArmAcknowledgementEvidence:
        """受信がなくても共通deadline判定を適用する。"""
        with self._lock:
            try:
                now = self._now(now_s)
                expired = expired_fast_arm_acknowledgement(self._pending_acknowledgement, now_s=now)
            except Exception:
                self._fail_closed_locked("fast_arm_clock_invalid")
                return self._last_acknowledgement
            if expired is not None:
                self._expire_acknowledgement_locked(now)
            return self._last_acknowledgement

    def _expire_acknowledgement_locked(self, now: float) -> None:
        pending = self._pending_acknowledgement
        expired = expired_fast_arm_acknowledgement(pending, now_s=now)
        if pending is None or expired is None:
            return
        self._invalidate_locked()
        self._last_acknowledgement = expired
        failure_reason = (
            "fast_arm_simulated_observation_timeout" if pending.evidence_kind == "simulated"
            else "fast_arm_router_acknowledgement_timeout"
        )
        self.lifecycle.fail(failure_reason, timestamp_s=now)
        self._state = "failed"

    def _invalidate_locked(self) -> None:
        self._generation += 1
        self._prepared_submission = None
        self._dispatch_inflight = False
        if self._active_grant is not None:
            self._active_grant.revoke()
        self._active_grant = None
        self._pending_acknowledgement = None

    def stop(self, *, now_s: float | None = None) -> PhysicalOutputLifecycleResult:
        with self._lock:
            try:
                now = self._now(now_s)
            except Exception:
                now = self._failure_timestamp()
            self._invalidate_locked()
            result = self.transport_adapter.stop(self.lifecycle, now_s=now, reason="fast_arm_operator_stop")
            self._state = "stopped"
            self._last_acknowledgement = FastArmAcknowledgementEvidence("unavailable", "local_lifecycle_stop_does_not_prove_physical_stop")
            return result

    def disarm(self, *, now_s: float | None = None) -> PhysicalOutputLifecycleResult:
        return self.stop(now_s=now_s)

    def abort(self, *, now_s: float | None = None) -> PhysicalOutputLifecycleResult:
        with self._lock:
            try:
                now = self._now(now_s)
            except Exception:
                now = self._failure_timestamp()
            self._invalidate_locked()
            result = self.lifecycle.abort("fast_arm_operator_abort", timestamp_s=now)
            self._state = "aborted"
            return result

    def disconnect(self, *, now_s: float | None = None) -> PhysicalOutputLifecycleResult:
        with self._lock:
            try:
                now = self._now(now_s)
            except Exception:
                now = self._failure_timestamp()
            self._invalidate_locked()
            result = self.transport_adapter.disconnect(self.lifecycle, now_s=now, reason="fast_arm_transport_disconnected")
            self._state = "failed"
            self._last_acknowledgement = FastArmAcknowledgementEvidence("unavailable", "transport_disconnected")
            return result


__all__ = [
    "FAST_ARM_PHYSICAL_EVIDENCE_SCHEMA_VERSION",
    "FAST_ARM_PHYSICAL_EVIDENCE_HANDOFF_SCHEMA_VERSION",
    "FastArmAcknowledgementEvidence",
    "FastArmPhysicalEvidenceAcceptance",
    "FastArmPhysicalEvidenceHandoff",
    "FastArmPhysicalOutputResult",
    "FastArmPhysicalOutputSession",
    "FastArmWireEncoder",
    "create_fast_arm_wire_encoder",
    "fast_arm_codec_identity",
    "fast_arm_envelope_provenance_token",
]
