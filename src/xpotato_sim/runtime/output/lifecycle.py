"""Fail-closed physical-output lifecycle coordination.

This module owns only state, transition evidence, and bounded local shutdown
semantics.  It never sends a request, opens a transport, or replays a stale
command.  A caller may attach a recording-only sink through the tiny
``record_lifecycle_event`` protocol.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from math import isfinite
from numbers import Real
from threading import RLock
from time import monotonic
from typing import Literal, Protocol, TypeAlias

from xpotato_sim.runtime.output.safety_gate import (
    PhysicalOutputSafetyEvaluation,
    PhysicalOutputSafetyTraceEvidence,
    PhysicalOutputSendableRequest,
    validate_physical_output_safety_evaluation,
    validate_physical_output_sendable_request,
)
from xpotato_sim.schemas import PhysicalOutputPermission, PhysicalOutputRequest


PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION = "physical-output-lifecycle/v2"
_PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION_V1 = "physical-output-lifecycle/v1"
PhysicalOutputLifecycleState: TypeAlias = Literal[
    "disabled",
    "armed",
    "active",
    "hold",
    "stopping",
    "stopped",
    "aborted",
    "failed",
]
PhysicalOutputLifecycleEventKind: TypeAlias = Literal[
    "arm_rejected",
    "armed",
    "request_accepted",
    "request_claimed",
    "request_rejected",
    "safety_hold",
    "safety_rejected",
    "safety_stop",
    "safety_invalid",
    "source_stale",
    "source_disconnected",
    "source_invalid",
    "operator_stop",
    "runtime_shutdown",
    "stop_completed",
    "stop_deadline_exceeded",
    "abort",
    "failure",
    "cleanup_failure",
    "reconnect",
]

_LIFECYCLE_STATES = frozenset(
    {"disabled", "armed", "active", "hold", "stopping", "stopped", "aborted", "failed"}
)
_LIFECYCLE_EVENT_KINDS = frozenset(
    {
        "arm_rejected",
        "armed",
        "request_accepted",
        "request_claimed",
        "request_rejected",
        "safety_hold",
        "safety_rejected",
        "safety_stop",
        "safety_invalid",
        "source_stale",
        "source_disconnected",
        "source_invalid",
        "operator_stop",
        "runtime_shutdown",
        "stop_completed",
        "stop_deadline_exceeded",
        "abort",
        "failure",
        "cleanup_failure",
        "reconnect",
    }
)
_LIFECYCLE_EVENT_FIELDS_V1 = frozenset(
    {
        "event_kind",
        "event_sequence",
        "reason",
        "request_sequence",
        "schema_version",
        "session_id",
        "state_after",
        "state_before",
        "timestamp_s",
    }
)
_LIFECYCLE_EVENT_FIELDS_V2 = _LIFECYCLE_EVENT_FIELDS_V1 | {"safety_evidence"}
_LIFECYCLE_NO_SEQUENCE_REASONS = frozenset(
    {
        "session_mismatch",
        "duplicate_or_out_of_order_sequence",
        "lifecycle_state_not_accepting",
        "physical_safety_binding_required",
        "physical_safety_binding_invalid",
    }
)


class PhysicalOutputLifecycleSink(Protocol):
    """Minimal recording-only sink protocol used by the lifecycle owner."""

    def record_lifecycle_event(self, event: "PhysicalOutputLifecycleEvent") -> object: ...


def _lifecycle_locked(method: Callable[..., object]) -> Callable[..., object]:
    """Serialize public lifecycle access through one re-entrant reducer lock."""

    @wraps(method)
    def synchronized(
        self: "PhysicalOutputLifecycle",
        *args: object,
        **kwargs: object,
    ) -> object:
        with self._lock:
            return method(self, *args, **kwargs)

    return synchronized


def _lifecycle_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL")
    return value


def _lifecycle_sequence(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("lifecycle sequence must be a non-negative integer")
    return value


def _lifecycle_timestamp(name: str, value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be numeric or null")
    timestamp = float(value)
    if not isfinite(timestamp):
        raise ValueError(f"{name} must be finite or null")
    return timestamp


def _lifecycle_canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class PhysicalOutputLifecycleEvent:
    """`state_after`を正とし、transitionとP5 evidenceを保持するimmutable event。"""

    event_sequence: int
    event_kind: PhysicalOutputLifecycleEventKind
    session_id: str
    state_before: PhysicalOutputLifecycleState
    state_after: PhysicalOutputLifecycleState
    request_sequence: int | None = None
    timestamp_s: float | None = None
    reason: str | None = None
    schema_version: str = PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION
    safety_evidence: PhysicalOutputSafetyTraceEvidence | None = None

    def __post_init__(self) -> None:
        _lifecycle_sequence(self.event_sequence)
        _lifecycle_identifier("session_id", self.session_id)
        if self.schema_version not in {
            _PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION_V1,
            PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION,
        }:
            raise ValueError(
                "unsupported physical output lifecycle schema_version: "
                f"{self.schema_version!r}"
            )
        if self.event_kind not in _LIFECYCLE_EVENT_KINDS:
            raise ValueError(
                "physical output lifecycle event_kind must be one of "
                f"{sorted(_LIFECYCLE_EVENT_KINDS)!r}"
            )
        if self.state_before not in _LIFECYCLE_STATES:
            raise ValueError("physical output lifecycle state_before is unknown")
        if self.state_after not in _LIFECYCLE_STATES:
            raise ValueError("physical output lifecycle state_after is unknown")
        request_sequence = None
        if self.request_sequence is not None:
            request_sequence = _lifecycle_sequence(self.request_sequence)
        timestamp_s = _lifecycle_timestamp("timestamp_s", self.timestamp_s)
        reason = None if self.reason is None else _lifecycle_identifier("reason", self.reason)
        if self.event_kind in {
            "arm_rejected",
            "request_rejected",
            "request_claimed",
            "safety_hold",
            "safety_rejected",
            "safety_stop",
            "safety_invalid",
            "source_stale",
            "source_disconnected",
            "source_invalid",
            "operator_stop",
            "runtime_shutdown",
            "stop_deadline_exceeded",
            "abort",
            "failure",
            "cleanup_failure",
        } and reason is None:
            raise ValueError(f"{self.event_kind} lifecycle event requires a reason")
        if self.safety_evidence is not None:
            if not isinstance(self.safety_evidence, PhysicalOutputSafetyTraceEvidence):
                raise TypeError(
                    "safety_evidence must be PhysicalOutputSafetyTraceEvidence or None"
                )
            if self.schema_version != PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION:
                raise ValueError("legacy lifecycle event cannot carry safety evidence")
        if self.event_kind in {
            "safety_hold",
            "safety_rejected",
            "safety_stop",
            "safety_invalid",
        } and self.safety_evidence is None:
            raise ValueError(f"{self.event_kind} event requires safety evidence")
        if (
            self.event_kind == "request_accepted"
            and self.schema_version == PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION
            and self.safety_evidence is None
        ):
            raise ValueError("accepted v2 lifecycle event requires safety evidence")
        if self.event_kind == "request_claimed":
            if self.schema_version != PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION:
                raise ValueError("request_claimed requires lifecycle v2")
            if self.safety_evidence is None:
                raise ValueError("request_claimed event requires safety evidence")
        if (
            self.schema_version == _PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION_V1
            and self.event_kind in {
                "safety_hold",
                "safety_rejected",
                "safety_stop",
                "safety_invalid",
            }
        ):
            raise ValueError("legacy lifecycle event cannot carry a safety transition")
        if self.event_kind == "armed" and self.state_after != "armed":
            raise ValueError("armed event must enter armed state")
        if self.event_kind == "armed" and self.state_before not in {
            "disabled",
            "hold",
            "stopped",
            "aborted",
            "failed",
        }:
            raise ValueError("armed event must come from a non-running state")
        if self.event_kind == "arm_rejected" and self.state_after != self.state_before:
            raise ValueError("arm_rejected event cannot change lifecycle state")
        if self.event_kind == "request_accepted" and self.state_before not in {
            "armed",
            "active",
        }:
            raise ValueError("request_accepted event must come from armed or active state")
        if self.event_kind == "request_accepted" and self.state_after != "active":
            raise ValueError("request_accepted event must enter active state")
        if self.event_kind == "request_claimed" and (
            self.state_before != "active" or self.state_after != "active"
        ):
            raise ValueError("request_claimed event must preserve active state")
        if self.event_kind == "request_rejected" and self.state_after not in {
            self.state_before,
            "hold",
        }:
            raise ValueError("request_rejected event may only preserve state or enter hold")
        if self.event_kind == "safety_hold":
            if self.state_after not in {self.state_before, "hold"}:
                raise ValueError("safety_hold event may only preserve state or enter hold")
            if self.state_after == "hold" and self.state_before not in {
                "armed",
                "active",
                "hold",
            }:
                raise ValueError("safety_hold may enter hold only from an accepting state")
        if self.event_kind == "safety_rejected" and self.state_after != self.state_before:
            raise ValueError("safety_rejected event cannot change lifecycle state")
        if self.event_kind == "safety_stop":
            if self.state_after not in {self.state_before, "stopping"}:
                raise ValueError("safety_stop may only preserve state or enter stopping")
            if self.state_after == "stopping" and self.state_before not in {
                "armed",
                "active",
                "hold",
                "stopping",
            }:
                raise ValueError("safety_stop cannot enter stopping from this state")
        if self.event_kind == "safety_invalid":
            expected_state = (
                self.state_before
                if self.state_before in {"aborted", "failed"}
                else "aborted"
            )
            if self.state_after != expected_state:
                raise ValueError("safety_invalid must enter aborted or preserve a terminal state")
        if self.event_kind == "stop_completed":
            if self.state_before not in {"stopping", "stopped"}:
                raise ValueError("stop_completed event must come from stopping or stopped state")
            if self.state_after != "stopped":
                raise ValueError("stop_completed event must enter stopped state")
        if self.event_kind in {"source_stale", "source_disconnected"} and self.state_after not in {
            "hold",
            self.state_before,
        }:
            raise ValueError(f"{self.event_kind} event must enter or remain in hold")
        if self.event_kind in {"operator_stop", "runtime_shutdown"} and self.state_after not in {
            self.state_before,
            "stopping",
        }:
            raise ValueError(f"{self.event_kind} event must preserve state or enter stopping")
        if self.event_kind == "stop_deadline_exceeded" and (
            self.state_before != "stopping" or self.state_after != "failed"
        ):
            raise ValueError("stop_deadline_exceeded event must enter failed from stopping")
        if self.event_kind in {"source_invalid", "abort"} and self.state_after not in {
            "aborted",
            "failed",
        }:
            raise ValueError(f"{self.event_kind} event must enter a terminal state")
        if self.event_kind == "failure" and self.state_after not in {
            "failed",
            "aborted",
        }:
            raise ValueError("failure event must enter a terminal state")
        if self.event_kind == "cleanup_failure" and self.state_after not in {
            self.state_before,
            "failed",
        }:
            raise ValueError("cleanup_failure event must preserve state or enter failed")
        if self.event_kind == "reconnect" and self.state_after != self.state_before:
            raise ValueError("reconnect event cannot change lifecycle state")
        if self.event_kind in {"source_stale", "source_disconnected"}:
            if self.state_after == "hold" and self.state_before not in {
                "armed",
                "active",
                "hold",
            }:
                raise ValueError(
                    f"{self.event_kind} cannot enter hold from {self.state_before}"
                )
        if (
            self.event_kind == "request_rejected"
            and self.state_after == "hold"
            and self.state_before != "hold"
        ):
            if self.state_before not in {"armed", "active"}:
                raise ValueError(
                    "request_rejected can enter hold only from armed or active state"
                )
            if self.reason not in {
                "physical_output_freshness_context_missing",
                "physical_output_freshness_context_invalid",
                "physical_output_request_stale",
                "physical_output_timestamp_in_future",
            }:
                raise ValueError(
                    "request_rejected can enter hold only for a freshness failure"
                )
        if self.event_kind in {"operator_stop", "runtime_shutdown"}:
            enters_stopping = self.state_after == "stopping"
            if enters_stopping and self.state_before not in {
                "armed",
                "active",
                "hold",
                "stopping",
            }:
                raise ValueError(
                    f"{self.event_kind} cannot enter stopping from {self.state_before}"
                )
        if self.event_kind == "source_invalid":
            expected_state = (
                self.state_before
                if self.state_before in {"aborted", "failed"}
                else "aborted"
            )
            if self.state_after != expected_state:
                raise ValueError(
                    "source_invalid must enter aborted or preserve a terminal state"
                )
        if self.event_kind == "abort":
            expected_state = (
                self.state_before
                if self.state_before in {"aborted", "failed"}
                else "aborted"
            )
            if self.state_after != expected_state:
                raise ValueError(
                    "abort must enter aborted or preserve a terminal state"
                )
        if self.event_kind == "failure":
            expected_state = (
                self.state_before
                if self.state_before in {"aborted", "failed"}
                else "failed"
            )
            if self.state_after != expected_state:
                raise ValueError(
                    "failure must enter failed or preserve a terminal state"
                )
        if self.event_kind == "cleanup_failure":
            expected_state = (
                self.state_before
                if self.state_before in {"aborted", "failed"}
                else "failed"
            )
            if self.state_after != expected_state:
                raise ValueError(
                    "cleanup_failure must enter failed or preserve a terminal state"
                )
        object.__setattr__(self, "request_sequence", request_sequence)
        object.__setattr__(self, "timestamp_s", timestamp_s)
        object.__setattr__(self, "reason", reason)

    def to_json_value(self) -> dict[str, object]:
        result: dict[str, object] = {
            "event_kind": self.event_kind,
            "event_sequence": self.event_sequence,
            "reason": self.reason,
            "request_sequence": self.request_sequence,
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "state_after": self.state_after,
            "state_before": self.state_before,
            "timestamp_s": self.timestamp_s,
        }
        if self.schema_version == PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION:
            result["safety_evidence"] = (
                None
                if self.safety_evidence is None
                else self.safety_evidence.to_json_value()
            )
        return result

    def to_json_bytes(self) -> bytes:
        return _lifecycle_canonical_json_bytes(self.to_json_value())


def _lifecycle_parse_json_object(document: bytes | str) -> dict[str, object]:
    if isinstance(document, bytes):
        if document.startswith(b"\xef\xbb\xbf"):
            raise ValueError("physical output lifecycle trace must not contain a UTF-8 BOM")
        try:
            text = document.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("physical output lifecycle trace must be valid UTF-8") from exc
    elif isinstance(document, str):
        text = document
    else:
        raise TypeError("physical output lifecycle event must be UTF-8 bytes or text")

    def reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"duplicate field in physical output lifecycle trace: {key!r}"
                )
            result[key] = value
        return result

    def reject_non_finite_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON constant is forbidden: {value}")

    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate_fields,
            parse_constant=reject_non_finite_constant,
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("physical output lifecycle event is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("physical output lifecycle event must be a JSON object")
    return value


def _lifecycle_event_from_json(
    document: bytes | str | dict[str, object],
) -> PhysicalOutputLifecycleEvent:
    payload = (
        document
        if isinstance(document, dict)
        else _lifecycle_parse_json_object(document)
    )
    schema_version = payload.get("schema_version")
    if schema_version == _PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION_V1:
        expected_fields = _LIFECYCLE_EVENT_FIELDS_V1
    elif schema_version == PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION:
        expected_fields = _LIFECYCLE_EVENT_FIELDS_V2
    else:
        raise ValueError("unsupported physical output lifecycle schema_version")
    actual = frozenset(payload)
    unknown = sorted(actual - expected_fields)
    missing = sorted(expected_fields - actual)
    if unknown:
        raise ValueError(f"physical output lifecycle event has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"physical output lifecycle event is missing fields: {missing}")
    event_kind = payload["event_kind"]
    state_before = payload["state_before"]
    state_after = payload["state_after"]
    session_id = payload["session_id"]
    if not all(
        isinstance(value, str)
        for value in (event_kind, state_before, state_after, session_id, schema_version)
    ):
        raise ValueError("physical output lifecycle string fields have invalid types")
    request_sequence = payload["request_sequence"]
    if request_sequence is not None and type(request_sequence) is not int:
        raise ValueError("physical output lifecycle request_sequence must be integer or null")
    timestamp_s = payload["timestamp_s"]
    if timestamp_s is not None and (
        isinstance(timestamp_s, bool) or not isinstance(timestamp_s, Real)
    ):
        raise ValueError("physical output lifecycle timestamp_s must be numeric or null")
    reason = payload["reason"]
    if reason is not None and not isinstance(reason, str):
        raise ValueError("physical output lifecycle reason must be string or null")
    safety_evidence = None
    if schema_version == PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION:
        evidence_document = payload["safety_evidence"]
        if evidence_document is not None:
            safety_evidence = PhysicalOutputSafetyTraceEvidence.from_json_value(
                evidence_document
            )
    return PhysicalOutputLifecycleEvent(
        event_sequence=_lifecycle_sequence(payload["event_sequence"]),
        event_kind=event_kind,  # type: ignore[arg-type]
        session_id=session_id,
        state_before=state_before,  # type: ignore[arg-type]
        state_after=state_after,  # type: ignore[arg-type]
        request_sequence=request_sequence,
        timestamp_s=timestamp_s,
        reason=reason,
        schema_version=schema_version,
        safety_evidence=safety_evidence,
    )


def _validate_lifecycle_events(events: tuple[PhysicalOutputLifecycleEvent, ...]) -> None:
    expected_sequence = 0
    previous_state: PhysicalOutputLifecycleState = "disabled"
    session_id: str | None = None
    used_session_ids: set[str] = set()
    latest_request_sequence_by_session: dict[str, int] = {}
    seen_request_sequences_by_session: dict[str, set[int]] = {}
    latest_accepted_request_sequence_by_session: dict[str, int] = {}
    claimed_request_sequences_by_session: dict[str, set[int]] = {}
    for event in events:
        if event.event_sequence != expected_sequence:
            raise ValueError("physical output lifecycle event sequence must be contiguous from zero")
        expected_sequence += 1
        if event.state_before != previous_state:
            raise ValueError("physical output lifecycle event state chain is inconsistent")
        if session_id is None:
            session_id = event.session_id
            used_session_ids.add(session_id)
        elif event.session_id != session_id:
            if not (
                event.event_kind == "armed"
                and previous_state in {"hold", "stopped", "aborted", "failed"}
            ):
                raise ValueError(
                    "physical output lifecycle session may change only on re-arm"
                )
            if event.session_id in used_session_ids:
                raise ValueError("physical output lifecycle session id may not be reused")
            session_id = event.session_id
            used_session_ids.add(session_id)
        elif event.event_kind == "armed" and previous_state in {
            "hold",
            "stopped",
            "aborted",
            "failed",
        }:
            raise ValueError("physical output lifecycle re-arm requires a new session")
        previous_state = event.state_after
        request_event_kinds = {
            "request_accepted",
            "request_rejected",
            "safety_hold",
            "safety_rejected",
            "safety_stop",
            "safety_invalid",
        }
        if event.event_kind == "request_claimed":
            if event.request_sequence is None:
                raise ValueError("request_claimed requires request_sequence")
            latest_accepted = latest_accepted_request_sequence_by_session.get(
                event.session_id
            )
            if event.request_sequence != latest_accepted:
                raise ValueError("request_claimed must name the latest accepted request")
            claimed_sequences = claimed_request_sequences_by_session.setdefault(
                event.session_id,
                set(),
            )
            if event.request_sequence in claimed_sequences:
                raise ValueError("physical output request may be claimed only once")
            claimed_sequences.add(event.request_sequence)
        if event.event_kind in request_event_kinds:
            if (
                event.request_sequence is None
                and event.reason not in _LIFECYCLE_NO_SEQUENCE_REASONS
            ):
                raise ValueError(f"{event.event_kind} requires request_sequence")
            if event.request_sequence is None:
                continue
            latest_request_sequence = latest_request_sequence_by_session.get(event.session_id)
            seen_request_sequences = seen_request_sequences_by_session.setdefault(
                event.session_id,
                set(),
            )
            if (
                latest_request_sequence is not None
                and event.request_sequence < latest_request_sequence
            ):
                raise ValueError("physical output lifecycle request event is late or out of order")
            if event.request_sequence in seen_request_sequences:
                raise ValueError("duplicate physical output lifecycle request event")
            seen_request_sequences.add(event.request_sequence)
            latest_request_sequence_by_session[event.session_id] = event.request_sequence
            if event.event_kind == "request_accepted":
                latest_accepted_request_sequence_by_session[event.session_id] = (
                    event.request_sequence
                )


@dataclass(frozen=True, slots=True)
class PhysicalOutputLifecycleTrace:
    """lifecycle event v1のreadとv2のstrict deterministic JSONL evidence。"""

    events: tuple[PhysicalOutputLifecycleEvent, ...] = ()
    schema_version: str = PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported physical output lifecycle trace schema_version: "
                f"{self.schema_version!r}"
            )
        events = tuple(self.events)
        if any(not isinstance(event, PhysicalOutputLifecycleEvent) for event in events):
            raise TypeError("physical output lifecycle trace events must be typed events")
        object.__setattr__(self, "events", events)
        _validate_lifecycle_events(events)

    def to_jsonl_bytes(self) -> bytes:
        if not self.events:
            return b""
        return b"\n".join(event.to_json_bytes() for event in self.events) + b"\n"

    @classmethod
    def from_jsonl(cls, document: bytes | str) -> "PhysicalOutputLifecycleTrace":
        if isinstance(document, str):
            document_bytes = document.encode("utf-8")
        elif isinstance(document, bytes):
            document_bytes = document
        else:
            raise TypeError("physical output lifecycle trace must be UTF-8 bytes or text")
        if document_bytes.startswith(b"\xef\xbb\xbf"):
            raise ValueError("physical output lifecycle trace must not contain a UTF-8 BOM")
        if not document_bytes:
            return cls()
        if not document_bytes.endswith(b"\n"):
            raise ValueError("physical output lifecycle trace must end with a newline")
        lines = document_bytes[:-1].split(b"\n")
        if any(not line for line in lines):
            raise ValueError("physical output lifecycle trace must not contain blank lines")
        trace = cls(events=tuple(_lifecycle_event_from_json(line) for line in lines))
        if trace.to_jsonl_bytes() != document_bytes:
            raise ValueError("physical output lifecycle trace is not canonical JSONL")
        return trace


@dataclass(frozen=True, slots=True)
class PhysicalOutputLifecycleResult:
    """request / transition operationの結果。"""

    accepted: bool
    state: PhysicalOutputLifecycleState
    reason: str | None = None
    event: PhysicalOutputLifecycleEvent | None = None
    sendable_request: PhysicalOutputSendableRequest | None = None


@dataclass(frozen=True, slots=True)
class PhysicalOutputLifecycleDispatchResult:
    """latest requestのone-shot claim / guarded dispatch結果。"""

    claimed: bool
    state: PhysicalOutputLifecycleState
    reason: str | None = None
    event: PhysicalOutputLifecycleEvent | None = None
    operation_invoked: bool = False
    operation_result: object | None = None
    operation_error_type: str | None = None


class PhysicalOutputLifecycle:
    """P5 safety binding、freshness、lifecycle state、bounded stopを守るpure local state machine。"""

    def __init__(
        self,
        session_id: str,
        *,
        shutdown_timeout_s: float = 1.0,
        sink: PhysicalOutputLifecycleSink | None = None,
        clock: Callable[[], Real] | None = None,
    ) -> None:
        self._lock = RLock()
        self._session_id = _lifecycle_identifier("session_id", session_id)
        if isinstance(shutdown_timeout_s, bool) or not isinstance(shutdown_timeout_s, Real):
            raise TypeError("shutdown_timeout_s must be numeric")
        self._shutdown_timeout_s = float(shutdown_timeout_s)
        if not isfinite(self._shutdown_timeout_s) or self._shutdown_timeout_s <= 0.0:
            raise ValueError("shutdown_timeout_s must be finite and positive")
        if sink is not None and not callable(getattr(sink, "record_lifecycle_event", None)):
            raise TypeError("lifecycle sink must implement record_lifecycle_event")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._sink = sink
        self._clock = monotonic if clock is None else clock
        self._used_session_ids: set[str] = {self._session_id}
        self._state: PhysicalOutputLifecycleState = "disabled"
        self._permission: PhysicalOutputPermission | None = None
        self._latest_request: PhysicalOutputRequest | None = None
        self._latest_sendable_request: PhysicalOutputSendableRequest | None = None
        self._last_request_sequence: int | None = None
        self._last_claimed_sequence: int | None = None
        self._last_claimed_request_timestamp_s: float | None = None
        self._last_claimed_at_s: float | None = None
        self._last_claimed_cadence_s: float | None = None
        self._event_sequence = 0
        self._events: list[PhysicalOutputLifecycleEvent] = []
        self._stop_deadline_s: float | None = None
        self._stop_started_s: float | None = None

    @property
    @_lifecycle_locked
    def session_id(self) -> str:
        return self._session_id

    @property
    @_lifecycle_locked
    def state(self) -> PhysicalOutputLifecycleState:
        return self._state

    @property
    @_lifecycle_locked
    def permission(self) -> PhysicalOutputPermission | None:
        return self._permission

    @property
    @_lifecycle_locked
    def latest_request(self) -> PhysicalOutputRequest | None:
        return self._latest_request

    @property
    @_lifecycle_locked
    def latest_sendable_request(self) -> PhysicalOutputSendableRequest | None:
        return self._latest_sendable_request

    @property
    @_lifecycle_locked
    def last_request_sequence(self) -> int | None:
        return self._last_request_sequence

    @property
    @_lifecycle_locked
    def stop_deadline_s(self) -> float | None:
        return self._stop_deadline_s

    @property
    @_lifecycle_locked
    def stop_started_s(self) -> float | None:
        return self._stop_started_s

    @property
    @_lifecycle_locked
    def events(self) -> tuple[PhysicalOutputLifecycleEvent, ...]:
        return tuple(self._events)

    @_lifecycle_locked
    def trace(self) -> PhysicalOutputLifecycleTrace:
        return PhysicalOutputLifecycleTrace(events=tuple(self._events))

    def _record(
        self,
        event_kind: PhysicalOutputLifecycleEventKind,
        *,
        state_before: PhysicalOutputLifecycleState,
        state_after: PhysicalOutputLifecycleState,
        request_sequence: int | None = None,
        timestamp_s: float | None = None,
        reason: str | None = None,
        safety_evidence: PhysicalOutputSafetyTraceEvidence | None = None,
    ) -> PhysicalOutputLifecycleEvent:
        # Normalize before constructing evidence; public transitions also do
        # this before changing state so invalid timestamps cannot partially
        # commit a transition.
        timestamp_s = _lifecycle_timestamp("timestamp_s", timestamp_s)
        event = PhysicalOutputLifecycleEvent(
            event_sequence=self._event_sequence,
            event_kind=event_kind,
            session_id=self._session_id,
            state_before=state_before,
            state_after=state_after,
            request_sequence=request_sequence,
            timestamp_s=timestamp_s,
            reason=reason,
            safety_evidence=safety_evidence,
        )
        self._event_sequence += 1
        self._events.append(event)
        if self._sink is not None:
            try:
                self._sink.record_lifecycle_event(event)
            except Exception as exc:
                failure_state: PhysicalOutputLifecycleState = (
                    "aborted" if event.state_after == "aborted" else "failed"
                )
                self._state = failure_state
                self._clear_latest_request()
                self._latest_sendable_request = None
                self._stop_deadline_s = None
                self._stop_started_s = None
                failure_event = PhysicalOutputLifecycleEvent(
                    event_sequence=self._event_sequence,
                    event_kind="failure",
                    session_id=self._session_id,
                    state_before=event.state_after,
                    state_after=failure_state,
                    timestamp_s=timestamp_s,
                    reason=f"lifecycle_event_recording_failed:{type(exc).__name__}",
                )
                self._event_sequence += 1
                self._events.append(failure_event)
                raise RuntimeError(
                    "physical output lifecycle event recording failed"
                ) from exc
        return event

    def _result(
        self,
        accepted: bool,
        reason: str | None = None,
        event: PhysicalOutputLifecycleEvent | None = None,
        sendable_request: PhysicalOutputSendableRequest | None = None,
    ) -> PhysicalOutputLifecycleResult:
        return PhysicalOutputLifecycleResult(
            accepted=accepted,
            state=self._state,
            reason=reason,
            event=event,
            sendable_request=sendable_request,
        )

    def _clock_now(self) -> float:
        return _required_timestamp("clock", self._clock())

    @_lifecycle_locked
    def arm(
        self,
        permission: PhysicalOutputPermission,
        *,
        session_id: str | None = None,
        timestamp_s: float | None = None,
    ) -> PhysicalOutputLifecycleResult:
        """Explicitly arm; reconnect alone never invokes this transition."""

        timestamp_s = _lifecycle_timestamp("timestamp_s", timestamp_s)
        if not isinstance(permission, PhysicalOutputPermission):
            raise TypeError("lifecycle arm requires PhysicalOutputPermission")
        if permission.mode == "disabled":
            event = self._record(
                "arm_rejected",
                state_before=self._state,
                state_after=self._state,
                timestamp_s=timestamp_s,
                reason="physical_output_disabled",
            )
            return self._result(False, "physical_output_disabled", event)
        if self._state in {"armed", "active", "stopping"}:
            event = self._record(
                "arm_rejected",
                state_before=self._state,
                state_after=self._state,
                timestamp_s=timestamp_s,
                reason="lifecycle_already_running",
            )
            return self._result(False, "lifecycle_already_running", event)
        if self._state in {"hold", "stopped", "aborted", "failed"}:
            if session_id is None:
                reason = "new_session_required_for_rearm"
                event = self._record(
                    "arm_rejected",
                    state_before=self._state,
                    state_after=self._state,
                    timestamp_s=timestamp_s,
                    reason=reason,
                )
                return self._result(False, reason, event)
            resolved_session_id = _lifecycle_identifier("session_id", session_id)
            if resolved_session_id == self._session_id:
                reason = "new_session_required_for_rearm"
                event = self._record(
                    "arm_rejected",
                    state_before=self._state,
                    state_after=self._state,
                    timestamp_s=timestamp_s,
                    reason=reason,
                )
                return self._result(False, reason, event)
            if resolved_session_id in self._used_session_ids:
                reason = "session_id_reuse_forbidden"
                event = self._record(
                    "arm_rejected",
                    state_before=self._state,
                    state_after=self._state,
                    timestamp_s=timestamp_s,
                    reason=reason,
                )
                return self._result(False, reason, event)
            self._session_id = resolved_session_id
            self._used_session_ids.add(resolved_session_id)
            self._last_request_sequence = None
            self._last_claimed_sequence = None
            self._last_claimed_request_timestamp_s = None
            self._last_claimed_at_s = None
            self._last_claimed_cadence_s = None
        elif session_id is not None and _lifecycle_identifier("session_id", session_id) != self._session_id:
            reason = "session_mismatch"
            event = self._record(
                "arm_rejected",
                state_before=self._state,
                state_after=self._state,
                timestamp_s=timestamp_s,
                reason=reason,
            )
            return self._result(False, reason, event)

        before = self._state
        self._permission = permission
        self._clear_latest_request()
        self._latest_sendable_request = None
        self._stop_deadline_s = None
        self._stop_started_s = None
        self._state = "armed"
        event = self._record(
            "armed",
            state_before=before,
            state_after=self._state,
            timestamp_s=timestamp_s,
        )
        return self._result(True, event=event)

    @_lifecycle_locked
    def reconnect(self, *, timestamp_s: float | None = None) -> PhysicalOutputLifecycleEvent:
        """Record reconnect only; it never changes state or accepts output."""

        timestamp_s = _lifecycle_timestamp("timestamp_s", timestamp_s)
        return self._record(
            "reconnect",
            state_before=self._state,
            state_after=self._state,
            timestamp_s=timestamp_s,
            reason=None,
        )

    @_lifecycle_locked
    def submit(
        self,
        request: (
            PhysicalOutputRequest
            | PhysicalOutputSafetyEvaluation
            | PhysicalOutputSendableRequest
        ),
        *,
        now_s: float | None = None,
        max_age_s: float | None = None,
        max_safety_age_s: float | None = None,
    ) -> PhysicalOutputLifecycleResult:
        """Safety allowとfreshnessを確認したtyped requestだけを受理する。"""

        try:
            now_s = _lifecycle_timestamp("now_s", now_s)
        except (TypeError, ValueError):
            if isinstance(
                request,
                (
                    PhysicalOutputRequest,
                    PhysicalOutputSafetyEvaluation,
                    PhysicalOutputSendableRequest,
                ),
            ):
                self._clear_latest_request()
            raise

        if isinstance(request, PhysicalOutputRequest):
            self._clear_latest_request()
            return self._reject_request(
                request,
                "physical_safety_binding_required",
                timestamp_s=now_s,
                record_sequence=False,
            )

        if isinstance(request, PhysicalOutputSendableRequest):
            try:
                validate_physical_output_sendable_request(request)
            except Exception:
                raw_request = request.request
                self._clear_latest_request()
                return self._reject_request(
                    raw_request,
                    "physical_safety_binding_invalid",
                    timestamp_s=now_s,
                    record_sequence=False,
                )
            evaluation = request.evaluation
        elif isinstance(request, PhysicalOutputSafetyEvaluation):
            evaluation = request
            try:
                validate_physical_output_safety_evaluation(evaluation)
            except Exception:
                raw_request = evaluation.request
                self._clear_latest_request()
                return self._reject_request(
                    raw_request,
                    "physical_safety_binding_invalid",
                    timestamp_s=now_s,
                    record_sequence=False,
                )
        else:
            raise TypeError(
                "lifecycle submit requires a typed physical output safety evaluation"
            )

        raw_request = evaluation.request
        safety_evidence = evaluation.to_trace_evidence()
        if evaluation.status != "allowed":
            request_sequence, sequence_reason = self._consume_safety_sequence(raw_request)
            event_reason = sequence_reason or evaluation.reason
            self._clear_latest_request()
            if evaluation.status == "stopped":
                return self._apply_safety_stop(
                    evaluation,
                    event_reason,
                    request_sequence=request_sequence,
                    now_s=now_s if now_s is not None else self._clock_now(),
                )
            if evaluation.status in {"held", "unavailable"}:
                return self._apply_safety_hold(
                    evaluation,
                    event_reason,
                    request_sequence=request_sequence,
                    timestamp_s=now_s,
                )
            if evaluation.status == "invalid":
                return self._apply_safety_invalid(
                    evaluation,
                    event_reason,
                    request_sequence=request_sequence,
                    timestamp_s=now_s,
                )
            return self._apply_safety_rejected(
                evaluation,
                event_reason,
                request_sequence=request_sequence,
                timestamp_s=now_s,
            )

        if raw_request.session_id != self._session_id:
            self._clear_latest_request()
            return self._apply_safety_rejected(
                evaluation,
                "session_mismatch",
                request_sequence=None,
                timestamp_s=now_s,
            )
        if (
            self._last_request_sequence is not None
            and raw_request.sequence <= self._last_request_sequence
        ):
            return self._apply_safety_rejected(
                evaluation,
                "duplicate_or_out_of_order_sequence",
                request_sequence=None,
                timestamp_s=now_s,
            )
        if self._state not in {"armed", "active"}:
            return self._apply_safety_rejected(
                evaluation,
                "lifecycle_state_not_accepting",
                request_sequence=None,
                timestamp_s=now_s,
            )

        request_sequence, _ = self._consume_safety_sequence(raw_request)
        safety_freshness_reason = _safety_freshness_reason(
            evaluation,
            now_s=now_s,
            max_age_s=max_safety_age_s,
        )
        if safety_freshness_reason is not None:
            self._clear_latest_request()
            return self._apply_safety_hold(
                evaluation,
                safety_freshness_reason,
                request_sequence=request_sequence,
                timestamp_s=now_s,
            )

        try:
            request_freshness_reason = _request_freshness_reason(
                raw_request,
                now_s=now_s,
                max_age_s=max_age_s,
            )
        except (TypeError, ValueError):
            request_freshness_reason = "physical_output_freshness_context_invalid"
        if request_freshness_reason is not None:
            self._clear_latest_request()
            return self._apply_safety_hold(
                evaluation,
                request_freshness_reason,
                request_sequence=request_sequence,
                timestamp_s=now_s,
            )

        sendable_request = evaluation.to_sendable_request()
        before = self._state
        self._latest_request = sendable_request.request
        self._latest_sendable_request = sendable_request
        self._state = "active"
        event = self._record(
            "request_accepted",
            state_before=before,
            state_after=self._state,
            request_sequence=raw_request.sequence,
            timestamp_s=now_s,
            safety_evidence=safety_evidence,
        )
        return self._result(
            True,
            event=event,
            sendable_request=sendable_request,
        )

    @_lifecycle_locked
    def claim_latest_sendable_request(
        self,
        request: PhysicalOutputSendableRequest,
        *,
        expected_permission: PhysicalOutputPermission,
        expected_request_sha256: str,
        expected_binding_sha256: str,
        now_s: float,
        max_age_s: float,
        max_safety_age_s: float,
        minimum_cadence_s: float = 0.0,
    ) -> PhysicalOutputLifecycleDispatchResult:
        """リデューサーのロック内で、要求とバインディングを検証して一度だけ消費する。"""

        return self._claim_latest_sendable_locked(
            request,
            expected_permission=expected_permission,
            expected_request_sha256=expected_request_sha256,
            expected_binding_sha256=expected_binding_sha256,
            now_s=now_s,
            max_age_s=max_age_s,
            max_safety_age_s=max_safety_age_s,
            minimum_cadence_s=minimum_cadence_s,
        )

    @_lifecycle_locked
    def guarded_dispatch_latest_sendable_request(
        self,
        request: PhysicalOutputSendableRequest,
        *,
        expected_permission: PhysicalOutputPermission,
        expected_request_sha256: str,
        expected_binding_sha256: str,
        now_s: float | None = None,
        max_age_s: float,
        max_safety_age_s: float,
        minimum_cadence_s: float,
        operation: Callable[[PhysicalOutputSendableRequest], object],
    ) -> PhysicalOutputLifecycleDispatchResult:
        """同じlock内で時刻とbindingを検査して一度だけbounded operationを実行する。"""

        if not callable(operation):
            raise TypeError("guarded dispatch operation must be callable")
        dispatch_now = self._clock_now() if now_s is None else _required_timestamp(
            "now_s", now_s
        )
        claimed = self._claim_latest_sendable_locked(
            request,
            expected_permission=expected_permission,
            expected_request_sha256=expected_request_sha256,
            expected_binding_sha256=expected_binding_sha256,
            now_s=dispatch_now,
            max_age_s=max_age_s,
            max_safety_age_s=max_safety_age_s,
            minimum_cadence_s=minimum_cadence_s,
        )
        if not claimed.claimed:
            return claimed
        try:
            operation_result = operation(request)
        except Exception as exc:
            failure = self._terminal_transition(
                "failure",
                "failed",
                "physical_output_guarded_dispatch_failed",
                dispatch_now,
            )
            return PhysicalOutputLifecycleDispatchResult(
                claimed=True,
                state=failure.state,
                reason="physical_output_guarded_dispatch_failed",
                event=failure.event,
                operation_invoked=True,
                operation_error_type=type(exc).__name__,
            )
        return PhysicalOutputLifecycleDispatchResult(
            claimed=True,
            state=self._state,
            event=claimed.event,
            operation_invoked=True,
            operation_result=operation_result,
        )

    def _claim_latest_sendable_locked(
        self,
        request: PhysicalOutputSendableRequest,
        *,
        expected_permission: PhysicalOutputPermission,
        expected_request_sha256: str,
        expected_binding_sha256: str,
        now_s: float,
        max_age_s: float,
        max_safety_age_s: float,
        minimum_cadence_s: float,
    ) -> PhysicalOutputLifecycleDispatchResult:
        now = _required_timestamp("now_s", now_s)
        if not isinstance(expected_permission, PhysicalOutputPermission):
            raise TypeError("expected_permission must be PhysicalOutputPermission")
        if not isinstance(request, PhysicalOutputSendableRequest):
            raise TypeError("request must be PhysicalOutputSendableRequest")
        if not isinstance(expected_request_sha256, str) or not isinstance(
            expected_binding_sha256, str
        ):
            raise TypeError("expected request and binding digests must be strings")
        minimum_cadence = _lifecycle_timestamp(
            "minimum_cadence_s",
            minimum_cadence_s,
        )
        if minimum_cadence is None or minimum_cadence < 0.0:
            raise ValueError("minimum_cadence_s must be finite and non-negative")
        if self._state != "active":
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=self._state,
                reason="lifecycle_state_not_active",
            )
        if (
            self._latest_sendable_request is not request
            or self._latest_request is not request.request
        ):
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=self._state,
                reason="physical_output_sendable_request_not_latest",
            )
        if self._permission != expected_permission:
            failure = self._terminal_transition(
                "failure",
                "failed",
                "physical_output_permission_binding_mismatch",
                now,
            )
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=failure.state,
                reason=failure.reason,
                event=failure.event,
            )
        try:
            validate_physical_output_sendable_request(request)
        except Exception:
            failure = self._terminal_transition(
                "failure",
                "failed",
                "physical_output_safety_binding_invalid",
                now,
            )
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=failure.state,
                reason=failure.reason,
                event=failure.event,
            )
        if (
            request.request_sha256 != expected_request_sha256
            or request.binding_sha256 != expected_binding_sha256
        ):
            failure = self._terminal_transition(
                "failure",
                "failed",
                "physical_output_binding_identity_mismatch",
                now,
            )
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=failure.state,
                reason=failure.reason,
                event=failure.event,
            )

        evaluation = request.evaluation
        freshness_reason = _request_freshness_reason(
            request.request,
            now_s=now,
            max_age_s=max_age_s,
        ) or _safety_freshness_reason(
            evaluation,
            now_s=now,
            max_age_s=max_safety_age_s,
        )
        if freshness_reason is not None:
            event = self._enter_hold(
                "source_stale",
                freshness_reason,
                timestamp_s=now,
            ).event
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=self._state,
                reason=freshness_reason,
                event=event,
            )

        request_timestamp = request.request.timestamp_s
        if (
            self._last_claimed_sequence is not None
            and request.request.sequence <= self._last_claimed_sequence
        ):
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=self._state,
                reason="physical_output_sequence_out_of_order",
            )
        if (
            self._last_claimed_request_timestamp_s is not None
            and request_timestamp <= self._last_claimed_request_timestamp_s
        ):
            event = self._enter_hold(
                "source_stale",
                "physical_output_timestamp_out_of_order",
                timestamp_s=now,
            ).event
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=self._state,
                reason="physical_output_timestamp_out_of_order",
                event=event,
            )
        if self._last_claimed_at_s is not None:
            minimum_interval = max(
                minimum_cadence,
                request.request.cadence_s,
                self._last_claimed_cadence_s or 0.0,
            )
            if now - self._last_claimed_at_s < minimum_interval:
                return PhysicalOutputLifecycleDispatchResult(
                    claimed=False,
                    state=self._state,
                    reason="physical_output_cadence_not_elapsed",
                )

        self._clear_latest_request()
        self._last_claimed_sequence = request.request.sequence
        self._last_claimed_request_timestamp_s = request_timestamp
        self._last_claimed_at_s = now
        self._last_claimed_cadence_s = request.request.cadence_s
        event = self._record(
            "request_claimed",
            state_before=self._state,
            state_after=self._state,
            request_sequence=request.request.sequence,
            timestamp_s=now,
            reason="physical_output_request_claimed",
            safety_evidence=evaluation.to_trace_evidence(),
        )
        return PhysicalOutputLifecycleDispatchResult(
            claimed=True,
            state=self._state,
            reason="physical_output_request_claimed",
            event=event,
        )

    def _clear_latest_request(self) -> None:
        self._latest_request = None
        self._latest_sendable_request = None

    def _consume_safety_sequence(
        self,
        request: PhysicalOutputRequest,
    ) -> tuple[int | None, str | None]:
        if request.session_id != self._session_id:
            return None, "session_mismatch"
        if (
            self._last_request_sequence is not None
            and request.sequence <= self._last_request_sequence
        ):
            return None, "duplicate_or_out_of_order_sequence"
        self._last_request_sequence = request.sequence
        return request.sequence, None

    def _apply_safety_hold(
        self,
        evaluation: PhysicalOutputSafetyEvaluation,
        reason: str,
        *,
        request_sequence: int | None,
        timestamp_s: float | None,
    ) -> PhysicalOutputLifecycleResult:
        before = self._state
        if self._state in {"armed", "active"}:
            self._state = "hold"
        self._clear_latest_request()
        event = self._record(
            "safety_hold",
            state_before=before,
            state_after=self._state,
            request_sequence=request_sequence,
            timestamp_s=timestamp_s,
            reason=reason,
            safety_evidence=evaluation.to_trace_evidence(),
        )
        return self._result(False, reason, event)

    def _apply_safety_rejected(
        self,
        evaluation: PhysicalOutputSafetyEvaluation,
        reason: str,
        *,
        request_sequence: int | None,
        timestamp_s: float | None,
    ) -> PhysicalOutputLifecycleResult:
        event = self._record(
            "safety_rejected",
            state_before=self._state,
            state_after=self._state,
            request_sequence=request_sequence,
            timestamp_s=timestamp_s,
            reason=reason,
            safety_evidence=evaluation.to_trace_evidence(),
        )
        return self._result(False, reason, event)

    def _apply_safety_invalid(
        self,
        evaluation: PhysicalOutputSafetyEvaluation,
        reason: str,
        *,
        request_sequence: int | None,
        timestamp_s: float | None,
    ) -> PhysicalOutputLifecycleResult:
        before = self._state
        self._state = before if before in {"aborted", "failed"} else "aborted"
        self._clear_latest_request()
        self._stop_deadline_s = None
        self._stop_started_s = None
        event = self._record(
            "safety_invalid",
            state_before=before,
            state_after=self._state,
            request_sequence=request_sequence,
            timestamp_s=timestamp_s,
            reason=reason,
            safety_evidence=evaluation.to_trace_evidence(),
        )
        return self._result(False, reason, event)

    def _apply_safety_stop(
        self,
        evaluation: PhysicalOutputSafetyEvaluation,
        reason: str,
        *,
        request_sequence: int | None,
        now_s: float,
    ) -> PhysicalOutputLifecycleResult:
        return self._request_stop(
            "safety_stop",
            reason,
            now_s=now_s,
            request_sequence=request_sequence,
            safety_evidence=evaluation.to_trace_evidence(),
        )

    def _reject_request(
        self,
        request: PhysicalOutputRequest,
        reason: str,
        *,
        timestamp_s: float | None,
        record_sequence: bool = True,
    ) -> PhysicalOutputLifecycleResult:
        event = self._record(
            "request_rejected",
            state_before=self._state,
            state_after=self._state,
            request_sequence=request.sequence if record_sequence else None,
            timestamp_s=timestamp_s,
            reason=reason,
        )
        return self._result(False, reason, event)

    @_lifecycle_locked
    def source_stale(
        self,
        reason: str = "source_stale",
        *,
        timestamp_s: float | None = None,
    ) -> PhysicalOutputLifecycleResult:
        return self._enter_hold("source_stale", reason, timestamp_s=timestamp_s)

    @_lifecycle_locked
    def source_disconnected(
        self,
        reason: str = "source_disconnected",
        *,
        timestamp_s: float | None = None,
    ) -> PhysicalOutputLifecycleResult:
        return self._enter_hold("source_disconnected", reason, timestamp_s=timestamp_s)

    def _enter_hold(
        self,
        event_kind: Literal["source_stale", "source_disconnected"],
        reason: str,
        *,
        timestamp_s: float | None,
    ) -> PhysicalOutputLifecycleResult:
        timestamp_s = _lifecycle_timestamp("timestamp_s", timestamp_s)
        reason = _lifecycle_identifier("reason", reason)
        before = self._state
        if self._state in {"active", "armed"}:
            self._state = "hold"
            self._clear_latest_request()
        event = self._record(
            event_kind,
            state_before=before,
            state_after=self._state,
            timestamp_s=timestamp_s,
            reason=reason,
        )
        return self._result(True, event=event)

    @_lifecycle_locked
    def source_invalid(
        self,
        reason: str = "source_invalid",
        *,
        timestamp_s: float | None = None,
    ) -> PhysicalOutputLifecycleResult:
        return self._terminal_transition("source_invalid", "aborted", reason, timestamp_s)

    @_lifecycle_locked
    def operator_stop(
        self,
        reason: str = "operator_stop",
        *,
        now_s: float,
    ) -> PhysicalOutputLifecycleResult:
        return self._request_stop("operator_stop", reason, now_s=now_s)

    @_lifecycle_locked
    def runtime_shutdown(
        self,
        reason: str = "runtime_shutdown",
        *,
        now_s: float,
    ) -> PhysicalOutputLifecycleResult:
        return self._request_stop("runtime_shutdown", reason, now_s=now_s)

    def _request_stop(
        self,
        event_kind: Literal["operator_stop", "runtime_shutdown", "safety_stop"],
        reason: str,
        *,
        now_s: float,
        request_sequence: int | None = None,
        safety_evidence: PhysicalOutputSafetyTraceEvidence | None = None,
    ) -> PhysicalOutputLifecycleResult:
        now = _required_timestamp("now_s", now_s)
        reason = _lifecycle_identifier("reason", reason)
        if event_kind == "safety_stop":
            self._clear_latest_request()
        if self._state == "disabled":
            event = self._record(
                event_kind,
                state_before=self._state,
                state_after=self._state,
                request_sequence=request_sequence,
                timestamp_s=now,
                reason="stop_idempotent",
                safety_evidence=safety_evidence,
            )
            return self._result(True, event=event)
        if self._state in {"stopping", "stopped"}:
            event = self._record(
                event_kind,
                state_before=self._state,
                state_after=self._state,
                request_sequence=request_sequence,
                timestamp_s=now,
                reason="stop_idempotent",
                safety_evidence=safety_evidence,
            )
            return self._result(True, event=event)
        if self._state in {"aborted", "failed"}:
            event = self._record(
                event_kind,
                state_before=self._state,
                state_after=self._state,
                request_sequence=request_sequence,
                timestamp_s=now,
                reason="terminal_state_preserved",
                safety_evidence=safety_evidence,
            )
            return self._result(True, event=event)
        deadline = now + self._shutdown_timeout_s
        if not isfinite(deadline):
            before = self._state
            self._state = "failed"
            self._clear_latest_request()
            self._stop_deadline_s = None
            self._stop_started_s = None
            event = self._record(
                "failure",
                state_before=before,
                state_after=self._state,
                request_sequence=request_sequence,
                timestamp_s=now,
                reason="bounded_shutdown_deadline_overflow",
                safety_evidence=safety_evidence,
            )
            return self._result(False, "bounded_shutdown_deadline_overflow", event)
        before = self._state
        self._state = "stopping"
        self._clear_latest_request()
        self._stop_deadline_s = deadline
        self._stop_started_s = now
        event = self._record(
            event_kind,
            state_before=before,
            state_after=self._state,
            request_sequence=request_sequence,
            timestamp_s=now,
            reason=reason,
            safety_evidence=safety_evidence,
        )
        return self._result(True, event=event)

    @_lifecycle_locked
    def complete_stop(
        self,
        *,
        now_s: float,
    ) -> PhysicalOutputLifecycleResult:
        now = _required_timestamp("now_s", now_s)
        if self._state == "stopped":
            event = self._record(
                "stop_completed",
                state_before=self._state,
                state_after=self._state,
                timestamp_s=now,
                reason="stop_idempotent",
            )
            return self._result(True, event=event)
        if self._state != "stopping":
            return self._result(False, "stop_not_pending")
        if self._stop_started_s is None:
            return self._result(False, "stop_start_timestamp_missing")
        if now < self._stop_started_s:
            return self._result(False, "stop_completion_before_start")
        if self._stop_deadline_s is not None and now > self._stop_deadline_s:
            before = self._state
            self._state = "failed"
            self._stop_deadline_s = None
            self._stop_started_s = None
            event = self._record(
                "stop_deadline_exceeded",
                state_before=before,
                state_after=self._state,
                timestamp_s=now,
                reason="bounded_shutdown_deadline_exceeded",
            )
            return self._result(False, "bounded_shutdown_deadline_exceeded", event)
        before = self._state
        self._state = "stopped"
        self._clear_latest_request()
        self._stop_deadline_s = None
        self._stop_started_s = None
        event = self._record(
            "stop_completed",
            state_before=before,
            state_after=self._state,
            timestamp_s=now,
        )
        return self._result(True, event=event)

    @_lifecycle_locked
    def abort(
        self,
        reason: str = "operator_abort",
        *,
        timestamp_s: float | None = None,
    ) -> PhysicalOutputLifecycleResult:
        return self._terminal_transition("abort", "aborted", reason, timestamp_s)

    @_lifecycle_locked
    def fail(
        self,
        reason: str = "output_failure",
        *,
        timestamp_s: float | None = None,
    ) -> PhysicalOutputLifecycleResult:
        return self._terminal_transition("failure", "failed", reason, timestamp_s)

    @_lifecycle_locked
    def record_cleanup_failure(
        self,
        reason: str = "cleanup_failure",
        *,
        timestamp_s: float | None = None,
    ) -> PhysicalOutputLifecycleResult:
        timestamp_s = _lifecycle_timestamp("timestamp_s", timestamp_s)
        reason = _lifecycle_identifier("reason", reason)
        before = self._state
        if self._state in {"failed", "aborted"}:
            self._stop_deadline_s = None
            self._stop_started_s = None
            event = self._record(
                "cleanup_failure",
                state_before=before,
                state_after=before,
                timestamp_s=timestamp_s,
                reason=reason,
            )
            return self._result(True, event=event)
        self._state = "failed"
        self._clear_latest_request()
        self._stop_deadline_s = None
        self._stop_started_s = None
        event = self._record(
            "cleanup_failure",
            state_before=before,
            state_after=self._state,
            timestamp_s=timestamp_s,
            reason=reason,
        )
        return self._result(False, reason, event)

    @_lifecycle_locked
    def shutdown(
        self,
        reason: str = "runtime_shutdown",
        *,
        now_s: float,
        cleanup: Callable[[], object] | None = None,
        primary_failure: str | None = None,
    ) -> PhysicalOutputLifecycleResult:
        """Run bounded local shutdown while preserving a prior primary failure."""

        now = _required_timestamp("now_s", now_s)
        if primary_failure is not None:
            self.fail(primary_failure, timestamp_s=now)
        stop_result = self.runtime_shutdown(reason, now_s=now)
        completion_now = now
        if cleanup is not None:
            try:
                cleanup_started = self._clock_now()
            except Exception:
                return self.record_cleanup_failure(
                    "cleanup_clock_invalid",
                    timestamp_s=now,
                )
            try:
                cleanup()
            except Exception as exc:  # cleanup must not hide primary failure
                try:
                    cleanup_finished = self._clock_now()
                    cleanup_timestamp = _cleanup_completion_timestamp(
                        now,
                        cleanup_started,
                        cleanup_finished,
                    )
                except Exception:
                    cleanup_timestamp = now
                return self.record_cleanup_failure(
                    f"cleanup_failed:{type(exc).__name__}",
                    timestamp_s=cleanup_timestamp,
                )
            try:
                cleanup_finished = self._clock_now()
                completion_now = _cleanup_completion_timestamp(
                    now,
                    cleanup_started,
                    cleanup_finished,
                )
            except Exception:
                return self.record_cleanup_failure(
                    "cleanup_clock_invalid",
                    timestamp_s=now,
                )
        if self._state == "stopping":
            return self.complete_stop(now_s=completion_now)
        return stop_result

    def _terminal_transition(
        self,
        event_kind: Literal["source_invalid", "abort", "failure"],
        terminal_state: Literal["aborted", "failed"],
        reason: str,
        timestamp_s: float | None,
    ) -> PhysicalOutputLifecycleResult:
        timestamp_s = _lifecycle_timestamp("timestamp_s", timestamp_s)
        reason = _lifecycle_identifier("reason", reason)
        before = self._state
        if self._state in {"failed", "aborted"}:
            self._stop_deadline_s = None
            self._stop_started_s = None
            event = self._record(
                event_kind,
                state_before=before,
                state_after=before,
                timestamp_s=timestamp_s,
                reason="terminal_state_preserved",
            )
            return self._result(True, event=event)
        self._state = terminal_state
        self._clear_latest_request()
        self._stop_deadline_s = None
        self._stop_started_s = None
        event = self._record(
            event_kind,
            state_before=before,
            state_after=self._state,
            timestamp_s=timestamp_s,
            reason=reason,
        )
        return self._result(False, reason, event)


def _cleanup_completion_timestamp(
    shutdown_timestamp: float,
    cleanup_started: float,
    cleanup_finished: float,
) -> float:
    elapsed = cleanup_finished - cleanup_started
    if not isfinite(elapsed) or elapsed < 0.0:
        raise ValueError("cleanup monotonic clock moved backwards or became non-finite")
    completion_timestamp = shutdown_timestamp + elapsed
    if not isfinite(completion_timestamp):
        raise ValueError("cleanup completion timestamp became non-finite")
    return completion_timestamp


def _required_timestamp(name: str, value: object) -> float:
    timestamp = _lifecycle_timestamp(name, value)
    if timestamp is None:
        raise ValueError(f"{name} is required")
    return timestamp


def _safety_freshness_reason(
    evaluation: PhysicalOutputSafetyEvaluation,
    *,
    now_s: float | None,
    max_age_s: float | None,
) -> str | None:
    if now_s is None or max_age_s is None:
        return "physical_safety_freshness_context_missing"
    try:
        now = _required_timestamp("now_s", now_s)
        max_age = _required_timestamp("max_safety_age_s", max_age_s)
    except (TypeError, ValueError):
        return "physical_safety_freshness_context_invalid"
    if max_age < 0.0:
        return "physical_safety_freshness_context_invalid"
    age = now - evaluation.checked_at_s
    if age < 0.0:
        return "physical_safety_decision_timestamp_in_future"
    if age > max_age:
        return "physical_safety_decision_stale"
    return None


def _request_freshness_reason(
    request: PhysicalOutputRequest,
    *,
    now_s: float | None,
    max_age_s: float | None,
) -> str | None:
    if now_s is None or max_age_s is None:
        if now_s is not None:
            try:
                _required_timestamp("now_s", now_s)
            except Exception:
                return "physical_output_freshness_context_invalid"
        if max_age_s is not None:
            try:
                max_age = _lifecycle_timestamp("max_age_s", max_age_s)
            except (TypeError, ValueError):
                return "physical_output_freshness_context_invalid"
            if max_age is None or max_age < 0.0:
                return "physical_output_freshness_context_invalid"
        return "physical_output_freshness_context_missing"
    try:
        max_age = _lifecycle_timestamp("max_age_s", max_age_s)
    except (TypeError, ValueError):
        return "physical_output_freshness_context_invalid"
    if max_age is None or max_age < 0.0:
        return "physical_output_freshness_context_invalid"
    try:
        now = _required_timestamp("now_s", now_s)
    except (TypeError, ValueError):
        return "physical_output_freshness_context_invalid"
    age = now - request.timestamp_s
    if age < 0.0:
        return "physical_output_timestamp_in_future"
    if age > max_age:
        return "physical_output_request_stale"
    return None


__all__ = [
    "PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION",
    "PhysicalOutputLifecycle",
    "PhysicalOutputLifecycleDispatchResult",
    "PhysicalOutputLifecycleEvent",
    "PhysicalOutputLifecycleEventKind",
    "PhysicalOutputLifecycleResult",
    "PhysicalOutputLifecycleSink",
    "PhysicalOutputLifecycleState",
    "PhysicalOutputLifecycleTrace",
]
