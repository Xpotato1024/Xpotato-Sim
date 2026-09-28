"""両側prepare後だけ送信する監督。UDPの原子的配送・物理停止の保証ではない。"""
from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from types import MappingProxyType
from xpotato_sim.runtime.output.fast_arm_adapter import FastArmPhysicalOutputSession, FastArmPreparedSubmission
from xpotato_sim.runtime.output.fast_arm_observation import (
    FastArmRouterHealthEvidence,
    resolve_fast_arm_router_health_datagram,
)
from xpotato_sim.runtime.output.safety_gate import PhysicalOutputSafetyEvaluation
from xpotato_sim.schemas.coordinated import CoordinatedInput, identifier, number


@dataclass(frozen=True, slots=True)
class CoordinatedOutputResult:
    state: str
    reason: str | None
    dispatched_arms: tuple[str, ...]
    stop_results: tuple[tuple[str, str, str], ...]
    physical_stop_confirmed: bool = False
    dispatch_call_arms: tuple[str, ...] = ()


class CoordinatedPhysicalOutputGroup:
    """既存gateを再利用するfail-closed監督。再armingには新しい全sessionが必要。"""
    def __init__(self, sessions: Mapping[str, FastArmPhysicalOutputSession], *,
                 scene_preflight: Callable[[Mapping[str, PhysicalOutputSafetyEvaluation]], bool],
                 stop_requesters: Mapping[str, Callable[[], object]], max_input_age_s: float,
                 max_router_health_age_s: float | None = None,
                 clock: Callable[[], float] = monotonic) -> None:
        if not isinstance(sessions, Mapping) or not 1 <= len(sessions) <= 2:
            raise ValueError("one or two explicit arm sessions required")
        for arm, session in sessions.items():
            identifier(arm, "arm_id")
            if type(session) is not FastArmPhysicalOutputSession or session.state != "disarmed":
                raise ValueError("fresh typed sessions are required")
        if (len({s.target_robot_id for s in sessions.values()}) != len(sessions)
                or len({s.session_id for s in sessions.values()}) != len(sessions)):
            raise ValueError("distinct arm targets and session IDs required")
        if not callable(scene_preflight) or not callable(clock):
            raise TypeError("explicit additional scene veto and monotonic clock required")
        if (not isinstance(stop_requesters, Mapping) or set(stop_requesters) != set(sessions)
                or not all(callable(s) for s in stop_requesters.values())):
            raise ValueError("every arm requires an explicit bounded stop requester")
        self.sessions = MappingProxyType(dict(sessions))
        self.stop_requesters = MappingProxyType(dict(stop_requesters))
        self.scene_preflight, self.clock = scene_preflight, clock
        self.max_input_age_s = number(max_input_age_s, "max_input_age_s", positive=True)
        self.max_router_health_age_s = (
            None if max_router_health_age_s is None
            else number(max_router_health_age_s, "max_router_health_age_s", positive=True)
        )
        self.state, self.reason = "disarmed", None
        self._last_input: CoordinatedInput | None = None
        self._last_now: float | None = None
        self._router_health: dict[str, FastArmRouterHealthEvidence] = {}
        self._busy = False
        self._generation = 0
        self._stop_results: tuple[tuple[str, str, str], ...] = ()
        self._lock = RLock()

    def _now(self, value):
        now = number(self.clock() if value is None else value, "host clock")
        with self._lock:
            if self._last_now is not None and now < self._last_now:
                raise ValueError("host_clock_regressed")
            self._last_now = now
        return now

    def _input(self, value: CoordinatedInput, now: float, *, require_neutral=False):
        if (type(value) is not CoordinatedInput or not value.available
                or {e.endpoint_id for e in value.endpoints} != set(self.sessions)):
            raise ValueError("complete_fresh_input_required")
        if not 0 <= now - value.received_at_s <= self.max_input_age_s:
            raise ValueError("input_stale_or_future")
        if require_neutral and not value.neutral:
            raise ValueError("neutral_required")
        old = self._last_input
        if old is not None:
            if value.source_epoch != old.source_epoch:
                raise ValueError("source_epoch_changed")
            if (value.source_sequence < old.source_sequence or value.source_timestamp_s < old.source_timestamp_s
                    or value.received_at_s < old.received_at_s):
                raise ValueError("old_input")
            if value.source_sequence == old.source_sequence and value != old:
                raise ValueError("input_sequence_reused")
        self._last_input = value

    def _require_router_health(self, now: float) -> None:
        if self.max_router_health_age_s is None:
            return
        for arm, session in self.sessions.items():
            evidence = self._router_health.get(arm)
            if evidence is None:
                raise ValueError("router_health_missing:" + arm)
            if evidence.target_robot_id != session.target_robot_id:
                raise ValueError("router_health_target_mismatch:" + arm)
            age = now - evidence.observed_at_s
            if not 0 <= age < self.max_router_health_age_s:
                raise ValueError("router_health_stale:" + arm)
            if evidence.status != "healthy" or evidence.watchdog_tripped:
                raise ValueError("router_health_unhealthy:" + arm + ":" + evidence.status)

    def _result(self, dispatched=(), attempted=()):
        return CoordinatedOutputResult(self.state, self.reason, tuple(dispatched), self._stop_results,
                                       dispatch_call_arms=tuple(attempted))

    def stop(self, reason="operator_stop", *, fault=False) -> CoordinatedOutputResult:
        with self._lock:
            if self.state in ("faulted", "stopped"):
                return self._result()
            self.state = "faulted" if fault else "stopped"
            self.reason = reason
            self._generation += 1
            self._busy = False
        results=[]
        # 許可を先に全側で失効。その後receiver固有の停止を全側で試みる。
        local={}
        for arm, session in self.sessions.items():
            try:
                session.stop()
                local[arm] = "local_permission_revoked"
            except Exception as exc:
                local[arm] = "local_stop_failed:" + type(exc).__name__
                try:
                    session.abort()
                    local[arm] += ";local_abort_attempted"
                except Exception as abort_error:
                    local[arm] += ";local_abort_failed:" + type(abort_error).__name__
        for arm, request in self.stop_requesters.items():
            try:
                outcome = request()
                remote = "stop_request_attempted" if outcome is True else "stop_request_unconfirmed"
            except Exception as exc:
                remote = "stop_request_failed:" + type(exc).__name__
            results.append((arm, local[arm], remote))
        with self._lock:
            self._stop_results = tuple(results)
            return self._result()

    def arm(self, permissions: Mapping[str, tuple], *, neutral: CoordinatedInput, now_s=None) -> CoordinatedOutputResult:
        try:
            now = self._now(now_s)
            with self._lock:
                if self.state != "disarmed" or self._busy:
                    raise RuntimeError("rearm_requires_new_group_and_sessions")
                if not isinstance(permissions, Mapping) or set(permissions) != set(self.sessions):
                    raise ValueError("all_permissions_required")
                self._input(neutral, now, require_neutral=True)
                self._require_router_health(now)
                for arm, session in self.sessions.items():
                    pair = permissions[arm]
                    if type(pair) is not tuple or len(pair) != 2:
                        raise ValueError("two_permissions_required")
                    if not session.arm(*pair, now_s=now).accepted:
                        raise ValueError("arm_permission_rejected:" + arm)
                self.state = "armed"
                return self._result()
        except Exception as exc:
            return self.stop(f"{type(exc).__name__}:{exc}", fault=True)

    def submit(self, evaluations: Mapping[str, PhysicalOutputSafetyEvaluation], *,
               input: CoordinatedInput, now_s=None) -> CoordinatedOutputResult:
        dispatched=[]
        attempted=[]
        try:
            now = self._now(now_s)
            with self._lock:
                if self.state not in ("armed", "active"):
                    return self._result()
                if self._busy:
                    raise RuntimeError("concurrent_batch_submission")
                self._input(input, now)
                self._require_router_health(now)
                if (not isinstance(evaluations, Mapping) or set(evaluations) != set(self.sessions)
                        or any(type(e) is not PhysicalOutputSafetyEvaluation for e in evaluations.values())):
                    raise ValueError("all_arm_evaluations_required")
                frozen = MappingProxyType(dict(evaluations))
                coherence = {(e.request.sequence, e.request.timestamp_s, e.request.cadence_s,
                              e.request.software_revision) for e in frozen.values()}
                if len(coherence) != 1:
                    raise ValueError("batch_identity_or_timing_mismatch")
                if any(s.pending_acknowledgement.status == "pending" for s in self.sessions.values()):
                    raise ValueError("previous_batch_ack_pending")
                self._busy = True
                generation = self._generation
            if self.scene_preflight(frozen) is not True:
                raise ValueError("whole_scene_preflight_rejected")
            prepared={}
            for arm, session in self.sessions.items():
                ticket = session.prepare_submission(frozen[arm], now_s=now_s)
                if type(ticket) is not FastArmPreparedSubmission:
                    raise ValueError("arm_preflight_rejected:" + arm + ":" + str(ticket.reason))
                prepared[arm] = ticket
            if self.scene_preflight(frozen) is not True:
                raise ValueError("whole_scene_preflight_changed")
            for arm, session in self.sessions.items():
                # final dispatchは有限sendだけ。stopと並行した時の順序をここで直列化する。
                with self._lock:
                    if generation != self._generation or self.state not in ("armed", "active"):
                        raise RuntimeError("group_state_changed_during_preflight")
                    dispatch_now = self._now(now_s)
                    self._input(input, dispatch_now)
                    self._require_router_health(dispatch_now)
                    attempted.append(arm)
                    result = session.dispatch_submission(prepared[arm], now_s=now_s)
                    if (result.status != "transmission_attempted" or result.transport_result is None
                            or result.transport_result.local_send_result.status not in ("accepted_by_local_socket", "simulated_acceptance")):
                        raise RuntimeError("arm_dispatch_failed:" + arm + ":" + str(result.reason))
                    dispatched.append(arm)
            with self._lock:
                if generation != self._generation:
                    raise RuntimeError("group_stopped_during_dispatch")
                self.state, self._busy = "active", False
                return self._result(dispatched, attempted)
        except Exception as exc:
            self.stop(f"{type(exc).__name__}:{exc}", fault=True)
            with self._lock:
                return self._result(dispatched, attempted)

    def observe_health(self, arm_id: str, datagram: bytes, *, now_s=None):
        """router healthをACKとは別入口で受け、active時の異常を全体faultへ昇格する。"""
        try:
            identifier(arm_id, "arm_id")
            now = self._now(now_s)
            with self._lock:
                session = self.sessions[arm_id]
            evidence = resolve_fast_arm_router_health_datagram(
                datagram,
                expected_target_robot_id=session.target_robot_id,
                now_s=now,
            )
            with self._lock:
                self._router_health[arm_id] = evidence
                running = self.state in ("armed", "active")
            if running and (evidence.status != "healthy" or evidence.watchdog_tripped):
                self.stop(
                    "router_health_unhealthy:" + arm_id + ":" + evidence.status,
                    fault=True,
                )
            return evidence
        except Exception as exc:
            with self._lock:
                if type(arm_id) is str and arm_id in self.sessions:
                    self._router_health.pop(arm_id, None)
                else:
                    self._router_health.clear()
                running = self.state in ("armed", "active")
            if running:
                label = arm_id if type(arm_id) is str else "invalid_arm_id"
                self.stop(
                    "router_health_observation_error:" + label + ":" + type(exc).__name__,
                    fault=True,
                )
            return None

    def observe(self, arm_id: str, datagram: bytes, *, now_s=None):
        try:
            now=self._now(now_s)
            with self._lock:
                if self.state not in ("armed", "active"):
                    return None
                session = self.sessions[arm_id]
            evidence = session.observe_router_datagram(datagram, now_s=now)
            if session.state not in ("armed", "active") or evidence.reason not in (
                    "simulated_router_observation_correlated", "router_command_processed"):
                self.stop("router_observation_invalid:" + str(evidence.reason), fault=True)
            return evidence
        except Exception as exc:
            self.stop(f"observation_error:{type(exc).__name__}", fault=True)
            return None

    def poll(self, *, now_s=None) -> CoordinatedOutputResult:
        try:
            now=self._now(now_s)
            with self._lock:
                if self.state not in ("armed", "active"):
                    return self._result()
                self._input(self._last_input, now)
                self._require_router_health(now)
            for arm, session in self.sessions.items():
                session.expire_acknowledgement(now_s=now)
                if session.state not in ("armed", "active"):
                    raise RuntimeError("arm_ack_timeout_or_failure:" + arm)
            with self._lock:
                return self._result()
        except Exception as exc:
            return self.stop(f"{type(exc).__name__}:{exc}", fault=True)
