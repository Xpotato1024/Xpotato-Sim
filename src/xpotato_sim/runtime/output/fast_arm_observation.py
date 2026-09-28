"""FastArmの応答判定と有限tick。通信・許可・物理状態は所有しない。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import Literal, Protocol, runtime_checkable

from xpotato_sim.plugins.robots.fast_arm.adapter.physical_output import (
    FastArmJointWireCommand,
    parse_fast_arm_router_observation,
    parse_fast_arm_router_target_health,
    router_observation_matches,
)
from xpotato_sim.transport.osc import decode_osc_message

FAST_ARM_ACK_STATUS = Literal["not_applicable", "pending", "router_command_observed", "unavailable"]


def _identifier(name: str, value: object) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty canonical string")
    return value


def observation_timestamp(value: object) -> float:
    """呼出側の時刻を検証する。時計の取得や別clockへのfallbackはしない。"""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("observation clock must be numeric")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("observation clock must be finite") from exc
    if not isfinite(result):
        raise ValueError("observation clock must be finite")
    return result


@dataclass(frozen=True, slots=True)
class FastArmAcknowledgementEvidence:
    """既存ACK DTO。疑似受信を物理運動・停止の証拠へ昇格させない。"""

    status: FAST_ARM_ACK_STATUS
    reason: str
    attempt_id: str | None = None
    source_token: str | None = None
    target_robot_id: str | None = None
    observed_at_s: float | None = None
    schema_version: str = "fast-arm-ack-evidence/v1"

    def __post_init__(self) -> None:
        if self.schema_version != "fast-arm-ack-evidence/v1":
            raise ValueError("unsupported FastArm acknowledgement schema_version")
        if self.status not in {"not_applicable", "pending", "router_command_observed", "unavailable"}:
            raise ValueError("unknown FastArm acknowledgement status")
        _identifier("acknowledgement reason", self.reason)
        if self.status == "router_command_observed":
            _identifier("attempt_id", self.attempt_id)
            _identifier("source_token", self.source_token)
            _identifier("target_robot_id", self.target_robot_id)
            if self.observed_at_s is None:
                raise ValueError("router observation requires observed_at_s")
        if self.observed_at_s is not None:
            object.__setattr__(self, "observed_at_s", observation_timestamp(self.observed_at_s))


@dataclass(frozen=True, slots=True)
class FastArmPendingObservation:
    """一回のattemptの期待値。送信済みであること自体の証拠ではない。"""

    attempt_id: str
    expected_command: FastArmJointWireCommand
    deadline_s: float
    evidence_kind: Literal["local_socket", "simulated"]
    started_at_s: float | None = None

    def __post_init__(self) -> None:
        _identifier("attempt_id", self.attempt_id)
        if type(self.expected_command) is not FastArmJointWireCommand:
            raise TypeError("pending observation requires FastArmJointWireCommand")
        if self.evidence_kind not in {"local_socket", "simulated"}:
            raise ValueError("invalid observation evidence kind")
        object.__setattr__(self, "deadline_s", observation_timestamp(self.deadline_s))
        if self.started_at_s is not None:
            start = observation_timestamp(self.started_at_s)
            if self.deadline_s <= start:
                raise ValueError("observation deadline must follow attempt start")
            object.__setattr__(self, "started_at_s", start)


def pending_fast_arm_acknowledgement(pending: FastArmPendingObservation) -> FastArmAcknowledgementEvidence:
    """同一pending内容から既存の状態表示を組み立てる。"""
    return FastArmAcknowledgementEvidence(
        "pending",
        "awaiting_simulated_router_observation" if pending.evidence_kind == "simulated"
        else "awaiting_correlated_router_command_observation",
        pending.attempt_id, pending.expected_command.source_token, pending.expected_command.target_robot_id,
    )


def expired_fast_arm_acknowledgement(
    pending: FastArmPendingObservation | None, *, now_s: float,
) -> FastArmAcknowledgementEvidence | None:
    """期限ちょうどをtimeoutとし、不正時刻で待機を延長しない。"""
    now = observation_timestamp(now_s)
    if pending is None:
        return None
    if pending.started_at_s is not None and now < pending.started_at_s:
        raise ValueError("observation clock precedes attempt")
    if now < pending.deadline_s:
        return None
    return FastArmAcknowledgementEvidence(
        "unavailable",
        "simulated_router_observation_timeout" if pending.evidence_kind == "simulated"
        else "router_command_observation_timeout",
        pending.attempt_id, pending.expected_command.source_token,
        pending.expected_command.target_robot_id, now,
    )


@dataclass(frozen=True, slots=True)
class FastArmObservationResolution:
    """parser判定をsession側の状態変更から分離する。"""

    disposition: Literal["keep", "clear", "expire"]
    evidence: FastArmAcknowledgementEvidence


def resolve_fast_arm_router_datagram(
    pending: FastArmPendingObservation | None, datagram: bytes, *, now_s: float,
) -> FastArmObservationResolution:
    """既存codec・parser・correlationを使う共通の副作用なし判定。"""
    now = observation_timestamp(now_s)
    expired = expired_fast_arm_acknowledgement(pending, now_s=now)
    if expired is not None:
        return FastArmObservationResolution("expire", expired)
    identity = {
        "attempt_id": None if pending is None else pending.attempt_id,
        "source_token": None if pending is None else pending.expected_command.source_token,
        "target_robot_id": None if pending is None else pending.expected_command.target_robot_id,
        "observed_at_s": now,
    }
    try:
        message = decode_osc_message(datagram)
        observed = parse_fast_arm_router_observation(message.address, message.arguments)
    except (ValueError, TypeError, OverflowError):
        return FastArmObservationResolution("keep", FastArmAcknowledgementEvidence(
            "unavailable", "router_observation_malformed", **identity,
        ))
    if pending is None:
        return FastArmObservationResolution("keep", FastArmAcknowledgementEvidence(
            "unavailable", "router_observation_without_pending_attempt",
        ))
    if not router_observation_matches(observed, pending.expected_command):
        return FastArmObservationResolution("keep", FastArmAcknowledgementEvidence(
            "unavailable", "router_observation_correlation_mismatch", **identity,
        ))
    simulated = pending.evidence_kind == "simulated"
    return FastArmObservationResolution("clear", FastArmAcknowledgementEvidence(
        "unavailable" if simulated else "router_command_observed",
        "simulated_router_observation_correlated" if simulated else "router_command_processed",
        **identity,
    ))


@dataclass(frozen=True, slots=True)
class FastArmRouterHealthEvidence:
    """router healthの受信事実。router内部clockとhost clockを比較しない。"""

    target_robot_id: str
    status: str
    state_age_s: float | None
    watchdog_tripped: bool
    observed_at_s: float
    schema_version: str = "fast-arm-router-health-evidence/v1"

    def __post_init__(self) -> None:
        if self.schema_version != "fast-arm-router-health-evidence/v1":
            raise ValueError("unsupported FastArm router health evidence schema_version")
        _identifier("target_robot_id", self.target_robot_id)
        object.__setattr__(self, "observed_at_s", observation_timestamp(self.observed_at_s))


def resolve_fast_arm_router_health_datagram(
    datagram: bytes,
    *,
    expected_target_robot_id: str,
    now_s: float,
) -> FastArmRouterHealthEvidence:
    """health datagramをstrictにparseし、呼出側targetと相関する。"""
    now = observation_timestamp(now_s)
    expected = _identifier("expected_target_robot_id", expected_target_robot_id)
    message = decode_osc_message(datagram)
    health = parse_fast_arm_router_target_health(message.address, message.arguments)
    if health.target_robot_id != expected:
        raise ValueError("router health target mismatch")
    return FastArmRouterHealthEvidence(
        target_robot_id=health.target_robot_id,
        status=health.status,
        state_age_s=health.state_age_s,
        watchdog_tripped=health.watchdog_tripped,
        observed_at_s=now,
    )


@runtime_checkable
class FastArmObservationTarget(Protocol):
    """実機sessionとno-I/O sessionが共有する受信・期限・切断境界。"""

    @property
    def state(self) -> str: ...
    @property
    def pending_acknowledgement(self) -> FastArmAcknowledgementEvidence: ...
    def observe_router_datagram(self, datagram: bytes, *, now_s: float) -> FastArmAcknowledgementEvidence: ...
    def expire_acknowledgement(self, *, now_s: float) -> FastArmAcknowledgementEvidence: ...
    def disconnect(self, *, now_s: float) -> object: ...


@dataclass(frozen=True, slots=True)
class FastArmObservationTick:
    """一tickの有限な判定列。無受信も最終expiry結果を残す。"""

    observations: tuple[FastArmAcknowledgementEvidence, ...]
    acknowledgement: FastArmAcknowledgementEvidence
    received_count: int
    budget_exhausted: bool


class _ObservationClockError(ValueError):
    """driver側clockの失敗を受信callbackの失敗と区別する。"""


class BoundedFastArmObservationDriver:
    """callerがtickする有限driver。callback自体を中断するthreadは作らない。"""

    def __init__(
        self, target: FastArmObservationTarget, *, receive_nowait: Callable[[], bytes | None],
        clock: Callable[[], float], max_datagrams: int,
    ) -> None:
        if not isinstance(target, FastArmObservationTarget):
            raise TypeError("observation target contract is required")
        if not callable(receive_nowait) or not callable(clock):
            raise TypeError("explicit receive_nowait and clock are required")
        if type(max_datagrams) is not int or max_datagrams < 1:
            raise ValueError("max_datagrams must be a positive integer")
        self._target, self._receive, self._clock = target, receive_nowait, clock
        self._budget = max_datagrams
        self._last_now: float | None = None
        self._closed = False

    def _now(self) -> float:
        try:
            now = observation_timestamp(self._clock())
            if self._last_now is not None and now < self._last_now:
                raise ValueError("observation driver clock moved backwards")
        except Exception as exc:
            raise _ObservationClockError("observation driver clock is invalid") from exc
        self._last_now = now
        return now

    def tick(self) -> FastArmObservationTick:
        """読取り前後にexpiryを検査し、stormでも有限件数で制御を返す。"""
        events: list[FastArmAcknowledgementEvidence] = []
        count = 0
        try:
            if self._closed or self._target.state in {"stopped", "aborted", "failed"}:
                return FastArmObservationTick((), self._target.pending_acknowledgement, 0, False)
            now = self._now()
            self._target.expire_acknowledgement(now_s=now)
            for _ in range(self._budget):
                if self._target.state in {"stopped", "aborted", "failed"}:
                    break
                try:
                    datagram = self._receive()
                except OSError:
                    self._target.disconnect(now_s=self._now())
                    self._closed = True
                    break
                now = self._now()
                self._target.expire_acknowledgement(now_s=now)
                if datagram is None or self._target.state in {"stopped", "aborted", "failed"}:
                    break
                count += 1
                events.append(self._target.observe_router_datagram(datagram, now_s=now))
            final = self._target.expire_acknowledgement(now_s=self._now())
        except Exception as failure:
            self._closed = True
            try:
                if isinstance(failure, _ObservationClockError) or self._last_now is None:
                    self._target.expire_acknowledgement(now_s=float("nan"))
                else:
                    # callback failureをclock failureと誤記録しない。
                    self._target.disconnect(now_s=self._last_now)
            except Exception as cleanup_failure:
                failure.add_note(f"observation cleanup failed: {cleanup_failure!r}")
            raise
        return FastArmObservationTick(tuple(events), final, count, count == self._budget)
