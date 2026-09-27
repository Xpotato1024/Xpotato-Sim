"""FastArm信号のno-I/O preview。実機permissionや送信能力を持たない。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import ClassVar, Literal

from xpotato_sim.plugins.robots.fast_arm.adapter.physical_output import (
    FastArmJointWireCommand,
    FastArmOutputMapping,
    build_fast_arm_joint_wire_command,
)
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.output.fast_arm_observation import (
    FastArmAcknowledgementEvidence,
    FastArmPendingObservation,
    expired_fast_arm_acknowledgement,
    observation_timestamp,
    pending_fast_arm_acknowledgement,
    resolve_fast_arm_router_datagram,
)
from xpotato_sim.schemas.command import PhysicalOutputRequest
from xpotato_sim.transport.osc import OscMessage, decode_osc_message, encode_osc_message


@dataclass(frozen=True, slots=True)
class FastArmSignalPreview:
    """送信可能wrapperではない、純粋変換の結果。P4がartifactへ記録する。"""

    request: PhysicalOutputRequest
    mapping_sha256: str
    attempt_id: str
    wire_command: FastArmJointWireCommand
    datagram: bytes
    observation_class: ClassVar[Literal["synthetic"]] = "synthetic"

    @property
    def datagram_sha256(self) -> str:
        return sha256(self.datagram).hexdigest()


def build_fast_arm_signal_preview(
    request: PhysicalOutputRequest, mapping: FastArmOutputMapping, *, attempt_id: str,
) -> FastArmSignalPreview:
    """既存productionのpure変換とcodecだけを使い、I/Oを実行しない。"""
    command = build_fast_arm_joint_wire_command(request, mapping, attempt_id=attempt_id)
    datagram = encode_osc_message(OscMessage(command.address, command.osc_arguments))
    return FastArmSignalPreview(request, mapping.identity_sha256, attempt_id, command, datagram)


@dataclass(frozen=True, slots=True)
class FastArmPeerReceipt:
    """受信bytesから復元した値と疑似応答。actual ACKではない。"""

    decoded_command: FastArmJointWireCommand
    response_datagram: bytes
    observation_class: ClassVar[Literal["synthetic"]] = "synthetic"


def emulate_fast_arm_peer(
    datagram: bytes, *, target_robot_id: str, wire_joint_order: tuple[str, ...],
) -> FastArmPeerReceipt:
    """expected commandを受け取らず、実byte列からrepo-owned応答を作る。"""
    message = decode_osc_message(datagram)
    parts = message.address.split("/")
    if len(parts) != 4 or parts[0] or parts[2] != target_robot_id or parts[3] != "joint":
        raise ValueError("peer target or command address mismatch")
    if type(wire_joint_order) is not tuple or not wire_joint_order or any(
        type(name) is not str or not name for name in wire_joint_order
    ):
        raise ValueError("peer requires explicit wire joint names")
    if len(message.arguments) != len(wire_joint_order) or any(type(x) is not float for x in message.arguments):
        raise ValueError("peer requires one OSC float32 per declared joint")
    command = FastArmJointWireCommand(
        source_token=parts[1], target_robot_id=parts[2], joint_order=wire_joint_order,
        position_degrees=message.arguments,
    )
    values = "[" + ", ".join(repr(x) for x in command.position_degrees) + "]"
    response = encode_osc_message(OscMessage(
        f"/router/{command.target_robot_id}/command", (command.source_token, command.command, values),
    ))
    return FastArmPeerReceipt(command, response)


class FastArmSignalSession:
    """一つのsynthetic session。実機の許可・lifecycle・物理状態は扱わない。

    最初の完全requestがsession範囲を固定する。終了後の再利用・自動再送はしない。
    serial/socket/senderを受け取るAPIがなく、単一callerから直列利用する。
    """

    def __init__(
        self, *, profile: RobotProfile, mapping: FastArmOutputMapping,
        acknowledgement_timeout_s: float,
    ) -> None:
        if type(profile) is not RobotProfile or type(mapping) is not FastArmOutputMapping:
            raise TypeError("signal session requires explicit profile and mapping")
        if (mapping.profile_id, mapping.profile_contract_version, mapping.model_contract_version,
            mapping.profile_joint_order) != (profile.profile_id, profile.profile_contract_version,
                                             profile.model_contract_version, profile.canonical_joint_names):
            raise ValueError("signal mapping does not match profile")
        timeout = observation_timestamp(acknowledgement_timeout_s)
        if timeout <= 0:
            raise ValueError("acknowledgement timeout must be positive")
        self._mapping = mapping
        self._timeout = timeout
        self._state = "active"
        self._scope: tuple[object, ...] | None = None
        self._last_sequence = -1
        self._last_request_time: float | None = None
        self._last_now: float | None = None
        self._pending: FastArmPendingObservation | None = None
        self._last = FastArmAcknowledgementEvidence("not_applicable", "no_signal_preview")

    @property
    def state(self) -> str:
        return self._state

    @property
    def pending_acknowledgement(self) -> FastArmAcknowledgementEvidence:
        return self._last if self._pending is None else pending_fast_arm_acknowledgement(self._pending)

    def _now(self, value: float) -> float:
        now = observation_timestamp(value)
        if self._last_now is not None and now < self._last_now:
            raise ValueError("signal session clock moved backwards")
        self._last_now = now
        return now

    def _finish(self, state: str, evidence: FastArmAcknowledgementEvidence) -> FastArmAcknowledgementEvidence:
        self._state, self._pending, self._last = state, None, evidence
        return evidence

    def _invalid_clock(self) -> FastArmAcknowledgementEvidence:
        return self._finish("failed", FastArmAcknowledgementEvidence("unavailable", "fast_arm_clock_invalid"))

    def submit(self, request: PhysicalOutputRequest, *, now_s: float) -> FastArmSignalPreview:
        """一度だけpreviewする。request時刻とhost時刻の絶対値は混同しない。"""
        self.expire_acknowledgement(now_s=now_s)
        if self._state != "active":
            raise RuntimeError("signal session is terminal; use a new session")
        if self._pending is not None:
            raise RuntimeError("router observation is pending")
        if type(request) is not PhysicalOutputRequest:
            raise TypeError("signal session requires PhysicalOutputRequest")
        scope = (request.target_robot_id, request.endpoint_id, request.session_id,
                 request.software_revision, request.cadence_s)
        if self._scope is not None and self._scope != scope:
            raise ValueError("signal request identity changed")
        if request.sequence <= self._last_sequence:
            raise ValueError("signal request sequence must increase")
        if self._last_request_time is not None and request.timestamp_s < self._last_request_time:
            raise ValueError("signal request timestamp moved backwards")
        deadline = now_s + self._timeout
        if not isfinite(deadline) or deadline <= now_s:
            self._invalid_clock()
            raise ValueError("signal observation deadline is invalid")
        preview = build_fast_arm_signal_preview(request, self._mapping, attempt_id=f"signal-preview-{request.sequence}")
        self._pending = FastArmPendingObservation(
            preview.attempt_id, preview.wire_command, deadline, "simulated", started_at_s=now_s,
        )
        self._scope, self._last_sequence, self._last_request_time = scope, request.sequence, request.timestamp_s
        self._last = pending_fast_arm_acknowledgement(self._pending)
        return preview

    def observe_router_datagram(self, datagram: bytes, *, now_s: float) -> FastArmAcknowledgementEvidence:
        """実機sessionと同じ判定を使うが、evidence kindはsimulatedに固定する。"""
        try:
            now = self._now(now_s)
            resolution = resolve_fast_arm_router_datagram(self._pending, datagram, now_s=now)
        except (ValueError, TypeError, OverflowError):
            return self._invalid_clock()
        if self._state != "active":
            return FastArmAcknowledgementEvidence("unavailable", "router_observation_without_pending_attempt")
        if resolution.disposition == "expire":
            return self._finish("failed", resolution.evidence)
        if resolution.disposition == "clear":
            self._pending, self._last = None, resolution.evidence
        return resolution.evidence

    def expire_acknowledgement(self, *, now_s: float) -> FastArmAcknowledgementEvidence:
        try:
            expired = expired_fast_arm_acknowledgement(self._pending, now_s=self._now(now_s))
        except (ValueError, TypeError, OverflowError):
            return self._invalid_clock()
        if expired is not None:
            return self._finish("failed", expired)
        return self._last

    def stop(self, *, now_s: float) -> FastArmAcknowledgementEvidence:
        """local previewを停止する。physical stop commandは生成しない。"""
        self.expire_acknowledgement(now_s=now_s)
        if self._state != "active":
            return self._last
        return self._finish("stopped", FastArmAcknowledgementEvidence("unavailable", "signal_preview_stopped"))

    def disconnect(self, *, now_s: float) -> FastArmAcknowledgementEvidence:
        """疑似受信側の切断をterminalとし、pendingを破棄する。"""
        self.expire_acknowledgement(now_s=now_s)
        if self._state != "active":
            return self._last
        return self._finish("failed", FastArmAcknowledgementEvidence("unavailable", "transport_disconnected"))
