"""単一snapshotの候補をまとめて反映するruntime。Robot固有IKや送信は所有しない。"""
from __future__ import annotations
from dataclasses import dataclass
from threading import RLock
from typing import Protocol
from xpotato_sim.schemas.command import JointPositionCommand
from xpotato_sim.schemas.coordinated import CoordinatedInput, CoordinatedSnapshot, EndpointVelocity, identifier, number


@dataclass(frozen=True, slots=True)
class PreparedCoordinatedStep:
    before: CoordinatedSnapshot
    command: JointPositionCommand
    predicted: CoordinatedSnapshot
    token: object


class CoordinatedMotionProvider(Protocol):
    endpoint_ids: tuple[str, ...]
    def snapshot(self) -> CoordinatedSnapshot: ...
    def preflight(self) -> bool: ...
    def prepare(self, commands: tuple[EndpointVelocity, ...], dt_s: float) -> PreparedCoordinatedStep: ...
    def commit(self, candidate: PreparedCoordinatedStep) -> CoordinatedSnapshot: ...
    def invalidate(self) -> None: ...
    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CoordinatedStepResult:
    epoch: str
    tick: int
    state: str
    reason: str | None
    before: CoordinatedSnapshot | None
    after: CoordinatedSnapshot | None
    command: JointPositionCommand | None
    input: CoordinatedInput | None


class CoordinatedRuntime:
    """faultはlatchする。入力復帰だけで再開せず、明示restartと新鮮な中立を要求。"""
    def __init__(self, provider: CoordinatedMotionProvider, *, epoch: str, dt_s: float,
                 max_input_age_s: float) -> None:
        self.provider = provider
        self.epoch = identifier(epoch, "epoch")
        self.dt_s = number(dt_s, "dt_s", positive=True)
        self.max_input_age_s = number(max_input_age_s, "max_input_age_s", positive=True)
        if not provider.endpoint_ids or len(set(provider.endpoint_ids)) != len(provider.endpoint_ids):
            raise ValueError("provider must name its endpoints exactly once")
        self.state, self.reason = "waiting_neutral", None
        self._tick = 0
        self._last: CoordinatedInput | None = None
        self._last_now: float | None = None
        self._minimum_received_at_s = 0.0
        self._retired_sources: set[str] = set()
        self._used_epochs = {epoch}
        self._lock = RLock()

    def _trip(self, reason: str) -> None:
        self.state, self.reason = "faulted", reason
        try:
            self.provider.invalidate()
        except Exception as exc:
            self.reason += f"; invalidate_failed:{type(exc).__name__}"

    def fail(self, reason: str) -> None:
        with self._lock:
            if self.state != "faulted":
                self._trip(reason)

    def _observe(self) -> CoordinatedSnapshot | None:
        try:
            return self.provider.snapshot()
        except Exception as exc:
            self._trip(f"snapshot_unavailable:{type(exc).__name__}")
            return None

    def stop(self, reason: str = "operator_stop") -> None:
        with self._lock:
            if self.state != "faulted":
                self.state, self.reason = "stopped", reason
            try:
                self.provider.invalidate()
            except Exception:
                self._trip("stop_invalidation_failed")

    def restart(self, *, epoch: str, now_s: float) -> None:
        with self._lock:
            identifier(epoch, "epoch")
            now = number(now_s, "now_s")
            if self.state not in ("stopped", "faulted"):
                raise RuntimeError("restart requires a stopped/faulted runtime")
            if epoch in self._used_epochs or len(self._used_epochs) >= 128:
                raise ValueError("new bounded execution epoch required")
            if self._last_now is not None and now < self._last_now:
                raise ValueError("monotonic clock moved backwards")
            if self._last is not None and self._last.source_epoch is not None:
                self._retired_sources.add(self._last.source_epoch)
            self._trip("reset_in_progress")
            self.provider.reset()
            if self.provider.preflight() is not True:
                raise RuntimeError("restart preflight rejected")
            self.epoch = epoch
            self._used_epochs.add(epoch)
            self._tick, self._last = 0, None
            self._last_now = self._minimum_received_at_s = now
            self.state, self.reason = "waiting_neutral", None

    def _validate_input_shape(self, value: CoordinatedInput) -> None:
        if type(value) is not CoordinatedInput:
            raise TypeError("typed coordinated input required")
        if {e.endpoint_id for e in value.endpoints} != set(self.provider.endpoint_ids):
            raise ValueError("input must cover every endpoint exactly once")

    def _validate_input(self, value: CoordinatedInput, now: float) -> None:
        self._validate_input_shape(value)
        if not value.available:
            raise ValueError("input_unavailable")
        if not 0 <= now - value.received_at_s <= self.max_input_age_s:
            raise ValueError("input_stale_or_future")
        if value.received_at_s < self._minimum_received_at_s or value.source_epoch in self._retired_sources:
            raise ValueError("previous_execution_input")
        old = self._last
        if old is not None:
            if value.source_epoch != old.source_epoch:
                raise ValueError("input_source_changed")
            if (value.source_sequence < old.source_sequence or value.source_timestamp_s < old.source_timestamp_s
                    or value.received_at_s < old.received_at_s):
                raise ValueError("input_out_of_order")
            if value.source_sequence == old.source_sequence and value != old:
                raise ValueError("input_sequence_reused_with_changed_content")
        self._last = value

    def tick(self, value: CoordinatedInput | None, *, epoch: str, now_s: float) -> CoordinatedStepResult:
        with self._lock:
            before = self._observe()
            command = None
            if self.state in ("faulted", "stopped"):
                return CoordinatedStepResult(self.epoch, self._tick, self.state, self.reason, before, before, None, value)
            try:
                now = number(now_s, "now_s")
                if epoch != self.epoch:
                    raise ValueError("execution_epoch_mismatch")
                if self._last_now is not None and now < self._last_now:
                    raise ValueError("monotonic_clock_regressed")
                self._last_now = now
                if self.state == "waiting_neutral":
                    if value is None:
                        return CoordinatedStepResult(
                            self.epoch, self._tick, self.state, "input_unavailable",
                            before, before, None, None,
                        )
                    self._validate_input_shape(value)
                    if not value.available:
                        return CoordinatedStepResult(
                            self.epoch, self._tick, self.state, "input_unavailable",
                            before, before, None, value,
                        )
                if value is None:
                    raise ValueError("input_unavailable")
                self._validate_input(value, now)
                if self.state == "waiting_neutral":
                    if value.neutral:
                        if self.provider.preflight() is not True:
                            raise ValueError("preflight_rejected")
                        self.state, self.reason = "running", None
                    return CoordinatedStepResult(self.epoch, self._tick, self.state, self.reason, before, before, None, value)
                candidate = self.provider.prepare(value.endpoints, self.dt_s)
                if candidate.before != before:
                    raise ValueError("candidate_snapshot_mismatch")
                after = self.provider.commit(candidate)
                if after != candidate.predicted:
                    raise RuntimeError("provider_commit_contract_violation")
                # wire要求のfreshnessはhost clock。simulation時刻はsnapshotへ別保存する。
                command = JointPositionCommand(timestamp_s=now, joint_angles_rad=candidate.command.joint_angles_rad)
                self._tick += 1
                return CoordinatedStepResult(self.epoch, self._tick, self.state, None, before, after, command, value)
            except Exception as exc:
                self._trip(f"{type(exc).__name__}:{exc}")
                return CoordinatedStepResult(self.epoch, self._tick, self.state, self.reason,
                                             before, self._observe(), command, value)
