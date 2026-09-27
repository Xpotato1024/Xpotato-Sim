from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import socket
from threading import Barrier, Event, Lock
from typing import Literal

import pytest

from xpotato_sim.runtime.output import (
    PhysicalOutputLifecycle,
    PhysicalOutputLifecycleTrace,
    bind_physical_output_safety,
    evaluate_and_bind_physical_output_safety,
    physical_output_candidate_id,
)
from xpotato_sim.runtime.output.safety_gate import PhysicalOutputSendableRequest
from xpotato_sim.runtime.output.transport_adapter import (
    PHYSICAL_OUTPUT_OSC_ADDRESS,
    PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION,
    PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2,
    PHYSICAL_OUTPUT_WIRE_SCHEMA_VERSION,
    GenericPhysicalOutputWireEncoder,
    PhysicalOutputCodecIdentity,
    PhysicalOutputEncodedDatagram,
    PhysicalOutputOperatorEnable,
    PhysicalOutputRecordingResult,
    PhysicalOutputTransportAdapter,
    PhysicalOutputTransportAuthorizationGrant,
    PhysicalOutputTransportConfig,
    PhysicalOutputTransportPreparedDispatch,
    PhysicalOutputWireMessage,
    _create_physical_output_transport_authorization_grant,
    build_physical_output_wire_message,
    decode_physical_output_wire_message,
)
from xpotato_sim.schemas import PhysicalOutputPermission, PhysicalOutputRequest
from xpotato_sim.transport.endpoint import OscUdpEndpointConfig
from xpotato_sim.transport.osc import OscMessage, decode_osc_message, encode_osc_message
from xpotato_sim.transport.udp import (
    DatagramSendReceipt,
    PreparedDatagramDestination,
    UdpDatagramSender,
)

from tests.runtime.test_physical_safety_core import _input as _safety_input_fixture
from tests.schemas.test_physical_output_contract import _endpoint_request
from tests.support.output_candidate_evidence import joint_request, observed_safety_input


class _Clock:
    def __init__(self, value: float = 1.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _SecondReadBlockingClock:
    def __init__(self, value: float = 1.0) -> None:
        self.value = value
        self.read_count = 0
        self.second_read_entered = Event()
        self.second_read_release = Event()
        self._lock = Lock()

    def __call__(self) -> float:
        with self._lock:
            self.read_count += 1
            read_count = self.read_count
            value = self.value
        if read_count == 2:
            self.second_read_entered.set()
            if not self.second_read_release.wait(timeout=2.0):
                raise TimeoutError("blocking clock release timeout")
        return value


class _FakeSender:
    def __init__(
        self,
        *,
        prepare_barrier: Barrier | None = None,
        send_entered: Event | None = None,
        send_release: Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self.prepare_barrier = prepare_barrier
        self.send_entered = send_entered
        self.send_release = send_release
        self.error = error
        self.prepare_calls: list[OscUdpEndpointConfig] = []
        self.send_calls: list[bytes] = []

    @property
    def evidence_kind(self) -> Literal["simulated"]:
        return "simulated"

    def prepare(self, endpoint: OscUdpEndpointConfig) -> PreparedDatagramDestination:
        self.prepare_calls.append(endpoint)
        if self.prepare_barrier is not None:
            self.prepare_barrier.wait(timeout=2.0)
        return PreparedDatagramDestination(
            endpoint=endpoint,
            address_family=socket.AF_INET,
            socket_address=(endpoint.host, endpoint.port),
        )

    def send(
        self, destination: PreparedDatagramDestination, datagram: bytes
    ) -> DatagramSendReceipt:
        assert destination.endpoint == self.prepare_calls[-1]
        self.send_calls.append(datagram)
        if self.send_entered is not None:
            self.send_entered.set()
        if self.send_release is not None and not self.send_release.wait(timeout=2.0):
            raise TimeoutError("fake sender release timeout")
        if self.error is not None:
            raise self.error
        return DatagramSendReceipt(len(datagram), "simulated")


class _RecordingSink:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.records: list[PhysicalOutputEncodedDatagram] = []

    def record_datagram(self, datagram: PhysicalOutputEncodedDatagram) -> None:
        if self.error is not None:
            raise self.error
        self.records.append(datagram)


class _FixtureWireEncoder:
    def __init__(self, identity: PhysicalOutputCodecIdentity) -> None:
        self.identity = identity
        self.seen: list[PhysicalOutputWireMessage] = []

    def encode(self, envelope: PhysicalOutputWireMessage) -> OscMessage:
        self.seen.append(envelope)
        return OscMessage(
            "/fixture/physical-output/v1",
            (envelope.attempt_id, envelope.candidate_id, envelope.request_json_bytes),
        )


def _request(**changes: object) -> PhysicalOutputRequest:
    return joint_request(**changes)


def _evaluation(request: PhysicalOutputRequest):
    safety_input = observed_safety_input(request)
    return evaluate_and_bind_physical_output_safety(
        request,
        safety_input,
        checked_at_s=request.timestamp_s,
    )


def _permission(*, operator_id: str = "operator-1", enable_token_id: str = "enable-1"):
    return PhysicalOutputPermission(
        mode="transmission_enabled",
        operator_id=operator_id,
        enable_token_id=enable_token_id,
    )


def _config(
    mode: str,
    *,
    operator_enable: PhysicalOutputOperatorEnable | None = None,
    host: str = "192.0.2.1",
) -> PhysicalOutputTransportConfig:
    return PhysicalOutputTransportConfig(
        target_robot_id="fixture-robot",
        software_revision="test-revision:physical-output",
        endpoint=OscUdpEndpointConfig(
            endpoint_id="tool_endpoint",
            host=host,
            port=9000,
            max_datagram_bytes=2_048,
            timeout_s=0.1,
        ),
        mode=mode,  # type: ignore[arg-type]
        operator_enable=operator_enable,
        max_request_age_s=5.0,
        max_safety_age_s=5.0,
        minimum_cadence_s=0.0,
    )


def _active_lifecycle(
    permission: PhysicalOutputPermission,
    *,
    clock: _Clock | None = None,
) -> tuple[PhysicalOutputLifecycle, PhysicalOutputSendableRequest]:
    request = _request()
    lifecycle_clock = _Clock() if clock is None else clock
    lifecycle = PhysicalOutputLifecycle(request.session_id, clock=lifecycle_clock)
    assert lifecycle.arm(permission, timestamp_s=1.0).accepted
    accepted = lifecycle.submit(
        _evaluation(request),
        now_s=1.0,
        max_age_s=5.0,
        max_safety_age_s=5.0,
    )
    assert accepted.accepted
    assert accepted.sendable_request is not None
    return lifecycle, accepted.sendable_request


def test_transport_config_json_toml_and_endpoint_are_strict_and_versioned() -> None:
    config = _config("disabled")
    assert PhysicalOutputTransportConfig.from_json(config.to_json_bytes()) == config
    assert config.schema_version == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION
    assert config.expected_codec_identity == GenericPhysicalOutputWireEncoder.identity

    toml_config = f"""
schema_version = "physical-output-transport-config/v1"
target_robot_id = "fixture-robot"
software_revision = "test-revision:physical-output"
mode = "disabled"
max_request_age_s = 5.0
max_safety_age_s = 5.0
minimum_cadence_s = 0.0

[expected_codec_identity]
codec_id = "{config.expected_codec_identity.codec_id}"
codec_version = "{config.expected_codec_identity.codec_version}"
configuration_sha256 = "{config.expected_codec_identity.configuration_sha256}"

[endpoint]
schema_version = "osc-udp-endpoint/v1"
endpoint_id = "tool_endpoint"
host = "192.0.2.1"
port = 9000
max_datagram_bytes = 2048
timeout_s = 0.1
"""
    assert PhysicalOutputTransportConfig.from_toml(toml_config) == config
    identity = config.expected_codec_identity
    changed_settings_identity = PhysicalOutputCodecIdentity.from_settings(
        identity.codec_id,
        identity.codec_version,
        {"osc_address": PHYSICAL_OUTPUT_OSC_ADDRESS, "wire_schema_version": "v2"},
    )
    assert changed_settings_identity.identity_sha256 != identity.identity_sha256
    with pytest.raises(ValueError, match="expected_codec_identity"):
        PhysicalOutputCodecIdentity.from_mapping(
            {**identity.to_json_value(), "unexpected": "field"}
        )
    assert PhysicalOutputTransportConfig(
        target_robot_id="fixture-robot",
        software_revision="test-revision:physical-output",
        endpoint=replace(config.endpoint, host="2001:db8::1"),
    ).endpoint.host == "2001:db8::1"

    with pytest.raises(ValueError, match="duplicate JSON key"):
        PhysicalOutputTransportConfig.from_json(
            b'{"schema_version":"physical-output-transport-config/v1",'
            b'"schema_version":"physical-output-transport-config/v1"}'
        )
    with pytest.raises(ValueError, match="unknown"):
        PhysicalOutputTransportConfig.from_json(
            {**config.to_json_value(), "unexpected": True}
        )
    with pytest.raises(ValueError, match="numeric"):
        PhysicalOutputTransportConfig.from_json(
            {**config.to_json_value(), "max_request_age_s": True}
        )
    with pytest.raises(ValueError, match="finite"):
        PhysicalOutputTransportConfig.from_json(
            {**config.to_json_value(), "max_safety_age_s": float("nan")}
        )


def test_generic_osc_codec_round_trips_typed_primitives_and_rejects_invalid_values() -> None:
    message = OscMessage(
        "/fixture/osc/v1",
        ("日本語", b"\x01\x02\x03", -42, 1.25),
    )
    encoded = encode_osc_message(message)
    assert decode_osc_message(encoded) == message
    with pytest.raises(ValueError, match="finite"):
        OscMessage("/fixture/osc/v1", (float("nan"),))
    with pytest.raises(ValueError, match="float32"):
        encode_osc_message(OscMessage("/fixture/osc/v1", (1e100,)))
    with pytest.raises(ValueError, match="trailing"):
        decode_osc_message(encoded + b"\x00\x00\x00\x00")


def test_wire_envelope_round_trips_exact_canonical_request_bytes() -> None:
    request = _request()
    sendable = _evaluation(request).to_sendable_request()
    config = _config("dry_run")

    wire_message = build_physical_output_wire_message(sendable, config)
    datagram = wire_message.to_bytes()
    decoded = decode_physical_output_wire_message(datagram)
    osc_message = decode_osc_message(datagram)

    assert decoded == wire_message
    assert decoded.request_json_bytes == request.to_json_bytes()
    assert sha256(decoded.request_json_bytes).hexdigest() == decoded.request_sha256
    assert decoded.candidate_id == physical_output_candidate_id(request)
    assert decoded.safety_binding_sha256 == sendable.binding_sha256
    assert osc_message.address == PHYSICAL_OUTPUT_OSC_ADDRESS
    assert osc_message.arguments[0] == PHYSICAL_OUTPUT_WIRE_SCHEMA_VERSION
    with pytest.raises(ValueError, match="unexpected"):
        decode_physical_output_wire_message(
            encode_osc_message(OscMessage("/other/v1", ("not output",)))
        )


def test_injected_wire_encoder_is_pure_input_boundary_and_content_identity_bound() -> None:
    permission = PhysicalOutputPermission(mode="dry_run")
    lifecycle, sendable = _active_lifecycle(permission)
    identity = PhysicalOutputCodecIdentity.from_settings(
        "fixture-robot-wire",
        "v1",
        {"axes": ["first", "second"], "offsets": [0.0, 0.0]},
    )
    encoder = _FixtureWireEncoder(identity)
    config = replace(_config("dry_run"), expected_codec_identity=identity)
    sender = _FakeSender()
    result = PhysicalOutputTransportAdapter(
        config,
        encoder=encoder,
        sender=sender,
        clock=_Clock(),
    ).dispatch(lifecycle, sendable)

    assert result.status == "dry_run_ready", result
    assert len(encoder.seen) == 1
    envelope = encoder.seen[0]
    assert envelope.request is sendable.request
    assert envelope.request_json_bytes == sendable.request.to_json_bytes()
    encoded = result.encoded_datagram
    assert encoded is not None
    assert encoded.codec_identity == identity
    assert encoded.codec_identity.identity_sha256 == identity.identity_sha256
    assert encoded.osc_message.address == "/fixture/physical-output/v1"
    assert encoded.datagram == encode_osc_message(encoded.osc_message)
    assert encoded.datagram_sha256 == sha256(encoded.datagram).hexdigest()
    assert result.datagram == encoded.datagram
    assert sender.prepare_calls == []
    assert sender.send_calls == []

    different_settings = PhysicalOutputCodecIdentity.from_settings(
        identity.codec_id,
        identity.codec_version,
        {"axes": ["second", "first"], "offsets": [0.0, 0.0]},
    )
    assert different_settings.identity_sha256 != identity.identity_sha256
    with pytest.raises(ValueError, match="does not match"):
        PhysicalOutputTransportAdapter(
            replace(config, expected_codec_identity=different_settings),
            encoder=encoder,
        )


def test_disabled_dry_run_and_recording_modes_never_call_sender() -> None:
    permission = PhysicalOutputPermission(mode="dry_run")
    disabled_lifecycle, sendable = _active_lifecycle(permission)
    disabled_sender = _FakeSender()
    disabled = PhysicalOutputTransportAdapter(
        _config("disabled"), sender=disabled_sender
    ).dispatch(disabled_lifecycle, sendable)
    assert disabled.status == "disabled"
    assert disabled_sender.prepare_calls == []
    assert disabled_sender.send_calls == []

    dry_lifecycle, dry_sendable = _active_lifecycle(permission)
    dry_sender = _FakeSender()
    dry_run = PhysicalOutputTransportAdapter(
        _config("dry_run"), sender=dry_sender, clock=_Clock()
    ).dispatch(dry_lifecycle, dry_sendable)
    assert dry_run.status == "dry_run_ready", dry_run
    assert dry_run.local_send_result.status == "not_attempted"
    assert dry_run.acknowledgement.status == "unavailable"
    assert dry_run.acknowledgement.reason == "transport_not_attempted"
    assert dry_lifecycle.latest_sendable_request is None
    assert dry_sender.prepare_calls == []
    assert dry_sender.send_calls == []
    dry_trace = dry_lifecycle.trace()
    assert dry_trace.events[-1].event_kind == "request_claimed"
    dry_trace_bytes = dry_trace.to_jsonl_bytes()
    decoded_dry_trace = PhysicalOutputLifecycleTrace.from_jsonl(dry_trace_bytes)
    assert decoded_dry_trace == dry_trace
    assert decoded_dry_trace.to_jsonl_bytes() == dry_trace_bytes

    recording_lifecycle, recording_sendable = _active_lifecycle(permission)
    recording_sender = _FakeSender()
    sink = _RecordingSink()
    recorded = PhysicalOutputTransportAdapter(
        _config("recording"),
        sender=recording_sender,
        recording_sink=sink,
        clock=_Clock(),
    ).dispatch(recording_lifecycle, recording_sendable)
    assert recorded.status == "recorded"
    assert recorded.recording_result == PhysicalOutputRecordingResult(
        status="recorded",
        byte_count=len(recorded.datagram or b""),
    )
    assert recorded.local_send_result.status == "not_attempted"
    assert recorded.acknowledgement.reason == "transport_not_attempted"
    assert len(sink.records) == 1
    assert sink.records[0] == recorded.encoded_datagram
    assert sink.records[0].codec_identity == _config("recording").expected_codec_identity
    assert sink.records[0].datagram == recorded.datagram
    assert recording_sender.prepare_calls == []
    assert recording_sender.send_calls == []


def test_raw_request_cannot_enter_transport_and_gate_mismatch_clears_lifecycle() -> None:
    permission = _permission()
    lifecycle, sendable = _active_lifecycle(permission)
    sender = _FakeSender()
    adapter = PhysicalOutputTransportAdapter(
        _config(
            "transmission_enabled",
            operator_enable=PhysicalOutputOperatorEnable("other-operator", "other-enable"),
        ),
        sender=sender,
    )
    raw_result = adapter.dispatch(lifecycle, sendable.request)
    assert raw_result.status == "rejected"
    assert raw_result.reason == "physical_output_sendable_binding_required"
    assert lifecycle.state == "stopped"
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None

    mismatched_lifecycle, _ = _active_lifecycle(permission)
    _, stale_sendable = _active_lifecycle(permission)
    stale_result = adapter.dispatch(mismatched_lifecycle, stale_sendable)
    assert stale_result.status == "rejected"
    assert stale_result.reason == "physical_output_sendable_request_not_latest"
    assert mismatched_lifecycle.state == "stopped"
    assert mismatched_lifecycle.latest_request is None
    assert mismatched_lifecycle.latest_sendable_request is None

    lifecycle, sendable = _active_lifecycle(permission)
    assert sender.prepare_calls == []

    rejected = adapter.dispatch(lifecycle, sendable)
    assert rejected.status == "rejected"
    assert rejected.reason == "physical_output_operator_enable_mismatch"
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None
    assert lifecycle.state == "stopped"
    assert sender.prepare_calls == []
    assert sender.send_calls == []


def test_transmission_requires_latest_bound_allow_and_reports_local_send_separately() -> None:
    clock = _Clock()
    permission = _permission()
    lifecycle, sendable = _active_lifecycle(permission, clock=clock)
    sender = _FakeSender()
    adapter = PhysicalOutputTransportAdapter(
        _config(
            "transmission_enabled",
            operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
        ),
        sender=sender,
        clock=clock,
    )

    result = adapter.dispatch(lifecycle, sendable)

    assert result.status == "transmission_attempted"
    assert result.attempt is not None
    assert result.attempt.request_sha256 == sendable.request_sha256
    assert result.attempt.safety_binding_sha256 == sendable.binding_sha256
    assert result.local_send_result.status == "simulated_acceptance"
    assert result.local_send_result.evidence_kind == "simulated"
    assert result.attempt.evidence_kind == "simulated"
    assert result.encoded_datagram is not None
    assert result.attempt.codec_identity == result.encoded_datagram.codec_identity
    assert result.local_send_result.datagram_bytes_accepted == len(result.datagram or b"")
    assert result.acknowledgement.status == "unavailable"
    assert result.acknowledgement.reason == "receiver_ack_not_configured"
    assert decode_physical_output_wire_message(sender.send_calls[0]) == result.wire_message
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None
    assert sender.prepare_calls == [_config("transmission_enabled", operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1")).endpoint]
    assert len(sender.send_calls) == 1


def test_two_adapters_cannot_dispatch_the_same_latest_request_twice() -> None:
    clock = _Clock()
    permission = _permission()
    lifecycle, sendable = _active_lifecycle(permission, clock=clock)
    barrier = Barrier(2)
    sender_a = _FakeSender(prepare_barrier=barrier)
    sender_b = _FakeSender(prepare_barrier=barrier)
    config = _config(
        "transmission_enabled",
        operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
    )
    adapter_a = PhysicalOutputTransportAdapter(config, sender=sender_a, clock=clock)
    adapter_b = PhysicalOutputTransportAdapter(config, sender=sender_b, clock=clock)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(adapter_a.dispatch, lifecycle, sendable)
        future_b = pool.submit(adapter_b.dispatch, lifecycle, sendable)
        results = (future_a.result(timeout=3.0), future_b.result(timeout=3.0))

    assert sum(len(sender.send_calls) for sender in (sender_a, sender_b)) == 1
    assert sorted(result.status for result in results) == ["rejected", "transmission_attempted"]
    assert lifecycle.latest_sendable_request is None


def test_stop_waits_for_inflight_bounded_send_and_prevents_later_dispatch() -> None:
    clock = _Clock()
    permission = _permission()
    lifecycle, sendable = _active_lifecycle(permission, clock=clock)
    send_entered = Event()
    send_release = Event()
    sender = _FakeSender(send_entered=send_entered, send_release=send_release)
    config = _config(
        "transmission_enabled",
        operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
    )
    adapter = PhysicalOutputTransportAdapter(config, sender=sender, clock=clock)
    stop_started = Event()
    stop_finished = Event()
    stop_results = []

    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatch_future = pool.submit(adapter.dispatch, lifecycle, sendable)
        assert send_entered.wait(timeout=1.0)

        def stop() -> None:
            stop_started.set()
            stop_results.append(adapter.stop(lifecycle, now_s=1.1))
            stop_finished.set()

        stop_future = pool.submit(stop)
        assert stop_started.wait(timeout=1.0)
        assert not stop_finished.wait(timeout=0.05)
        send_release.set()
        dispatch_result = dispatch_future.result(timeout=2.0)
        stop_future.result(timeout=2.0)

    assert dispatch_result.status == "transmission_attempted"
    assert stop_results[0].state == "stopped"
    assert lifecycle.state == "stopped"
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None
    later = adapter.dispatch(lifecycle, sendable)
    assert later.status == "rejected"
    assert len(sender.send_calls) == 1


def test_timeout_failure_is_recorded_and_cannot_be_retried() -> None:
    clock = _Clock()
    permission = _permission()
    lifecycle, sendable = _active_lifecycle(permission, clock=clock)
    sender = _FakeSender(error=TimeoutError("bounded fake timeout"))
    adapter = PhysicalOutputTransportAdapter(
        _config(
            "transmission_enabled",
            operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
        ),
        sender=sender,
        clock=clock,
    )

    result = adapter.dispatch(lifecycle, sendable)
    retry = adapter.dispatch(lifecycle, sendable)

    assert result.status == "transmission_attempted"
    assert result.reason == "physical_output_local_datagram_send_failed"
    assert result.attempt is not None
    assert result.local_send_result.status == "failed"
    assert result.local_send_result.error_type == "TimeoutError"
    assert result.acknowledgement.status == "unavailable"
    assert lifecycle.state == "failed"
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None
    assert retry.status == "rejected"
    assert len(sender.send_calls) == 1


@pytest.mark.parametrize(
    "invalid_time",
    [float("nan"), float("inf"), float("-inf")],
)
def test_non_finite_dispatch_time_invalidates_latest_request(invalid_time: float) -> None:
    clock = _Clock()
    lifecycle, sendable = _active_lifecycle(_permission(), clock=clock)
    sender = _FakeSender()
    adapter = PhysicalOutputTransportAdapter(
        _config(
            "transmission_enabled",
            operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
        ),
        sender=sender,
        clock=clock,
    )

    result = adapter.dispatch(lifecycle, sendable, now_s=invalid_time)

    assert result.status == "rejected"
    assert lifecycle.state == "failed"
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None
    failure = lifecycle.trace().events[-1]
    assert failure.event_kind == "failure"
    assert failure.reason == "physical_output_transport_clock_invalid"
    assert failure.timestamp_s is None

    retry = adapter.dispatch(lifecycle, sendable, now_s=1.0)
    assert retry.status == "rejected"
    assert retry.reason == "physical_output_lifecycle_not_active"
    assert sender.prepare_calls == []
    assert sender.send_calls == []


def _authorization_grant_fields(
    sendable: PhysicalOutputSendableRequest,
    config: PhysicalOutputTransportConfig,
    permission: PhysicalOutputPermission,
    context_sha256: str,
) -> dict[str, object]:
    request = sendable.request
    return {
        "target_robot_id": request.target_robot_id,
        "endpoint_id": request.endpoint_id,
        "software_revision": request.software_revision,
        "session_id": request.session_id,
        "sequence": request.sequence,
        "request_sha256": sendable.request_sha256,
        "safety_binding_sha256": sendable.binding_sha256,
        "candidate_id": sendable.evaluation.candidate_id,
        "codec_identity_sha256": config.expected_codec_identity.identity_sha256,
        "config_sha256": sha256(config.to_json_bytes()).hexdigest(),
        "transmission_permission_sha256": sha256(permission.to_json_bytes()).hexdigest(),
        "actuation_permission_sha256": sha256(b"synthetic-test-actuation-permission").hexdigest(),
        "authorization_context_sha256": context_sha256,
        "issued_at_s": 1.0,
        "expires_at_s": 2.0,
    }


def test_transport_config_v2_round_trips_an_explicit_external_gate() -> None:
    config = replace(
        _config("disabled"),
        schema_version=PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2,
        external_authorization_required=True,
    )

    assert PhysicalOutputTransportConfig.from_json(config.to_json_bytes()) == config
    assert config.to_json_value()["external_authorization_required"] is True
    with pytest.raises(ValueError, match="fields are incomplete or unknown"):
        PhysicalOutputTransportConfig.from_mapping(
            {**config.to_json_value(), "unrecognized": True}
        )


def test_v2_transport_requires_and_consumes_one_shot_runtime_grant() -> None:
    clock = _Clock()
    permission = _permission()
    config = replace(
        _config(
            "transmission_enabled",
            operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
        ),
        schema_version=PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2,
        external_authorization_required=True,
    )

    lifecycle, sendable = _active_lifecycle(permission, clock=clock)
    missing_sender = _FakeSender()
    missing_adapter = PhysicalOutputTransportAdapter(
        config,
        sender=missing_sender,
        clock=clock,
    )
    missing = missing_adapter.prepare_dispatch(lifecycle, sendable, now_s=1.0)
    assert missing.status == "rejected"
    assert missing.reason == "physical_output_transport_authorization_grant_required"
    assert missing_sender.prepare_calls == []
    assert missing_sender.send_calls == []

    lifecycle, sendable = _active_lifecycle(permission, clock=clock)
    sender = _FakeSender()
    adapter = PhysicalOutputTransportAdapter(config, sender=sender, clock=clock)
    context_sha256 = sha256(b"synthetic-test-authorization-context").hexdigest()
    grant_fields = _authorization_grant_fields(
        sendable,
        config,
        permission,
        context_sha256,
    )
    with pytest.raises(ValueError, match="only be issued by runtime composition"):
        PhysicalOutputTransportAuthorizationGrant(**grant_fields)  # type: ignore[arg-type]

    grant = _create_physical_output_transport_authorization_grant(**grant_fields)  # type: ignore[arg-type]
    assert grant.validate_for(
        sendable,
        config,
        permission,
        authorization_context_sha256=context_sha256,
        now_s=1.0,
    ) is None
    prepared = adapter.prepare_dispatch(
        lifecycle,
        sendable,
        now_s=1.0,
        authorization_grant=grant,
        authorization_context_sha256=context_sha256,
    )
    assert type(prepared) is PhysicalOutputTransportPreparedDispatch

    result = adapter.dispatch_prepared(prepared, now_s=1.0)

    assert result.status == "transmission_attempted"
    assert len(sender.send_calls) == 1
    assert grant.validate_for(
        sendable,
        config,
        permission,
        authorization_context_sha256=context_sha256,
        now_s=1.0,
    ) == "physical_output_transport_authorization_grant_consumed"
    retry = adapter.dispatch_prepared(prepared, now_s=1.0)
    assert retry.status == "rejected"
    assert len(sender.send_calls) == 1


def test_prepared_dispatch_rechecks_freshness_after_lifecycle_lock_wait() -> None:
    clock = _SecondReadBlockingClock()
    permission = _permission()
    lifecycle, sendable = _active_lifecycle(permission, clock=clock)  # type: ignore[arg-type]
    sender = _FakeSender()
    adapter = PhysicalOutputTransportAdapter(
        _config(
            "transmission_enabled",
            operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
        ),
        sender=sender,
        clock=clock,
    )
    prepared = adapter.prepare_dispatch(lifecycle, sendable)
    assert type(prepared) is PhysicalOutputTransportPreparedDispatch

    with ThreadPoolExecutor(max_workers=1) as pool:
        dispatch_future = pool.submit(adapter.dispatch_prepared, prepared)
        assert clock.second_read_entered.wait(timeout=1.0)
        with lifecycle._lock:
            clock.value = 7.0
            clock.second_read_release.set()
        result = dispatch_future.result(timeout=2.0)

    assert result.status == "rejected"
    assert result.reason in {
        "physical_output_request_stale",
        "physical_output_safety_stale",
    }
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None
    assert sender.prepare_calls == [_config(
        "transmission_enabled",
        operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
    ).endpoint]
    assert sender.send_calls == []


def test_udp_provider_uses_only_injected_socket_and_closes_it() -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.timeout: float | None = None
            self.sent: list[tuple[bytes, tuple[object, ...]]] = []
            self.closed = False

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def sendto(self, data: bytes, address: tuple[object, ...]) -> int:
            self.sent.append((data, address))
            return len(data)

        def close(self) -> None:
            self.closed = True

    fake_socket = FakeSocket()
    factory_calls: list[tuple[int, int]] = []

    def factory(family: int, socket_type: int) -> FakeSocket:
        factory_calls.append((family, socket_type))
        return fake_socket

    endpoint = _config("disabled").endpoint
    sender = UdpDatagramSender(socket_factory=factory)
    assert factory_calls == []
    destination = sender.prepare(endpoint)
    assert factory_calls == []
    receipt = sender.send(destination, b"test datagram")
    assert receipt == DatagramSendReceipt(len(b"test datagram"), "simulated")
    assert factory_calls == [(socket.AF_INET, socket.SOCK_DGRAM)]
    assert fake_socket.timeout == endpoint.timeout_s
    assert fake_socket.sent == [(b"test datagram", (endpoint.host, endpoint.port))]
    assert fake_socket.closed


def test_stale_candidate_or_wrong_target_never_reaches_preflight() -> None:
    permission = _permission()
    lifecycle, sendable = _active_lifecycle(permission)
    sender = _FakeSender()
    wrong_config = replace(
        _config(
            "transmission_enabled",
            operator_enable=PhysicalOutputOperatorEnable("operator-1", "enable-1"),
        ),
        target_robot_id="other-robot",
    )
    result = PhysicalOutputTransportAdapter(wrong_config, sender=sender).dispatch(
        lifecycle,
        sendable,
        now_s=1.0,
    )
    assert result.status == "rejected"
    assert result.reason == "physical_output_transport_identity_mismatch"
    assert sender.prepare_calls == []
    assert sender.send_calls == []
    assert lifecycle.latest_request is None


def test_wire_builder_rejects_unbound_or_nonallow_requests() -> None:
    request = _request()
    config = _config("dry_run")
    unsafe_evaluation = bind_physical_output_safety(
        request,
        replace(
            _safety_input_fixture(),
            candidate_id=physical_output_candidate_id(request),
        ),
        # invalidまたは欠落したevidenceをwire変換でallowへ引き上げない。
        _evaluation(request).decision,
        checked_at_s=request.timestamp_s,
    )
    assert unsafe_evaluation.status == "invalid"
    with pytest.raises(ValueError, match="explicit safety allow"):
        build_physical_output_wire_message(
            unsafe_evaluation.to_sendable_request(),  # type: ignore[arg-type]
            config,
        )
