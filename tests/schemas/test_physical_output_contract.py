from __future__ import annotations

import json

import pytest

from xpotato_sim.schemas import (
    EndpointVelocityCommand,
    JointPositionCommand,
    MotionCommand,
    PhysicalOutputDecision,
    PhysicalOutputPermission,
    PhysicalOutputRequest,
    decode_physical_output_permission,
    decode_physical_output_request,
    encode_physical_output_permission,
    encode_physical_output_request,
)


def _endpoint_request() -> PhysicalOutputRequest:
    command = EndpointVelocityCommand(
        timestamp_s=1.0,
        velocity_m_s=(0.1, -0.2, 0.0),
        frame="world",
    )
    return PhysicalOutputRequest(
        target_robot_id="fast_arm",
        endpoint_id="tool_endpoint",
        command_semantics="endpoint_velocity_command/v1",
        command=command,
        session_id="session-1",
        sequence=4,
        timestamp_s=1.0,
        cadence_s=0.02,
        software_revision="test-revision:physical-output",
    )


def test_request_accepts_only_typed_robot_command_and_binds_identity() -> None:
    request = _endpoint_request()

    assert request.command is not None
    assert request.command_semantics == "endpoint_velocity_command/v1"
    assert request.to_json_bytes() == encode_physical_output_request(request)

    with pytest.raises(TypeError, match="does not match command_semantics"):
        PhysicalOutputRequest(
            target_robot_id="fast_arm",
            endpoint_id="tool_endpoint",
            command_semantics="joint_position_command/v1",
            command=request.command,
            session_id="session-1",
            sequence=4,
            timestamp_s=1.0,
            cadence_s=0.02,
            software_revision="test-revision:physical-output",
        )
    with pytest.raises(TypeError, match="does not match command_semantics"):
        PhysicalOutputRequest(
            target_robot_id="fast_arm",
            endpoint_id="tool_endpoint",
            command_semantics="endpoint_velocity_command/v1",
            command=MotionCommand(timestamp_s=1.0),  # type: ignore[arg-type]
            session_id="session-1",
            sequence=4,
            timestamp_s=1.0,
            cadence_s=0.02,
            software_revision="test-revision:physical-output",
        )


def test_request_json_round_trip_is_deterministic_and_strict() -> None:
    request = _endpoint_request()
    encoded = encode_physical_output_request(request)

    assert encoded == encode_physical_output_request(
        decode_physical_output_request(encoded)
    )
    assert b"\xef\xbb\xbf" not in encoded

    decoded = json.loads(encoded)
    decoded["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        decode_physical_output_request(decoded)

    duplicate = encoded[:-1] + b',"sequence":4}'
    with pytest.raises(ValueError, match="duplicate field"):
        decode_physical_output_request(duplicate)


def test_request_rejects_invalid_identity_cadence_sequence_and_timestamp() -> None:
    request = _endpoint_request()

    with pytest.raises(ValueError, match="cadence_s must be positive"):
        PhysicalOutputRequest(
            target_robot_id=request.target_robot_id,
            endpoint_id=request.endpoint_id,
            command_semantics=request.command_semantics,
            command=request.command,
            session_id=request.session_id,
            sequence=request.sequence,
            timestamp_s=request.timestamp_s,
            cadence_s=0.0,
            software_revision=request.software_revision,
        )
    with pytest.raises(ValueError, match="non-negative integer"):
        PhysicalOutputRequest(
            target_robot_id="fast_arm",
            endpoint_id="tool_endpoint",
            command_semantics="endpoint_velocity_command/v1",
            command=request.command,
            session_id="session-1",
            sequence=-1,
            timestamp_s=1.0,
            cadence_s=0.02,
            software_revision="test-revision:physical-output",
        )
    with pytest.raises(ValueError, match="must match command timestamp"):
        PhysicalOutputRequest(
            target_robot_id="fast_arm",
            endpoint_id="tool_endpoint",
            command_semantics="endpoint_velocity_command/v1",
            command=request.command,
            session_id="session-1",
            sequence=4,
            timestamp_s=2.0,
            cadence_s=0.02,
            software_revision="test-revision:physical-output",
        )


def test_joint_position_semantics_are_supported_without_transport() -> None:
    command = JointPositionCommand(timestamp_s=0.0, joint_angles_rad=(0.0, 0.1))
    request = PhysicalOutputRequest(
        target_robot_id="fixture_bot",
        endpoint_id="joint_group",
        command_semantics="joint_position_command/v1",
        command=command,
        session_id="session-2",
        sequence=0,
        timestamp_s=0.0,
        cadence_s=0.1,
        software_revision="test-revision:physical-output",
    )

    assert decode_physical_output_request(request.to_json_bytes()) == request


def test_permission_defaults_disabled_and_requires_explicit_operator_gate() -> None:
    disabled = PhysicalOutputPermission()
    assert disabled.mode == "disabled"
    assert disabled.state == "disabled"
    assert not disabled.allows_transmission
    assert not disabled.allows_physical_actuation

    dry_run = PhysicalOutputPermission(mode="dry_run")
    assert dry_run.state == "enabled"
    assert not dry_run.allows_transmission

    enabled = PhysicalOutputPermission(
        mode="transmission_enabled",
        operator_id="operator-1",
        enable_token_id="gate-1",
    )
    assert enabled.allows_transmission
    assert not enabled.allows_physical_actuation

    with pytest.raises(ValueError, match="explicit operator enable gate"):
        PhysicalOutputPermission(mode="physical_actuation")
    with pytest.raises(ValueError, match="cannot carry an operator gate"):
        PhysicalOutputPermission(mode="disabled", operator_id="operator-1")


def test_permission_json_round_trip_preserves_state_and_gate_identity() -> None:
    permission = PhysicalOutputPermission(
        mode="physical_actuation",
        operator_id="operator-1",
        enable_token_id="gate-1",
    )

    encoded = encode_physical_output_permission(permission)
    assert decode_physical_output_permission(encoded) == permission
    assert permission.to_json_bytes() == encoded

    tampered = json.loads(encoded)
    tampered["state"] = "disabled"
    with pytest.raises(ValueError, match="state does not match mode"):
        decode_physical_output_permission(tampered)


def test_decision_rejects_accepted_disabled_permission() -> None:
    with pytest.raises(
        ValueError,
        match="accepted physical output decision requires non-disabled permission",
    ):
        PhysicalOutputDecision(
            request=_endpoint_request(),
            permission=PhysicalOutputPermission(),
            status="accepted",
        )


def test_decision_acceptance_keeps_operator_gate_invariant() -> None:
    request = _endpoint_request()
    accepted = PhysicalOutputDecision(
        request=request,
        permission=PhysicalOutputPermission(mode="dry_run"),
        status="accepted",
    )
    assert accepted.status == "accepted"

    enabled = PhysicalOutputPermission(
        mode="transmission_enabled",
        operator_id="operator-1",
        enable_token_id="gate-1",
    )
    gated = PhysicalOutputDecision(
        request=request,
        permission=enabled,
        status="accepted",
    )
    assert gated.permission.explicitly_enabled
