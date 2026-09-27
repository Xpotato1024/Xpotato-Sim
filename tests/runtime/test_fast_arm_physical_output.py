from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from math import pi
from threading import Event
from typing import Literal

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.physical_output import (
    FAST_ARM_JOINT_POSITION_SEMANTICS,
    FastArmOutputMapping,
    build_fast_arm_joint_wire_command,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.runtime.output.fast_arm_adapter import (
    FastArmPhysicalEvidenceAcceptance,
    FastArmPhysicalEvidenceHandoff,
    FastArmPhysicalOutputSession,
    create_fast_arm_wire_encoder,
    fast_arm_codec_identity,
    fast_arm_envelope_provenance_token,
)
from xpotato_sim.runtime.output.safety_gate import (
    evaluate_and_bind_physical_output_safety,
    physical_output_candidate_id,
)
from xpotato_sim.runtime.output.transport_adapter import (
    PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1,
    PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2,
    PhysicalOutputOperatorEnable,
    PhysicalOutputTransportAdapter,
    PhysicalOutputTransportConfig,
)
from xpotato_sim.runtime.safety.limit_resolution import resolve_joint_space_bounds
from xpotato_sim.runtime.safety.operator_validation import (
    EvidenceClass,
    build_dry_run_validation_artifact,
)
from xpotato_sim.runtime.safety.physical_limits import (
    EvidenceStatus,
    LimitQuantity,
    LimitSourceProvenance,
    LimitSpace,
    PhysicalLimit,
    PhysicalSafetyEnvelope,
)
from xpotato_sim.runtime.safety.physical_safety_core import SafetyInput
from xpotato_sim.schemas import PhysicalOutputPermission
from xpotato_sim.schemas.command import JointPositionCommand, PhysicalOutputRequest
from xpotato_sim.transport.endpoint import OscUdpEndpointConfig
from xpotato_sim.transport.osc import OscMessage, encode_osc_message
from xpotato_sim.transport.udp import (
    DatagramSendReceipt,
    PreparedDatagramDestination,
)
from tests.runtime.test_physical_safety_core import (
    CollisionStatus,
    FeasibilityStatus,
    _collision,
    _dynamic,
)
from tests.runtime.test_operator_validation import COMPLETED, STARTED, _all_checks, _procedure
from tests.support.output_candidate_evidence import observed_safety_input


_PROFILE = FAST_ARM_ROBOT_PROFILE
_PROFILE_JOINTS = _PROFILE.canonical_joint_names
_TARGET_ROBOT_ID = "arm_a"
_SYNTHETIC_WIRE_JOINTS = ("wire_joint_0", "wire_joint_1", "wire_joint_2", "wire_joint_3")
_SYNTHETIC_WIRE_ORDER = ("wire_joint_2", "wire_joint_0", "wire_joint_3", "wire_joint_1")
_ENDPOINT_ID = "fast-arm-test-endpoint"
_REVISION = "fast-arm-test-revision"
_SESSION_ID = "fast-arm-test-session"


class _Clock:
    def __init__(self, value: float = 1.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _SyntheticDatagramSender:
    """テスト専用のin-memory provider。socketを開かず、datagramを外部送信しない。"""

    def __init__(
        self,
        *,
        evidence_kind: Literal["simulated"] = "simulated",
        prepare_entered: Event | None = None,
        prepare_release: Event | None = None,
    ) -> None:
        self._evidence_kind = evidence_kind
        self.prepare_entered = prepare_entered
        self.prepare_release = prepare_release
        self.prepare_calls: list[OscUdpEndpointConfig] = []
        self.send_calls: list[bytes] = []

    @property
    def evidence_kind(self) -> Literal["simulated"]:
        return self._evidence_kind

    def prepare(self, endpoint: OscUdpEndpointConfig) -> PreparedDatagramDestination:
        self.prepare_calls.append(endpoint)
        if self.prepare_entered is not None:
            self.prepare_entered.set()
        if self.prepare_release is not None and not self.prepare_release.wait(timeout=2.0):
            raise TimeoutError("synthetic preflight release timeout")
        return PreparedDatagramDestination(
            endpoint=endpoint,
            address_family=2,
            socket_address=(endpoint.host, endpoint.port),
        )

    def send(
        self,
        destination: PreparedDatagramDestination,
        datagram: bytes,
    ) -> DatagramSendReceipt:
        assert destination.endpoint == self.prepare_calls[-1]
        self.send_calls.append(datagram)
        return DatagramSendReceipt(len(datagram), self._evidence_kind)


def _mapping() -> FastArmOutputMapping:
    return FastArmOutputMapping(
        profile_id=_PROFILE.profile_id,
        profile_contract_version=_PROFILE.profile_contract_version,
        model_contract_version=_PROFILE.model_contract_version,
        profile_joint_order=_PROFILE_JOINTS,
        wire_joint_order=_SYNTHETIC_WIRE_ORDER,
        joint_map=tuple(zip(_PROFILE_JOINTS, _SYNTHETIC_WIRE_JOINTS, strict=True)),
        joint_coordinate_signs=tuple(zip(_PROFILE_JOINTS, (1, -1, 1, -1), strict=True)),
        joint_angle_offsets=tuple(zip(_PROFILE_JOINTS, (0.01, -0.02, 0.03, -0.04), strict=True)),
        source_id="synthetic-fast-arm-source",
        angle_input_unit="rad",
        angle_output_unit="degree",
        angle_offset_unit="rad",
        command_semantics=FAST_ARM_JOINT_POSITION_SEMANTICS,
    )


def _request(
    *,
    sequence: int = 1,
    timestamp_s: float = 1.0,
    joint_angles_rad: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4),
    target_robot_id: str = _TARGET_ROBOT_ID,
) -> PhysicalOutputRequest:
    return PhysicalOutputRequest(
        target_robot_id=target_robot_id,
        endpoint_id=_ENDPOINT_ID,
        command_semantics=FAST_ARM_JOINT_POSITION_SEMANTICS,
        command=JointPositionCommand(
            timestamp_s=timestamp_s,
            joint_angles_rad=joint_angles_rad,
        ),
        session_id=_SESSION_ID,
        sequence=sequence,
        timestamp_s=timestamp_s,
        cadence_s=0.1,
        software_revision=_REVISION,
    )


def _accepted_evidence(target_robot_id: str = _TARGET_ROBOT_ID) -> FastArmPhysicalEvidenceAcceptance:
    limits: list[PhysicalLimit] = []
    references: list[tuple[str, str]] = []
    for index, joint_name in enumerate(_PROFILE_JOINTS):
        reference = f"synthetic-test-only:joint-measurement-{index}"
        source = LimitSourceProvenance(
            source_kind="physical_measurement",
            source_id=f"synthetic-test-measurement-source-{index}",
            revision="synthetic-test-revision",
            status=EvidenceStatus.AUTHORITATIVE,
            evidence_reference=reference,
        )
        limits.append(
            PhysicalLimit(
                name=joint_name,
                quantity=LimitQuantity.POSITION,
                lower=-1.0,
                upper=1.0,
                unit="rad",
                space=LimitSpace.JOINT,
                frame="fast_arm joint space",
                status=EvidenceStatus.AUTHORITATIVE,
                source=source,
            )
        )
        references.append((joint_name, reference))
    envelope = PhysicalSafetyEnvelope(
        envelope_id="synthetic-test-fast-arm-envelope",
        envelope_version=1,
        robot_id=target_robot_id,
        model_id=_PROFILE.model_contract_version,
        limits=tuple(limits),
        source_summary="synthetic test fixture only; not hardware evidence",
    )
    envelope_sha256 = sha256(envelope.to_json_bytes()).hexdigest()
    acceptance_reference = "synthetic-test-only:#509-acceptance-record"
    joint_measurement_references = tuple(references)
    handoff_bytes = FastArmPhysicalEvidenceHandoff(
        acceptance_reference=acceptance_reference,
        target_robot_id=target_robot_id,
        profile_id=_PROFILE.profile_id,
        profile_contract_version=_PROFILE.profile_contract_version,
        model_contract_version=_PROFILE.model_contract_version,
        envelope_sha256=envelope_sha256,
        joint_measurement_references=joint_measurement_references,
        accepted_at_s=1.0,
    ).to_json_bytes()
    return FastArmPhysicalEvidenceAcceptance(
        acceptance_reference=acceptance_reference,
        acceptance_sha256=sha256(handoff_bytes).hexdigest(),
        handoff_bytes=handoff_bytes,
        target_robot_id=target_robot_id,
        profile_id=_PROFILE.profile_id,
        profile_contract_version=_PROFILE.profile_contract_version,
        model_contract_version=_PROFILE.model_contract_version,
        observation_class="physical_measurement",
        envelope=envelope,
        envelope_sha256=envelope_sha256,
        joint_measurement_references=joint_measurement_references,
        accepted_at_s=1.0,
    )


def test_physical_evidence_handoff_uses_strict_canonical_json_bytes() -> None:
    evidence = _accepted_evidence()
    handoff = FastArmPhysicalEvidenceHandoff.from_json_bytes(evidence.handoff_bytes)

    assert handoff.schema_version == "fast-arm-physical-evidence-handoff/v1"
    assert handoff.issue_id == "#509"
    assert handoff.status == "accepted"
    assert handoff.observation_class == "physical_measurement"
    assert sha256(evidence.handoff_bytes).hexdigest() == evidence.acceptance_sha256
    assert handoff.to_json_bytes() == evidence.handoff_bytes

    duplicate_key = evidence.handoff_bytes.replace(
        b'"acceptance_reference":"synthetic-test-only:#509-acceptance-record"',
        b'"acceptance_reference":"duplicate","acceptance_reference":"synthetic-test-only:#509-acceptance-record"',
        1,
    )
    with pytest.raises(ValueError, match="duplicate field"):
        FastArmPhysicalEvidenceHandoff.from_json_bytes(duplicate_key)
    with pytest.raises(ValueError, match="BOM"):
        FastArmPhysicalEvidenceHandoff.from_json_bytes(b"\xef\xbb\xbf" + evidence.handoff_bytes)
    with pytest.raises(ValueError, match="canonical JSON"):
        FastArmPhysicalEvidenceHandoff.from_json_bytes(
            evidence.handoff_bytes.replace(b'"accepted_at_s":1.0', b'"accepted_at_s":1.00')
        )
    with pytest.raises(ValueError, match="non-finite JSON constant"):
        FastArmPhysicalEvidenceHandoff.from_json_bytes(
            evidence.handoff_bytes.replace(b'"accepted_at_s":1.0', b'"accepted_at_s":NaN')
        )
    with pytest.raises(ValueError, match="missing or unknown fields"):
        FastArmPhysicalEvidenceHandoff.from_json_bytes(
            evidence.handoff_bytes.replace(b'"target_robot_id":"arm_a"', b'"target_robot_id":"arm_a","unexpected":true')
        )
    with pytest.raises(ValueError, match="missing or unknown fields"):
        FastArmPhysicalEvidenceHandoff.from_json_bytes(
            evidence.handoff_bytes.replace(b',"target_robot_id":"arm_a"', b'')
        )
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        FastArmPhysicalEvidenceHandoff.from_json_bytes(b"\xff")

    handoff = FastArmPhysicalEvidenceHandoff.from_json_bytes(evidence.handoff_bytes)
    with pytest.raises(ValueError, match="accepted physical-measurement"):
        replace(handoff, status="rejected")
    with pytest.raises(ValueError, match="accepted physical-measurement"):
        replace(handoff, observation_class="software_only")
    with pytest.raises(ValueError, match="accepted physical-measurement"):
        replace(handoff, schema_version="other/v1")


def test_physical_evidence_acceptance_requires_matching_handoff_bytes() -> None:
    evidence = _accepted_evidence()
    with pytest.raises(ValueError, match="handoff digest"):
        replace(evidence, acceptance_sha256="0" * 64)
    with pytest.raises(TypeError, match="exact handoff bytes"):
        replace(evidence, handoff_bytes=None)

    handoff = FastArmPhysicalEvidenceHandoff.from_json_bytes(evidence.handoff_bytes)
    changed_bytes = replace(handoff, acceptance_reference="synthetic-test-only:other-reference").to_json_bytes()
    with pytest.raises(ValueError, match="do not match typed physical evidence"):
        replace(evidence, handoff_bytes=changed_bytes, acceptance_sha256=sha256(changed_bytes).hexdigest())


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("acceptance_reference", "synthetic-test-only:other-reference"),
        ("target_robot_id", "another-arm"),
        ("profile_id", "another-profile"),
        ("profile_contract_version", 99),
        ("model_contract_version", "another-model"),
        ("envelope_sha256", "0" * 64),
        ("joint_measurement_references", (("joint-1", "other-reference"),)),
        ("accepted_at_s", 2.0),
    ),
)
def test_physical_evidence_acceptance_rejects_typed_handoff_mismatch(field_name: str, replacement: object) -> None:
    evidence = _accepted_evidence()
    with pytest.raises((TypeError, ValueError)):
        replace(evidence, **{field_name: replacement})


def test_physical_evidence_handoff_rejects_software_only_p6_artifact() -> None:
    p6_artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="synthetic-software-only-p6",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert p6_artifact.evidence_class is EvidenceClass.SOFTWARE_ONLY
    with pytest.raises(ValueError, match="missing or unknown fields"):
        FastArmPhysicalEvidenceHandoff.from_json_bytes(p6_artifact.to_json_bytes())


def test_session_rejects_accepted_envelope_model_mismatch() -> None:
    evidence = _accepted_evidence()
    envelope = replace(evidence.envelope, model_id="another-model")
    envelope_sha256 = sha256(envelope.to_json_bytes()).hexdigest()
    handoff = replace(
        FastArmPhysicalEvidenceHandoff.from_json_bytes(evidence.handoff_bytes),
        model_contract_version="another-model",
        envelope_sha256=envelope_sha256,
    )
    handoff_bytes = handoff.to_json_bytes()
    mismatched_evidence = replace(
        evidence,
        model_contract_version="another-model",
        envelope=envelope,
        envelope_sha256=envelope_sha256,
        handoff_bytes=handoff_bytes,
        acceptance_sha256=sha256(handoff_bytes).hexdigest(),
    )

    with pytest.raises(ValueError, match="accepted #509 evidence does not match"):
        _new_session(evidence=mismatched_evidence)


def _evaluation(
    request: PhysicalOutputRequest,
    evidence: FastArmPhysicalEvidenceAcceptance,
    *,
    include_envelope_provenance: bool = True,
):
    provenance = (
        (
            fast_arm_envelope_provenance_token(
                evidence.envelope,
                evidence.envelope_sha256,
            ),
        )
        if include_envelope_provenance
        else ()
    )
    safety_input = observed_safety_input(
        request,
        joint_names=_PROFILE_JOINTS,
        model_id=_PROFILE.model_contract_version,
        limits=resolve_joint_space_bounds(
            evidence.envelope.limits,
            expected_joint_names=_PROFILE_JOINTS,
            robot_id=request.target_robot_id,
        ),
    )
    safety_input = replace(safety_input, provenance=(*provenance, *safety_input.provenance))
    evaluation = evaluate_and_bind_physical_output_safety(
        request,
        safety_input,
        checked_at_s=request.timestamp_s,
    )
    assert evaluation.status == "allowed"
    return evaluation


def _transport_config(
    mapping: FastArmOutputMapping,
    *,
    target_robot_id: str = _TARGET_ROBOT_ID,
    schema_version: str = PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2,
    external_authorization_required: bool = True,
) -> PhysicalOutputTransportConfig:
    return PhysicalOutputTransportConfig(
        target_robot_id=target_robot_id,
        software_revision=_REVISION,
        endpoint=OscUdpEndpointConfig(
            endpoint_id=_ENDPOINT_ID,
            host="127.0.0.1",
            port=9000,
            max_datagram_bytes=2_048,
            timeout_s=0.1,
        ),
        expected_codec_identity=fast_arm_codec_identity(mapping),
        mode="transmission_enabled",
        operator_enable=PhysicalOutputOperatorEnable("test-operator", "test-enable"),
        max_request_age_s=5.0,
        max_safety_age_s=5.0,
        minimum_cadence_s=0.1,
        external_authorization_required=external_authorization_required,
        schema_version=schema_version,
    )


def _new_session(
    *,
    sender: _SyntheticDatagramSender | None = None,
    config: PhysicalOutputTransportConfig | None = None,
    evidence: FastArmPhysicalEvidenceAcceptance | None = None,
    runtime_plugin_id: str | None = None,
    runtime_robot_id: str | None = None,
    target_robot_id: str = _TARGET_ROBOT_ID,
):
    clock = _Clock()
    selected_mapping = _mapping()
    selected_evidence = _accepted_evidence(target_robot_id) if evidence is None else evidence
    selected_config = (
        _transport_config(selected_mapping, target_robot_id=target_robot_id)
        if config is None
        else config
    )
    selected_sender = _SyntheticDatagramSender() if sender is None else sender
    adapter = PhysicalOutputTransportAdapter(
        selected_config,
        sender=selected_sender,
        encoder=create_fast_arm_wire_encoder(selected_mapping),
        clock=clock,
    )
    session = FastArmPhysicalOutputSession(
        profile=_PROFILE,
        runtime_plugin_id=_PROFILE.profile_id if runtime_plugin_id is None else runtime_plugin_id,
        runtime_robot_id=target_robot_id if runtime_robot_id is None else runtime_robot_id,
        target_robot_id=target_robot_id,
        endpoint_id=_ENDPOINT_ID,
        session_id=_SESSION_ID,
        mapping=selected_mapping,
        accepted_evidence=selected_evidence,
        transport_config=selected_config,
        transport_adapter=adapter,
        cadence_s=0.1,
        authorization_ttl_s=0.5,
        acknowledgement_timeout_s=1.0,
        clock=clock,
    )
    physical_permission = PhysicalOutputPermission(
        mode="physical_actuation",
        operator_id="test-operator",
        enable_token_id="physical-enable",
    )
    transmission_permission = PhysicalOutputPermission(
        mode="transmission_enabled",
        operator_id="test-operator",
        enable_token_id="test-enable",
    )
    return (
        session,
        selected_mapping,
        selected_evidence,
        selected_config,
        selected_sender,
        clock,
        physical_permission,
        transmission_permission,
    )


def _arm_session(session: FastArmPhysicalOutputSession, permissions) -> None:
    physical_permission, transmission_permission = permissions
    assert session.arm(
        physical_permission,
        transmission_permission,
        now_s=1.0,
    ).accepted


def _router_observation_datagram(command, *, token: str | None = None) -> bytes:
    args = "[" + ", ".join(repr(value) for value in command.osc_arguments) + "]"
    return encode_osc_message(
        OscMessage(
            f"/router/{command.target_robot_id}/command",
            (
                command.source_token if token is None else token,
                command.command,
                args,
            ),
        )
    )


def test_mapping_binds_profile_names_to_explicit_wire_order_and_rad_to_degree() -> None:
    mapping = _mapping()
    request = _request()
    command = build_fast_arm_joint_wire_command(
        request,
        mapping,
        attempt_id="synthetic-attempt-1",
    )
    profile_values = dict(zip(_PROFILE_JOINTS, request.command.joint_angles_rad, strict=True))
    profile_for_wire = {wire: profile for profile, wire in mapping.joint_map}
    sign_by_joint = dict(mapping.joint_coordinate_signs)
    offset_by_joint = dict(mapping.joint_angle_offsets)
    expected_degrees = tuple(
        (
            profile_values[profile_for_wire[wire]] * sign_by_joint[profile_for_wire[wire]]
            + offset_by_joint[profile_for_wire[wire]]
        )
        * 180.0
        / pi
        for wire in _SYNTHETIC_WIRE_ORDER
    )

    assert command.joint_order == _SYNTHETIC_WIRE_ORDER
    assert command.position_degrees == pytest.approx(expected_degrees, rel=1e-6)
    assert command.address.startswith("/synthetic-fast-arm-source-")
    assert FastArmOutputMapping.from_json(mapping.to_json_bytes()) == mapping
    changed_signs = replace(
        mapping,
        joint_coordinate_signs=tuple((joint, -sign) for joint, sign in mapping.joint_coordinate_signs),
    )
    changed_offsets = replace(
        mapping,
        joint_angle_offsets=tuple((joint, offset + 0.5) for joint, offset in mapping.joint_angle_offsets),
    )
    assert changed_signs.identity_sha256 != mapping.identity_sha256
    assert changed_offsets.identity_sha256 != mapping.identity_sha256
    for field in ("angle_offset_unit", "joint_coordinate_signs", "joint_angle_offsets"):
        missing_transform = mapping.to_json_value()
        del missing_transform[field]
        with pytest.raises(ValueError, match="fields are incomplete or unknown"):
            FastArmOutputMapping.from_mapping(missing_transform)
    with pytest.raises(ValueError, match="exactly -1 or 1"):
        replace(
            mapping,
            joint_coordinate_signs=tuple((joint, 0) for joint, _ in mapping.joint_coordinate_signs),
        )
    with pytest.raises(ValueError, match="fields are incomplete or unknown"):
        FastArmOutputMapping.from_mapping({})


def test_mapping_applies_explicit_degree_offsets_as_a_pure_transform() -> None:
    base_mapping = _mapping()
    offsets = tuple(zip(_PROFILE_JOINTS, (1.0, -2.0, 3.0, -4.0), strict=True))
    mapping = replace(base_mapping, angle_offset_unit="degree", joint_angle_offsets=offsets)
    request = _request()

    command = build_fast_arm_joint_wire_command(
        request,
        mapping,
        attempt_id="synthetic-attempt-degree-offset",
    )
    profile_values = dict(zip(_PROFILE_JOINTS, request.command.joint_angles_rad, strict=True))
    sign_by_joint = dict(mapping.joint_coordinate_signs)
    offset_by_joint = dict(mapping.joint_angle_offsets)
    profile_for_wire = {wire: profile for profile, wire in mapping.joint_map}
    expected_degrees = tuple(
        profile_values[profile_for_wire[wire]] * sign_by_joint[profile_for_wire[wire]] * 180.0 / pi
        + offset_by_joint[profile_for_wire[wire]]
        for wire in mapping.wire_joint_order
    )

    assert command.position_degrees == pytest.approx(expected_degrees, rel=1e-6)
    assert FastArmOutputMapping.from_json(mapping.to_json_bytes()) == mapping


def test_profile_plugin_identity_is_separate_from_output_target_identity() -> None:
    session, _mapping_value, evidence, _config, sender, _clock, physical, transmission = _new_session()
    assert session.profile.profile_id == "fast_arm"
    assert session.runtime_plugin_id == "fast_arm"
    assert session.profile.profile_id != session.target_robot_id
    assert session.runtime_robot_id == session.target_robot_id == _TARGET_ROBOT_ID
    assert evidence.profile_id == session.profile.profile_id
    assert evidence.target_robot_id == session.target_robot_id

    _arm_session(session, (physical, transmission))
    wrong_target = session.submit(
        _evaluation(_request(target_robot_id="arm_b"), evidence),
        now_s=1.0,
    )
    assert wrong_target.status == "failed"
    assert wrong_target.reason == "fast_arm_request_identity_or_semantics_mismatch"
    assert session.latest_sendable_request is None
    assert sender.prepare_calls == []

    with pytest.raises(ValueError, match="runtime, profile, target, and endpoint identities differ"):
        _new_session(runtime_robot_id="arm_b")
    with pytest.raises(ValueError, match="runtime, profile, target, and endpoint identities differ"):
        _new_session(runtime_plugin_id="arm_a")
    with pytest.raises(ValueError, match="accepted #509 evidence does not match"):
        _new_session(evidence=_accepted_evidence("arm_b"))


@pytest.mark.parametrize(
    ("schema_version", "external_authorization_required"),
    [
        (PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1, False),
        (PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2, False),
    ],
)
def test_session_rejects_v1_or_unprotected_transport_config(
    schema_version: str,
    external_authorization_required: bool,
) -> None:
    mapping = _mapping()
    config = _transport_config(
        mapping,
        schema_version=schema_version,
        external_authorization_required=external_authorization_required,
    )
    sender = _SyntheticDatagramSender()

    with pytest.raises(ValueError, match="v2 external authorization"):
        _new_session(config=config, sender=sender)

    assert sender.prepare_calls == []
    assert sender.send_calls == []


def test_public_fast_arm_encoder_requires_generic_v2_external_authorization() -> None:
    mapping = _mapping()
    encoder = create_fast_arm_wire_encoder(mapping)

    v1_config = _transport_config(
        mapping,
        schema_version=PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1,
        external_authorization_required=False,
    )
    v2_grantless_config = _transport_config(
        mapping,
        schema_version=PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2,
        external_authorization_required=False,
    )
    protected_config = _transport_config(mapping)

    with pytest.raises(ValueError, match="requires transport config v2 external authorization"):
        PhysicalOutputTransportAdapter(v1_config, encoder=encoder)
    with pytest.raises(ValueError, match="requires transport config v2 external authorization"):
        PhysicalOutputTransportAdapter(v2_grantless_config, encoder=encoder)

    adapter = PhysicalOutputTransportAdapter(protected_config, encoder=encoder)
    assert adapter.config is protected_config


def test_synthetic_envelope_provenance_is_required_before_any_preflight() -> None:
    session, _mapping_value, evidence, _config, sender, _clock, physical, transmission = _new_session()
    _arm_session(session, (physical, transmission))
    evaluation = _evaluation(
        _request(),
        evidence,
        include_envelope_provenance=False,
    )

    result = session.submit(evaluation, now_s=1.0)

    assert result.status == "failed"
    assert result.reason == "fast_arm_envelope_provenance_mismatch"
    assert session.lifecycle.latest_request is None
    assert session.latest_sendable_request is None
    assert sender.prepare_calls == []
    assert sender.send_calls == []


def test_simulated_router_observation_correlation_never_becomes_receiver_evidence() -> None:
    sender = _SyntheticDatagramSender()
    session, mapping, evidence, _config, _sender, _clock, physical, transmission = _new_session(
        sender=sender,
    )
    _arm_session(session, (physical, transmission))
    request = _request()

    result = session.submit(_evaluation(request, evidence), now_s=1.0)

    assert result.status == "transmission_attempted"
    assert result.transport_result is not None
    assert result.transport_result.attempt is not None
    assert result.acknowledgement.status == "pending"
    assert result.acknowledgement.reason == "awaiting_simulated_router_observation"
    assert result.transport_result.local_send_result.evidence_kind == "simulated"
    assert result.transport_result.local_send_result.status == "simulated_acceptance"
    assert len(sender.send_calls) == 1
    command = build_fast_arm_joint_wire_command(
        request,
        mapping,
        attempt_id=result.transport_result.attempt.attempt_id,
    )
    mismatch = session.observe_router_datagram(
        _router_observation_datagram(command, token=f"{command.source_token}-wrong"),
        now_s=1.1,
    )
    assert mismatch.status == "unavailable"
    assert mismatch.reason == "router_observation_correlation_mismatch"
    assert session.pending_acknowledgement.status == "pending"
    blocked = session.submit(
        _evaluation(_request(sequence=2, timestamp_s=1.15), evidence),
        now_s=1.15,
    )
    assert blocked.status == "rejected"
    assert blocked.reason == "fast_arm_router_acknowledgement_pending"
    assert len(sender.send_calls) == 1

    observed = session.observe_router_datagram(
        _router_observation_datagram(command),
        now_s=1.2,
    )

    assert observed.status == "unavailable"
    assert observed.reason == "simulated_router_observation_correlated"
    assert observed.attempt_id == result.transport_result.attempt.attempt_id
    assert session.pending_acknowledgement.status == "unavailable"
    assert session.latest_sendable_request is None


def test_acknowledgement_timeout_fails_closed_and_prevents_a_later_send() -> None:
    sender = _SyntheticDatagramSender()
    session, _mapping_value, evidence, _config, _sender, _clock, physical, transmission = _new_session(
        sender=sender,
    )
    _arm_session(session, (physical, transmission))
    request = _request()
    sent = session.submit(_evaluation(request, evidence), now_s=1.0)
    assert sent.acknowledgement.status == "pending"
    assert sent.transport_result is not None
    assert sent.transport_result.local_send_result.evidence_kind == "simulated"
    assert sent.transport_result.local_send_result.status == "simulated_acceptance"

    timed_out = session.expire_acknowledgement(now_s=2.1)
    later = session.submit(
        _evaluation(
            _request(sequence=2, timestamp_s=2.2),
            evidence,
        ),
        now_s=2.2,
    )

    assert timed_out.status == "unavailable"
    assert timed_out.reason == "simulated_router_observation_timeout"
    assert session.state == "failed"
    assert session.lifecycle.latest_request is None
    assert session.latest_sendable_request is None
    assert later.status == "rejected"
    assert len(sender.send_calls) == 1


def test_invalid_observation_clock_fails_closed_and_clears_pending_output() -> None:
    session, _mapping_value, evidence, _config, sender, _clock, physical, transmission = _new_session()
    _arm_session(session, (physical, transmission))
    sent = session.submit(_evaluation(_request(), evidence), now_s=1.0)
    assert sent.acknowledgement.status == "pending"

    invalid_clock = session.expire_acknowledgement(now_s=float("nan"))
    later = session.submit(
        _evaluation(_request(sequence=2, timestamp_s=1.2), evidence),
        now_s=1.2,
    )

    assert invalid_clock.status == "unavailable"
    assert invalid_clock.reason == "fast_arm_clock_invalid"
    assert session.state == "failed"
    assert session.lifecycle.latest_request is None
    assert session.latest_sendable_request is None
    assert later.status == "rejected"
    assert len(sender.send_calls) == 1


def test_malformed_router_observation_keeps_pending_and_blocks_another_request() -> None:
    session, _mapping_value, evidence, _config, sender, _clock, physical, transmission = _new_session()
    _arm_session(session, (physical, transmission))
    sent = session.submit(_evaluation(_request(), evidence), now_s=1.0)
    assert sent.acknowledgement.status == "pending"

    malformed = session.observe_router_datagram(b"not-an-osc-packet", now_s=1.1)
    blocked = session.submit(
        _evaluation(_request(sequence=2, timestamp_s=1.2), evidence),
        now_s=1.2,
    )

    assert malformed.status == "unavailable"
    assert malformed.reason == "router_observation_malformed"
    assert session.pending_acknowledgement.status == "pending"
    assert session.lifecycle.latest_request is None
    assert session.latest_sendable_request is None
    assert blocked.status == "rejected"
    assert blocked.reason == "fast_arm_router_acknowledgement_pending"
    assert len(sender.send_calls) == 1


def test_invalid_clock_value_fails_closed_before_transport_preflight() -> None:
    session, _mapping_value, evidence, _config, sender, _clock, physical, transmission = _new_session()
    _arm_session(session, (physical, transmission))

    result = session.submit(_evaluation(_request(), evidence), now_s=float("nan"))

    assert result.status == "failed"
    assert result.reason == "fast_arm_clock_invalid"
    assert session.state == "failed"
    assert session.lifecycle.latest_request is None
    assert session.latest_sendable_request is None
    assert sender.prepare_calls == []
    assert sender.send_calls == []


def test_operator_stop_during_blocking_preflight_prevents_send_after_release() -> None:
    prepare_entered = Event()
    prepare_release = Event()
    sender = _SyntheticDatagramSender(
        evidence_kind="simulated",
        prepare_entered=prepare_entered,
        prepare_release=prepare_release,
    )
    session, _mapping_value, evidence, _config, _sender, _clock, physical, transmission = _new_session(
        sender=sender,
    )
    _arm_session(session, (physical, transmission))
    evaluation = _evaluation(_request(), evidence)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        submit_future = pool.submit(session.submit, evaluation)
        assert prepare_entered.wait(timeout=1.0)
        stopped = session.stop(now_s=1.1)
        prepare_release.set()
        result = submit_future.result(timeout=2.0)

    assert stopped.state == "stopped"
    assert result.status == "rejected"
    assert result.reason == "fast_arm_operator_state_changed_during_preflight"
    assert session.state == "stopped"
    assert session.lifecycle.latest_request is None
    assert session.latest_sendable_request is None
    assert len(sender.prepare_calls) == 1
    assert sender.send_calls == []
