from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from math import inf, nan
from threading import Barrier

import pytest

from xpotato_sim.runtime.output import (
    bind_physical_output_safety,
    evaluate_and_bind_physical_output_safety,
    PhysicalOutputLifecycle,
    PhysicalOutputLifecycleEvent,
    PhysicalOutputLifecycleTrace,
    PhysicalOutputRecordingSink,
    physical_output_candidate_id,
)
from xpotato_sim.runtime.output.permission import evaluate_physical_output_permission
from xpotato_sim.runtime.safety.collision_policy import CollisionStatus
from xpotato_sim.runtime.safety.limit_resolution import LimitResolutionStatus
from xpotato_sim.runtime.safety.trajectory_feasibility import FeasibilityStatus
from xpotato_sim.schemas import PhysicalOutputPermission

from tests.schemas.test_physical_output_contract import _endpoint_request
from tests.runtime.test_physical_safety_core import _input as _safety_input_fixture
from tests.support.output_candidate_evidence import joint_request, observed_safety_input


def _request(**changes: object):
    return joint_request(**changes)


def _safety_input(request, *, base=None, include_revision: bool = True):
    safety_input = observed_safety_input(request) if base is None else base
    safety_input = replace(
        safety_input,
        candidate_id=physical_output_candidate_id(request),
    )
    if include_revision:
        provenance = tuple(dict.fromkeys((*safety_input.provenance, f"software_revision:{request.software_revision}")))
        safety_input = replace(safety_input, provenance=provenance)
    else:
        safety_input = replace(safety_input, provenance=tuple(p for p in safety_input.provenance if not p.startswith("software_revision:")))
    return safety_input


def _evaluation(request, *, checked_at_s: float | None = None, base=None, include_revision: bool = True):
    return evaluate_and_bind_physical_output_safety(
        request,
        _safety_input(request, base=base, include_revision=include_revision),
        checked_at_s=request.timestamp_s if checked_at_s is None else checked_at_s,
    )


def _submit(lifecycle, request, **kwargs):
    max_safety_age_s = kwargs.pop("max_safety_age_s", kwargs.get("max_age_s"))
    return lifecycle.submit(
        _evaluation(request),
        max_safety_age_s=max_safety_age_s,
        **kwargs,
    )


def test_default_disabled_and_explicit_arm_are_fail_closed() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    request = _request()

    assert lifecycle.state == "disabled"
    raw = lifecycle.submit(request)
    assert not raw.accepted
    assert raw.reason == "physical_safety_binding_required"
    rejected = _submit(lifecycle, request)
    assert not rejected.accepted
    assert rejected.reason == "lifecycle_state_not_accepting"
    assert lifecycle.state == "disabled"
    repeated = _submit(lifecycle, request)
    assert not repeated.accepted
    assert repeated.reason == "lifecycle_state_not_accepting"
    assert lifecycle.trace().events[-1].request_sequence is None

    assert not lifecycle.arm(PhysicalOutputPermission()).accepted
    assert lifecycle.state == "disabled"
    assert lifecycle.arm(PhysicalOutputPermission(mode="dry_run")).accepted
    assert lifecycle.state == "armed"
    assert _submit(lifecycle, request, now_s=1.0, max_age_s=1.0).accepted


def test_submit_tracks_latest_state_but_rejects_duplicate_late_and_stale_requests() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    permission = PhysicalOutputPermission(mode="dry_run")
    request = _request()
    lifecycle.arm(permission)

    accepted = _submit(lifecycle, request, now_s=1.0, max_age_s=1.0)
    assert accepted.accepted
    assert lifecycle.state == "active"
    assert lifecycle.latest_request == request
    assert lifecycle.last_request_sequence == request.sequence

    duplicate = _submit(lifecycle, request, now_s=1.01)
    assert not duplicate.accepted
    assert duplicate.reason == "duplicate_or_out_of_order_sequence"
    assert lifecycle.latest_request == request

    late = _submit(lifecycle, replace(request, sequence=3), now_s=1.02)
    assert not late.accepted
    assert late.reason == "duplicate_or_out_of_order_sequence"
    assert lifecycle.latest_request == request
    assert lifecycle.trace().events[-1].request_sequence is None

    stale_request = replace(
        request,
        sequence=5,
        command=replace(request.command, timestamp_s=2.0),
        timestamp_s=2.0,
    )
    stale = _submit(
        lifecycle,
        stale_request,
        now_s=3.0,
        max_age_s=0.5,
        max_safety_age_s=2.0,
    )
    assert not stale.accepted
    assert stale.reason == "physical_output_request_stale"
    assert lifecycle.state == "hold"
    assert lifecycle.latest_request is None
    assert lifecycle.last_request_sequence == stale_request.sequence

    replay = _submit(
        lifecycle,
        stale_request,
        now_s=2.1,
        max_age_s=0.5,
        max_safety_age_s=2.0,
    )
    assert not replay.accepted
    assert replay.reason == "duplicate_or_out_of_order_sequence"


def test_session_mismatch_rejection_is_not_bound_to_current_sequence() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    permission = PhysicalOutputPermission(mode="dry_run")
    lifecycle.arm(permission)
    foreign = replace(_request(), session_id="session-foreign", sequence=99)

    rejected = _submit(lifecycle, foreign, now_s=1.0)
    assert not rejected.accepted
    assert rejected.reason == "session_mismatch"
    assert rejected.event is not None
    assert rejected.event.request_sequence is None

    accepted = _submit(lifecycle, _request(), now_s=1.0, max_age_s=1.0)
    assert accepted.accepted
    assert lifecycle.trace().events[-1].request_sequence == 4


def test_safety_hold_reject_stop_stale_and_identity_mismatch_clear_prior_sendable() -> None:
    request = _request()
    wrong_robot_input = _safety_input_fixture()
    wrong_robot = "other-fixture-robot"
    wrong_robot_input = replace(
        wrong_robot_input,
        limit_resolution=replace(wrong_robot_input.limit_resolution, robot_id=wrong_robot),
        collision=replace(
            wrong_robot_input.collision,
            context=replace(wrong_robot_input.collision.context, robot_id=wrong_robot),
        ),
    )
    next_request = replace(
        request,
        sequence=request.sequence + 1,
        command=replace(request.command, timestamp_s=request.timestamp_s + 0.1),
        timestamp_s=request.timestamp_s + 0.1,
    )
    scenarios = (
        (
            "hold",
            _evaluation(next_request, base=_safety_input_fixture(dynamic_authoritative=False)),
            next_request.timestamp_s,
            1.0,
            "safety_hold",
            "held",
        ),
        (
            "unavailable",
            _evaluation(next_request, base=_safety_input_fixture(limits=LimitResolutionStatus.UNKNOWN)),
            next_request.timestamp_s,
            1.0,
            "safety_hold",
            "unavailable",
        ),
        (
            "invalid",
            _evaluation(next_request, base=_safety_input_fixture(dynamic=FeasibilityStatus.INVALID)),
            next_request.timestamp_s,
            1.0,
            "safety_invalid",
            "invalid",
        ),
        (
            "reject",
            _evaluation(next_request, base=_safety_input_fixture(limits=LimitResolutionStatus.MISMATCH)),
            next_request.timestamp_s,
            1.0,
            "safety_rejected",
            "rejected",
        ),
        (
            "stop",
            _evaluation(next_request, base=_safety_input_fixture(collision=CollisionStatus.COLLISION)),
            next_request.timestamp_s,
            1.0,
            "safety_stop",
            "stopped",
        ),
        (
            "stale",
            _evaluation(next_request, checked_at_s=0.0),
            next_request.timestamp_s,
            0.5,
            "safety_hold",
            "allowed",
        ),
        (
            "future decision",
            _evaluation(next_request, checked_at_s=next_request.timestamp_s + 1.0),
            next_request.timestamp_s,
            0.5,
            "safety_hold",
            "allowed",
        ),
        (
            "robot mismatch",
            _evaluation(next_request, base=wrong_robot_input),
            next_request.timestamp_s,
            1.0,
            "safety_rejected",
            "rejected",
        ),
        (
            "revision mismatch",
            _evaluation(next_request, include_revision=False),
            next_request.timestamp_s,
            1.0,
            "safety_rejected",
            "rejected",
        ),
    )

    for name, evaluation, now_s, max_safety_age_s, event_kind, gate_status in scenarios:
        lifecycle = PhysicalOutputLifecycle("session-1")
        assert lifecycle.arm(PhysicalOutputPermission(mode="dry_run")).accepted
        allowed = _submit(
            lifecycle,
            request,
            now_s=request.timestamp_s,
            max_age_s=1.0,
            max_safety_age_s=1.0,
        )
        assert allowed.accepted
        assert lifecycle.latest_sendable_request is not None

        result = lifecycle.submit(
            evaluation,
            now_s=now_s,
            max_age_s=1.0,
            max_safety_age_s=max_safety_age_s,
        )

        assert result.accepted is (event_kind == "safety_stop"), name
        assert result.event is not None
        assert result.event.event_kind == event_kind
        assert result.event.safety_evidence is not None
        assert result.event.safety_evidence.gate_status == gate_status
        assert lifecycle.latest_request is None
        assert lifecycle.latest_sendable_request is None


def test_candidate_mismatch_after_active_clears_latest_sendable_request() -> None:
    request = _request()
    evaluation = _evaluation(request)
    assert evaluation.safety_input is not None
    assert evaluation.decision is not None

    lifecycle = PhysicalOutputLifecycle("session-1")
    assert lifecycle.arm(PhysicalOutputPermission(mode="dry_run")).accepted
    accepted = _submit(
        lifecycle,
        request,
        now_s=request.timestamp_s,
        max_age_s=1.0,
        max_safety_age_s=1.0,
    )
    assert accepted.accepted
    assert lifecycle.latest_sendable_request is not None

    changed_request = replace(
        request,
        sequence=request.sequence + 1,
        command=replace(
            request.command,
            joint_angles_rad=(
                request.command.joint_angles_rad[0] + 0.01,
                *request.command.joint_angles_rad[1:],
            ),
        ),
    )
    mismatched = bind_physical_output_safety(
        changed_request,
        evaluation.safety_input,
        evaluation.decision,
        checked_at_s=changed_request.timestamp_s,
    )
    result = lifecycle.submit(mismatched, now_s=changed_request.timestamp_s + 0.1)

    assert not result.accepted
    assert result.reason == "physical_safety_candidate_mismatch"
    assert result.event is not None
    assert result.event.event_kind == "safety_rejected"
    assert result.event.safety_evidence is not None
    assert result.event.safety_evidence.gate_reason == "physical_safety_candidate_mismatch"
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None


def test_raw_intent_cannot_replace_an_active_sendable_request() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    request = _request()
    assert lifecycle.arm(PhysicalOutputPermission(mode="dry_run")).accepted
    accepted = _submit(
        lifecycle,
        request,
        now_s=request.timestamp_s,
        max_age_s=1.0,
    )
    assert accepted.accepted

    rejected = lifecycle.submit(
        replace(request, sequence=request.sequence + 1),
        now_s=request.timestamp_s + 0.1,
    )

    assert not rejected.accepted
    assert rejected.reason == "physical_safety_binding_required"
    assert lifecycle.latest_request is None
    assert lifecycle.latest_sendable_request is None


def test_reconnect_does_not_rearm_and_explicit_rearm_keeps_stale_commands_out() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    permission = PhysicalOutputPermission(mode="dry_run")
    request = _request()
    lifecycle.arm(permission)
    _submit(lifecycle, request, now_s=1.0, max_age_s=1.0)
    lifecycle.source_disconnected(timestamp_s=2.0)

    reconnect = lifecycle.reconnect(timestamp_s=3.0)
    assert reconnect.state_before == "hold"
    assert reconnect.state_after == "hold"
    assert lifecycle.state == "hold"
    assert not _submit(lifecycle, replace(request, sequence=5), now_s=3.1).accepted

    assert lifecycle.arm(permission, session_id="session-2").accepted
    assert lifecycle.state == "armed"
    next_request = replace(
        request,
        session_id="session-2",
        sequence=6,
        command=replace(request.command, timestamp_s=3.2),
        timestamp_s=3.2,
    )
    assert _submit(lifecycle, next_request, now_s=3.2, max_age_s=1.0).accepted


def test_stop_is_idempotent_and_bounded() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1", shutdown_timeout_s=1.0)
    permission = PhysicalOutputPermission(mode="dry_run")
    lifecycle.arm(permission)
    _submit(lifecycle, _request(), now_s=1.0, max_age_s=1.0)

    stopping = lifecycle.operator_stop(now_s=2.0)
    assert stopping.accepted
    assert lifecycle.state == "stopping"
    assert lifecycle.stop_deadline_s == 3.0
    assert lifecycle.latest_request is None

    repeated = lifecycle.operator_stop(now_s=2.5)
    assert repeated.accepted
    assert repeated.reason is None
    assert lifecycle.state == "stopping"

    stopped = lifecycle.complete_stop(now_s=2.9)
    assert stopped.accepted
    assert lifecycle.state == "stopped"
    assert lifecycle.stop_deadline_s is None
    assert lifecycle.complete_stop(now_s=3.0).accepted
    assert lifecycle.state == "stopped"

    timed_out = PhysicalOutputLifecycle("session-timeout", shutdown_timeout_s=1.0)
    timed_out.arm(permission)
    _submit(
        timed_out,
        _request(session_id="session-timeout"),
        now_s=0.0,
        max_age_s=1.0,
    )
    timed_out.operator_stop(now_s=0.0)
    exceeded = timed_out.complete_stop(now_s=1.1)
    assert not exceeded.accepted
    assert exceeded.reason == "bounded_shutdown_deadline_exceeded"
    assert timed_out.state == "failed"
    assert timed_out.stop_deadline_s is None


def test_complete_stop_rejects_timestamp_before_stop_start_without_mutation() -> None:
    lifecycle = PhysicalOutputLifecycle("session-order", shutdown_timeout_s=2.0)
    lifecycle.arm(PhysicalOutputPermission(mode="dry_run"))
    lifecycle.operator_stop(now_s=10.0)
    before_events = lifecycle.events

    result = lifecycle.complete_stop(now_s=9.0)

    assert not result.accepted
    assert result.reason == "stop_completion_before_start"
    assert lifecycle.state == "stopping"
    assert lifecycle.stop_started_s == 10.0
    assert lifecycle.stop_deadline_s == 12.0
    assert lifecycle.events == before_events


@pytest.mark.parametrize("timestamp", (nan, inf, -inf))
@pytest.mark.parametrize(
    "operation",
    ("arm", "submit", "source_stale", "source_invalid", "abort", "fail", "cleanup", "reconnect"),
)
def test_non_finite_transition_timestamp_cannot_partially_commit(
    timestamp: float,
    operation: str,
) -> None:
    lifecycle = PhysicalOutputLifecycle("session-timestamp")
    permission = PhysicalOutputPermission(mode="dry_run")
    request = _request()
    if operation != "arm":
        lifecycle.arm(permission)
    if operation in {"submit", "source_stale", "source_invalid", "abort", "fail", "cleanup", "reconnect"}:
        if operation != "submit":
            _submit(lifecycle, request, now_s=1.0, max_age_s=1.0)
    before = (
        lifecycle.state,
        lifecycle.permission,
        lifecycle.session_id,
        lifecycle.latest_request,
        lifecycle.last_request_sequence,
        lifecycle.stop_started_s,
        lifecycle.stop_deadline_s,
        lifecycle.events,
    )

    with pytest.raises(ValueError, match="finite"):
        if operation == "arm":
            lifecycle.arm(permission, timestamp_s=timestamp)
        elif operation == "submit":
            _submit(lifecycle, request, now_s=timestamp, max_age_s=1.0)
        elif operation == "source_stale":
            lifecycle.source_stale(timestamp_s=timestamp)
        elif operation == "source_invalid":
            lifecycle.source_invalid("invalid", timestamp_s=timestamp)
        elif operation == "abort":
            lifecycle.abort("abort", timestamp_s=timestamp)
        elif operation == "fail":
            lifecycle.fail("failure", timestamp_s=timestamp)
        elif operation == "cleanup":
            lifecycle.record_cleanup_failure("cleanup", timestamp_s=timestamp)
        else:
            lifecycle.reconnect(timestamp_s=timestamp)

    after = (
        lifecycle.state,
        lifecycle.permission,
        lifecycle.session_id,
        lifecycle.latest_request,
        lifecycle.last_request_sequence,
        lifecycle.stop_started_s,
        lifecycle.stop_deadline_s,
        lifecycle.events,
    )
    assert after == before


def test_source_invalid_aborts_and_new_session_is_required_after_terminal_state() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    permission = PhysicalOutputPermission(mode="dry_run")
    lifecycle.arm(permission)
    _submit(lifecycle, _request(), now_s=1.0, max_age_s=1.0)

    invalid = lifecycle.source_invalid("source_payload_invalid", timestamp_s=2.0)
    assert not invalid.accepted
    assert invalid.reason == "source_payload_invalid"
    assert lifecycle.state == "aborted"
    assert lifecycle.latest_request is None
    assert lifecycle.reconnect(timestamp_s=3.0).state_after == "aborted"
    assert not lifecycle.arm(permission).accepted
    assert lifecycle.arm(permission, session_id="session-2").accepted
    assert lifecycle.session_id == "session-2"
    assert lifecycle.state == "armed"
    assert lifecycle.trace().events[-1].session_id == "session-2"


def test_cleanup_failure_does_not_hide_primary_failure() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    lifecycle.arm(PhysicalOutputPermission(mode="dry_run"))
    _submit(lifecycle, _request(), now_s=1.0, max_age_s=1.0)

    def cleanup() -> None:
        raise RuntimeError("cleanup broke")

    result = lifecycle.shutdown(
        now_s=2.0,
        cleanup=cleanup,
        primary_failure="primary_output_failure",
    )
    assert result.accepted
    assert lifecycle.state == "failed"
    assert lifecycle.events[2].event_kind == "failure"
    assert lifecycle.events[2].reason == "primary_output_failure"
    assert lifecycle.events[-1].event_kind == "cleanup_failure"
    assert lifecycle.events[-1].state_after == "failed"
    assert lifecycle.events[-1].reason == "cleanup_failed:RuntimeError"


def test_dry_run_sink_captures_lifecycle_trace_separately_from_output_trace() -> None:
    sink = PhysicalOutputRecordingSink()
    lifecycle = PhysicalOutputLifecycle("session-1", sink=sink)
    permission = PhysicalOutputPermission(mode="dry_run")
    request = _request()
    lifecycle.arm(permission)
    decision = evaluate_physical_output_permission(request, permission)
    _submit(lifecycle, request, now_s=1.0, max_age_s=1.0)
    sink.record_requested(request, permission)
    sink.record_permission_decision(decision)
    lifecycle.source_stale(timestamp_s=2.0)

    assert sink.events[0].event_kind == "requested"
    assert sink.events[1].event_kind == "permitted"
    assert len(sink.lifecycle_events) == len(lifecycle.events)
    lifecycle_trace = PhysicalOutputLifecycleTrace.from_jsonl(
        sink.lifecycle_trace_bytes()
    )
    assert lifecycle_trace.events == lifecycle.events
    assert lifecycle_trace.events[-1].state_after == "hold"


def test_lifecycle_trace_rejects_duplicate_and_out_of_order_artifacts() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    lifecycle.arm(PhysicalOutputPermission(mode="dry_run"), timestamp_s=0.0)
    encoded = lifecycle.trace().to_jsonl_bytes()
    duplicate = encoded[:-2] + b',"event_sequence":0}\n'
    with pytest.raises(ValueError, match="duplicate field"):
        PhysicalOutputLifecycleTrace.from_jsonl(duplicate)

    event_lines = encoded.splitlines()
    reordered = b"\n".join((event_lines[0].replace(b'"event_sequence":0', b'"event_sequence":1', 1), event_lines[0])) + b"\n"
    with pytest.raises(ValueError, match="contiguous"):
        PhysicalOutputLifecycleTrace.from_jsonl(reordered)


def test_lifecycle_trace_reads_legacy_v1_events_and_round_trips_safety_evidence() -> None:
    legacy_event = PhysicalOutputLifecycleEvent(
        event_sequence=0,
        event_kind="armed",
        session_id="legacy-session",
        state_before="disabled",
        state_after="armed",
        timestamp_s=0.0,
        schema_version="physical-output-lifecycle/v1",
    )
    legacy_accepted = PhysicalOutputLifecycleEvent(
        event_sequence=1,
        event_kind="request_accepted",
        session_id="legacy-session",
        state_before="armed",
        state_after="active",
        request_sequence=4,
        timestamp_s=1.0,
        schema_version="physical-output-lifecycle/v1",
    )
    legacy_events = (legacy_event, legacy_accepted)
    legacy_bytes = PhysicalOutputLifecycleTrace(events=legacy_events).to_jsonl_bytes()
    assert PhysicalOutputLifecycleTrace.from_jsonl(legacy_bytes).events == legacy_events

    lifecycle = PhysicalOutputLifecycle("session-v2")
    assert lifecycle.arm(PhysicalOutputPermission(mode="dry_run")).accepted
    request = _request(session_id="session-v2")
    accepted = _submit(
        lifecycle,
        request,
        now_s=request.timestamp_s,
        max_age_s=1.0,
    )
    assert accepted.event is not None
    assert accepted.event.safety_evidence is not None
    encoded = lifecycle.trace().to_jsonl_bytes()
    decoded = PhysicalOutputLifecycleTrace.from_jsonl(encoded)
    assert decoded.events == lifecycle.events
    assert decoded.to_jsonl_bytes() == encoded


def test_lifecycle_event_rejects_impossible_state_transition() -> None:
    with pytest.raises(ValueError, match="reconnect event"):
        PhysicalOutputLifecycleTrace(
            events=(
                # Direct construction exercises the typed transition guard.
                PhysicalOutputLifecycleEvent(
                    event_sequence=0,
                    event_kind="reconnect",
                    session_id="session-1",
                    state_before="disabled",
                    state_after="armed",
                ),
            )
        )

    lifecycle = PhysicalOutputLifecycle("session-1")
    lifecycle.arm(PhysicalOutputPermission(mode="dry_run"))
    lifecycle.source_invalid("invalid", timestamp_s=1.0)
    lifecycle.arm(PhysicalOutputPermission(mode="dry_run"), session_id="session-2")
    with pytest.raises(ValueError, match="session may change"):
        PhysicalOutputLifecycleTrace(
            events=(
                replace(lifecycle.events[0], session_id="session-2"),
                *lifecycle.events[1:],
            )
        )

    with pytest.raises(ValueError, match="cannot enter hold"):
        PhysicalOutputLifecycleEvent(
            event_sequence=0,
            event_kind="source_stale",
            session_id="session-1",
            state_before="disabled",
            state_after="hold",
            reason="source_stale",
        )

    with pytest.raises(ValueError, match="cannot enter stopping"):
        PhysicalOutputLifecycleEvent(
            event_sequence=0,
            event_kind="operator_stop",
            session_id="session-1",
            state_before="disabled",
            state_after="stopping",
            reason="operator_stop",
        )


def test_missing_freshness_context_enters_hold_without_acceptance() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    permission = PhysicalOutputPermission(mode="dry_run")
    lifecycle.arm(permission)

    result = _submit(lifecycle, _request(), now_s=1.0, max_safety_age_s=1.0)

    assert not result.accepted
    assert result.reason == "physical_output_freshness_context_missing"
    assert lifecycle.state == "hold"
    assert lifecycle.latest_request is None
    assert lifecycle.last_request_sequence == 4


def test_hold_rearm_requires_an_unused_session_id_for_lifetime() -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    permission = PhysicalOutputPermission(mode="dry_run")
    lifecycle.arm(permission)
    _submit(lifecycle, _request(), now_s=1.0, max_age_s=1.0)
    lifecycle.source_stale(timestamp_s=2.0)

    same_session = lifecycle.arm(permission, session_id="session-1")
    assert not same_session.accepted
    assert same_session.reason == "new_session_required_for_rearm"

    assert lifecycle.arm(permission, session_id="session-2").accepted
    lifecycle.source_invalid("invalid", timestamp_s=3.0)
    reused_session = lifecycle.arm(permission, session_id="session-1")
    assert not reused_session.accepted
    assert reused_session.reason == "session_id_reuse_forbidden"


@pytest.mark.parametrize("health_event", ("source_stale", "source_disconnected"))
def test_submit_and_source_health_are_serialized_without_trace_corruption(
    health_event: str,
) -> None:
    lifecycle = PhysicalOutputLifecycle("session-1")
    permission = PhysicalOutputPermission(mode="dry_run")
    request = _request()
    lifecycle.arm(permission)
    barrier = Barrier(2)

    def submit() -> object:
        barrier.wait()
        return _submit(lifecycle, request, now_s=1.0, max_age_s=1.0)

    def stale() -> object:
        barrier.wait()
        return getattr(lifecycle, health_event)(timestamp_s=1.0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda operation: operation(), (submit, stale)))

    assert len(results) == 2
    assert lifecycle.state == "hold"
    assert lifecycle.latest_request is None
    trace = lifecycle.trace()
    assert PhysicalOutputLifecycleTrace.from_jsonl(trace.to_jsonl_bytes()) == trace


def test_sink_event_failure_fails_closed_and_keeps_internal_trace_valid() -> None:
    class BrokenSink:
        def record_lifecycle_event(self, event: PhysicalOutputLifecycleEvent) -> None:
            raise RuntimeError("sink unavailable")

    lifecycle = PhysicalOutputLifecycle("session-1", sink=BrokenSink())
    with pytest.raises(RuntimeError, match="event recording failed"):
        lifecycle.arm(PhysicalOutputPermission(mode="dry_run"))

    assert lifecycle.state == "failed"
    assert lifecycle.latest_request is None
    assert lifecycle.events[-1].event_kind == "failure"
    assert "lifecycle_event_recording_failed:RuntimeError" == lifecycle.events[-1].reason
    assert PhysicalOutputLifecycleTrace.from_jsonl(lifecycle.trace().to_jsonl_bytes()) == lifecycle.trace()


def test_shutdown_uses_post_cleanup_monotonic_time_and_marks_overrun_failed() -> None:
    clock_values = iter((0.0, 2.0))
    lifecycle = PhysicalOutputLifecycle(
        "session-slow-cleanup",
        shutdown_timeout_s=1.0,
        clock=lambda: next(clock_values),
    )
    lifecycle.arm(PhysicalOutputPermission(mode="dry_run"))
    _submit(
        lifecycle,
        _request(session_id="session-slow-cleanup"),
        now_s=1.0,
        max_age_s=1.0,
    )

    result = lifecycle.shutdown(now_s=2.0, cleanup=lambda: None)

    assert not result.accepted
    assert result.reason == "bounded_shutdown_deadline_exceeded"
    assert lifecycle.state == "failed"


def test_stop_deadline_overflow_fails_closed() -> None:
    lifecycle = PhysicalOutputLifecycle(
        "session-overflow",
        shutdown_timeout_s=1.0e308,
    )
    lifecycle.arm(PhysicalOutputPermission(mode="dry_run"))

    result = lifecycle.operator_stop(now_s=1.0e308)

    assert not result.accepted
    assert result.reason == "bounded_shutdown_deadline_overflow"
    assert lifecycle.state == "failed"
    assert lifecycle.stop_deadline_s is None


def test_lifecycle_trace_rejects_same_session_rearm_from_stopped() -> None:
    events = (
        PhysicalOutputLifecycleEvent(
            event_sequence=0,
            event_kind="armed",
            session_id="session-1",
            state_before="disabled",
            state_after="armed",
        ),
        PhysicalOutputLifecycleEvent(
            event_sequence=1,
            event_kind="operator_stop",
            session_id="session-1",
            state_before="armed",
            state_after="stopping",
            reason="operator_stop",
        ),
        PhysicalOutputLifecycleEvent(
            event_sequence=2,
            event_kind="stop_completed",
            session_id="session-1",
            state_before="stopping",
            state_after="stopped",
        ),
        PhysicalOutputLifecycleEvent(
            event_sequence=3,
            event_kind="armed",
            session_id="session-1",
            state_before="stopped",
            state_after="armed",
        ),
    )

    with pytest.raises(ValueError, match="re-arm requires a new session"):
        PhysicalOutputLifecycleTrace(events=events)


def test_lifecycle_trace_rejects_disabled_request_to_hold() -> None:
    with pytest.raises(ValueError, match="request_rejected can enter hold"):
        PhysicalOutputLifecycleEvent(
            event_sequence=0,
            event_kind="request_rejected",
            session_id="session-1",
            state_before="disabled",
            state_after="hold",
            request_sequence=0,
            reason="physical_output_request_stale",
        )
