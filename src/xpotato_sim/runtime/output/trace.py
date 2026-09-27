"""Lossless, offline physical-output request trace.

The trace records request and permission facts without importing a transport or
opening a file until the caller explicitly asks for artifact persistence.  It
is intentionally limited to requested/permitted/rejected/dropped evidence;
``sent`` and ``acknowledged`` belong to a later transport boundary.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkstemp
from threading import RLock
from typing import TYPE_CHECKING, Literal, TypeAlias

from xpotato_sim.schemas import (
    PhysicalOutputDecision,
    PhysicalOutputPermission,
    PhysicalOutputRequest,
    decode_physical_output_permission,
    decode_physical_output_request,
    encode_physical_output_permission,
    encode_physical_output_request,
)

if TYPE_CHECKING:
    from xpotato_sim.runtime.output.lifecycle import (
        PhysicalOutputLifecycleEvent,
        PhysicalOutputLifecycleTrace,
    )


PHYSICAL_OUTPUT_TRACE_SCHEMA_VERSION = "physical-output-trace/v1"
PhysicalOutputTraceEventKind: TypeAlias = Literal[
    "requested",
    "permitted",
    "rejected",
    "dropped",
]
PhysicalOutputTraceDecisionStatus: TypeAlias = Literal[
    "accepted",
    "rejected",
]

_TRACE_EVENT_KINDS = frozenset({"requested", "permitted", "rejected", "dropped"})
_TRACE_DECISION_STATUSES = frozenset({"accepted", "rejected"})
_TRACE_EVENT_FIELDS = frozenset(
    {
        "decision_status",
        "event_kind",
        "event_sequence",
        "permission",
        "permission_bytes_hex",
        "reason",
        "request",
        "request_bytes_hex",
        "schema_version",
    }
)


def _trace_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL")
    return value


def _trace_non_negative_int(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _trace_canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _trace_parse_json_object(document: bytes | str) -> Mapping[str, object]:
    if isinstance(document, bytes):
        if document.startswith(b"\xef\xbb\xbf"):
            raise ValueError("physical output trace must not contain a UTF-8 BOM")
        try:
            text = document.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("physical output trace must be valid UTF-8") from exc
    elif isinstance(document, str):
        text = document
    else:
        raise TypeError("physical output trace event must be UTF-8 bytes or text")

    def reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field in physical output trace: {key!r}")
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
        raise ValueError("physical output trace event is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("physical output trace event must be a JSON object")
    return value


def _trace_require_fields(
    document: Mapping[str, object],
) -> Mapping[str, object]:
    actual = frozenset(document)
    unknown = sorted(actual - _TRACE_EVENT_FIELDS)
    missing = sorted(_TRACE_EVENT_FIELDS - actual)
    if unknown:
        raise ValueError(f"physical output trace event has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"physical output trace event is missing fields: {missing}")
    if any(not isinstance(key, str) for key in document):
        raise ValueError("physical output trace event keys must be strings")
    return document


def _trace_hex_bytes(name: str, value: object) -> bytes:
    if not isinstance(value, str) or not value or value.lower() != value:
        raise ValueError(f"{name} must be lowercase hexadecimal text")
    if len(value) % 2 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} must be lowercase hexadecimal text")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be lowercase hexadecimal text") from exc


def _write_trace_fsynced(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = mkstemp(
        prefix=f".{path.name}.",
        suffix=".rollback",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    descriptor_open = True
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor_open = False
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary_path.read_bytes() != payload:
            raise OSError("physical output trace rollback temporary read-back mismatch")
        os.replace(temporary_path, path)
    finally:
        if descriptor_open:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def _restore_trace_target(target: Path, previous: bytes | None) -> None:
    if previous is None:
        if target.exists():
            target.unlink()
        return
    _write_trace_fsynced(target, previous)


@dataclass(frozen=True, slots=True)
class PhysicalOutputTraceEvent:
    """One ordered trace event with its bound request and permission snapshot."""

    event_sequence: int
    event_kind: PhysicalOutputTraceEventKind
    request: PhysicalOutputRequest
    permission: PhysicalOutputPermission
    decision_status: PhysicalOutputTraceDecisionStatus | None = None
    reason: str | None = None
    schema_version: str = PHYSICAL_OUTPUT_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _trace_non_negative_int("event_sequence", self.event_sequence)
        if self.schema_version != PHYSICAL_OUTPUT_TRACE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported physical output trace schema_version: "
                f"{self.schema_version!r}"
            )
        if self.event_kind not in _TRACE_EVENT_KINDS:
            raise ValueError(
                "physical output trace event_kind must be one of "
                f"{sorted(_TRACE_EVENT_KINDS)!r}"
            )
        if not isinstance(self.request, PhysicalOutputRequest):
            raise TypeError("physical output trace event requires PhysicalOutputRequest")
        if not isinstance(self.permission, PhysicalOutputPermission):
            raise TypeError("physical output trace event requires PhysicalOutputPermission")
        if (
            self.decision_status is not None
            and self.decision_status not in _TRACE_DECISION_STATUSES
        ):
            raise ValueError(
                "physical output trace decision_status must be accepted or rejected"
            )
        reason = None if self.reason is None else _trace_identifier("reason", self.reason)
        if self.event_kind == "requested":
            if self.decision_status is not None or reason is not None:
                raise ValueError("requested trace event cannot carry a decision")
        elif self.event_kind == "permitted":
            if self.decision_status != "accepted" or reason is not None:
                raise ValueError("permitted trace event requires accepted status only")
            if self.permission.mode == "disabled":
                raise ValueError(
                    "permitted trace event requires non-disabled permission"
                )
            if (
                self.permission.mode
                in {"transmission_enabled", "physical_actuation"}
                and not self.permission.explicitly_enabled
            ):
                raise ValueError(
                    "permitted trace event requires explicit operator enable gate"
                )
        elif self.event_kind == "rejected":
            if self.decision_status != "rejected" or reason is None:
                raise ValueError("rejected trace event requires a reason")
        elif self.decision_status is not None or reason is None:
            raise ValueError("dropped trace event requires a reason only")
        object.__setattr__(self, "reason", reason)

    @property
    def request_bytes(self) -> bytes:
        """Canonical request bytes available to a later adapter for comparison."""

        return encode_physical_output_request(self.request)

    @property
    def permission_bytes(self) -> bytes:
        return encode_physical_output_permission(self.permission)

    def to_json_value(self) -> dict[str, object]:
        return {
            "decision_status": self.decision_status,
            "event_kind": self.event_kind,
            "event_sequence": self.event_sequence,
            "permission": self.permission.to_json_value(),
            "permission_bytes_hex": self.permission_bytes.hex(),
            "reason": self.reason,
            "request": self.request.to_json_value(),
            "request_bytes_hex": self.request_bytes.hex(),
            "schema_version": self.schema_version,
        }

    def to_json_bytes(self) -> bytes:
        return _trace_canonical_json_bytes(self.to_json_value())


def _trace_event_from_json(
    document: bytes | str | Mapping[str, object],
) -> PhysicalOutputTraceEvent:
    if isinstance(document, Mapping):
        payload = _trace_require_fields(document)
    else:
        payload = _trace_require_fields(_trace_parse_json_object(document))

    request_document = payload["request"]
    permission_document = payload["permission"]
    request = decode_physical_output_request(request_document)  # type: ignore[arg-type]
    permission = decode_physical_output_permission(  # type: ignore[arg-type]
        permission_document
    )
    request_bytes = _trace_hex_bytes(
        "request_bytes_hex", payload["request_bytes_hex"]
    )
    permission_bytes = _trace_hex_bytes(
        "permission_bytes_hex", payload["permission_bytes_hex"]
    )
    if request_bytes != encode_physical_output_request(request):
        raise ValueError("physical output trace request bytes do not match request")
    if permission_bytes != encode_physical_output_permission(permission):
        raise ValueError(
            "physical output trace permission bytes do not match permission"
        )

    event_kind = payload["event_kind"]
    if not isinstance(event_kind, str):
        raise ValueError("physical output trace event_kind must be a string")
    decision_status = payload["decision_status"]
    if decision_status is not None and not isinstance(decision_status, str):
        raise ValueError(
            "physical output trace decision_status must be a string or null"
        )
    reason = payload["reason"]
    if reason is not None and not isinstance(reason, str):
        raise ValueError("physical output trace reason must be a string or null")
    return PhysicalOutputTraceEvent(
        event_sequence=_trace_non_negative_int(
            "event_sequence", payload["event_sequence"]
        ),
        event_kind=event_kind,  # type: ignore[arg-type]
        request=request,
        permission=permission,
        decision_status=decision_status,  # type: ignore[arg-type]
        reason=reason,
        schema_version=payload["schema_version"],  # type: ignore[arg-type]
    )


def _validate_trace_events(events: tuple[PhysicalOutputTraceEvent, ...]) -> None:
    expected_event_sequence = 0
    latest_request_sequence: dict[str, int] = {}
    states: dict[tuple[str, int], str] = {}
    requests: dict[tuple[str, int], PhysicalOutputRequest] = {}
    permissions: dict[tuple[str, int], PhysicalOutputPermission] = {}

    for event in events:
        if event.event_sequence != expected_event_sequence:
            raise ValueError(
                "physical output trace event_sequence must be contiguous from zero"
            )
        expected_event_sequence += 1
        key = (event.request.session_id, event.request.sequence)
        previous_request = requests.get(key)
        previous_permission = permissions.get(key)
        if previous_request is not None and previous_request != event.request:
            raise ValueError(
                "physical output trace reuses sequence with another request"
            )
        if previous_permission is not None and previous_permission != event.permission:
            raise ValueError(
                "physical output trace reuses sequence with another permission"
            )

        if event.event_kind == "requested":
            if key in states:
                raise ValueError("duplicate physical output requested event")
            previous_sequence = latest_request_sequence.get(event.request.session_id)
            if (
                previous_sequence is not None
                and event.request.sequence <= previous_sequence
            ):
                raise ValueError("physical output request sequence is out of order")
            latest_request_sequence[event.request.session_id] = event.request.sequence
            states[key] = "requested"
            requests[key] = event.request
            permissions[key] = event.permission
            continue

        if key not in states:
            raise ValueError("physical output trace event has no requested predecessor")
        if event.request.sequence < latest_request_sequence[event.request.session_id]:
            raise ValueError("physical output trace event is late or out of order")
        state = states[key]
        if event.event_kind == "permitted":
            if state != "requested":
                raise ValueError("duplicate or late permitted physical output event")
            states[key] = "permitted"
        elif event.event_kind == "rejected":
            if state != "requested":
                raise ValueError("duplicate or late rejected physical output event")
            states[key] = "rejected"
        elif state not in {"requested", "permitted"}:
            raise ValueError("duplicate or late dropped physical output event")
        else:
            states[key] = "dropped"


@dataclass(frozen=True, slots=True)
class PhysicalOutputTrace:
    """Validated immutable event stream and deterministic JSONL artifact."""

    events: tuple[PhysicalOutputTraceEvent, ...] = ()
    schema_version: str = PHYSICAL_OUTPUT_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PHYSICAL_OUTPUT_TRACE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported physical output trace schema_version: "
                f"{self.schema_version!r}"
            )
        events = tuple(self.events)
        if any(not isinstance(event, PhysicalOutputTraceEvent) for event in events):
            raise TypeError("physical output trace events must be typed trace events")
        object.__setattr__(self, "events", events)
        _validate_trace_events(events)

    def to_jsonl_bytes(self) -> bytes:
        if not self.events:
            return b""
        return b"\n".join(event.to_json_bytes() for event in self.events) + b"\n"

    @classmethod
    def from_jsonl(cls, document: bytes | str) -> "PhysicalOutputTrace":
        if isinstance(document, str):
            document_bytes = document.encode("utf-8")
        elif isinstance(document, bytes):
            document_bytes = document
        else:
            raise TypeError("physical output trace must be UTF-8 bytes or text")
        if document_bytes.startswith(b"\xef\xbb\xbf"):
            raise ValueError("physical output trace must not contain a UTF-8 BOM")
        if not document_bytes:
            return cls()
        if not document_bytes.endswith(b"\n"):
            raise ValueError("physical output trace must end with a newline")
        lines = document_bytes[:-1].split(b"\n")
        if any(not line for line in lines):
            raise ValueError("physical output trace must not contain blank lines")
        events = tuple(_trace_event_from_json(line) for line in lines)
        trace = cls(events=events)
        if trace.to_jsonl_bytes() != document_bytes:
            raise ValueError("physical output trace is not in canonical JSONL form")
        return trace

    def write_atomic(self, path: str | os.PathLike[str]) -> Path:
        """Atomically write and strictly read back this trace artifact."""

        target = Path(path)
        if not target.parent.is_dir():
            raise ValueError("physical output trace target directory must already exist")
        payload = self.to_jsonl_bytes()
        previous = target.read_bytes() if target.exists() else None
        descriptor, temporary_name = mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary_path = Path(temporary_name)
        descriptor_open = True
        replaced = False
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor_open = False
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if temporary_path.read_bytes() != payload:
                raise ValueError("physical output trace temporary read-back mismatch")
            current = target.read_bytes() if target.exists() else None
            if current != previous:
                raise ValueError("physical output trace target changed before atomic replace")
            os.replace(temporary_path, target)
            replaced = True
            read_back = target.read_bytes()
            if read_back != payload:
                raise ValueError("physical output trace strict read-back mismatch")
            decoded = PhysicalOutputTrace.from_jsonl(read_back)
            if decoded != self:
                raise ValueError("physical output trace semantic read-back mismatch")
            return target
        except (OSError, ValueError) as exc:
            if replaced:
                try:
                    _restore_trace_target(target, previous)
                except OSError as rollback_exc:
                    raise ValueError(
                        f"physical output trace rollback failed: {rollback_exc}"
                    ) from rollback_exc
            if isinstance(exc, ValueError):
                raise
            raise ValueError(f"physical output trace atomic write failed: {exc}") from exc
        finally:
            if descriptor_open:
                os.close(descriptor)
            if temporary_path.exists():
                temporary_path.unlink()

    @classmethod
    def read_strict(cls, path: str | os.PathLike[str]) -> "PhysicalOutputTrace":
        target = Path(path)
        document = target.read_bytes()
        trace = cls.from_jsonl(document)
        if trace.to_jsonl_bytes() != document:
            raise ValueError("physical output trace strict read-back mismatch")
        return trace


class PhysicalOutputRecordingSink:
    """Recording-only sink for request and permission evidence."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._events: list[PhysicalOutputTraceEvent] = []
        self._lifecycle_events: list[PhysicalOutputLifecycleEvent] = []

    @property
    def events(self) -> tuple[PhysicalOutputTraceEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def _append(
        self,
        event_factory: Callable[[int], PhysicalOutputTraceEvent],
    ) -> PhysicalOutputTraceEvent:
        # sequence採番、strict validation、appendを同一critical sectionへ置き、
        # concurrent writerによるevent sequence再利用を防ぐ。
        with self._lock:
            event = event_factory(len(self._events))
            candidate = PhysicalOutputTrace(events=(*self._events, event))
            self._events = list(candidate.events)
            return event

    @property
    def lifecycle_events(self) -> tuple[PhysicalOutputLifecycleEvent, ...]:
        """Lifecycle evidence kept separate from physical request events."""

        with self._lock:
            return tuple(self._lifecycle_events)

    def record_lifecycle_event(
        self,
        event: PhysicalOutputLifecycleEvent,
    ) -> PhysicalOutputLifecycleEvent:
        # Keep the runtime boundary typed: an arbitrary object exposing a
        # to_json_bytes method must not enter the lifecycle evidence stream.
        from xpotato_sim.runtime.output.lifecycle import PhysicalOutputLifecycleEvent

        if not isinstance(event, PhysicalOutputLifecycleEvent):
            raise TypeError("lifecycle sink requires PhysicalOutputLifecycleEvent")
        with self._lock:
            self._lifecycle_events.append(event)
        return event

    def lifecycle_trace(self) -> PhysicalOutputLifecycleTrace:
        """Build the separate lifecycle trace captured by this dry-run sink."""

        from xpotato_sim.runtime.output.lifecycle import PhysicalOutputLifecycleTrace

        with self._lock:
            events = tuple(self._lifecycle_events)
        return PhysicalOutputLifecycleTrace(events=events)

    def lifecycle_trace_bytes(self) -> bytes:
        trace = self.lifecycle_trace()
        return trace.to_jsonl_bytes()


    def record_requested(
        self,
        request: PhysicalOutputRequest,
        permission: PhysicalOutputPermission,
    ) -> PhysicalOutputTraceEvent:
        return self._append(
            lambda event_sequence: PhysicalOutputTraceEvent(
                event_sequence=event_sequence,
                event_kind="requested",
                request=request,
                permission=permission,
            )
        )

    def record_permission_decision(
        self,
        decision: PhysicalOutputDecision,
    ) -> PhysicalOutputTraceEvent:
        if not isinstance(decision, PhysicalOutputDecision):
            raise TypeError("physical output trace requires PhysicalOutputDecision")
        if decision.status == "accepted":
            return self._append(
                lambda event_sequence: PhysicalOutputTraceEvent(
                    event_sequence=event_sequence,
                    event_kind="permitted",
                    request=decision.request,
                    permission=decision.permission,
                    decision_status="accepted",
                )
            )
        return self._append(
            lambda event_sequence: PhysicalOutputTraceEvent(
                event_sequence=event_sequence,
                event_kind="rejected",
                request=decision.request,
                permission=decision.permission,
                decision_status="rejected",
                reason=decision.reason,
            )
        )

    record_decision = record_permission_decision

    def record_dropped(
        self,
        request: PhysicalOutputRequest,
        permission: PhysicalOutputPermission,
        *,
        reason: str,
    ) -> PhysicalOutputTraceEvent:
        return self._append(
            lambda event_sequence: PhysicalOutputTraceEvent(
                event_sequence=event_sequence,
                event_kind="dropped",
                request=request,
                permission=permission,
                reason=reason,
            )
        )

    def snapshot(self) -> PhysicalOutputTrace:
        with self._lock:
            return PhysicalOutputTrace(events=tuple(self._events))

    def to_jsonl_bytes(self) -> bytes:
        return self.snapshot().to_jsonl_bytes()

    def write_atomic(self, path: str | os.PathLike[str]) -> Path:
        return self.snapshot().write_atomic(path)


def replay_physical_output_trace(
    document: PhysicalOutputTrace | bytes | str,
) -> PhysicalOutputTrace:
    """Replay trace events into a recording sink without output side effects."""

    source = (
        document
        if isinstance(document, PhysicalOutputTrace)
        else PhysicalOutputTrace.from_jsonl(document)
    )
    sink = PhysicalOutputRecordingSink()
    for event in source.events:
        if event.event_kind == "requested":
            sink.record_requested(event.request, event.permission)
        elif event.event_kind == "permitted":
            sink.record_permission_decision(
                PhysicalOutputDecision(
                    request=event.request,
                    permission=event.permission,
                    status="accepted",
                )
            )
        elif event.event_kind == "rejected":
            sink.record_permission_decision(
                PhysicalOutputDecision(
                    request=event.request,
                    permission=event.permission,
                    status="rejected",
                    reason=event.reason,
                )
            )
        else:
            sink.record_dropped(
                event.request,
                event.permission,
                reason=event.reason or "trace_drop",
            )
    replayed = sink.snapshot()
    if replayed.to_jsonl_bytes() != source.to_jsonl_bytes():
        raise ValueError("physical output trace replay is not byte-equivalent")
    return replayed


def physical_output_traces_equivalent(
    expected: PhysicalOutputTrace | bytes | str,
    actual: PhysicalOutputTrace | bytes | str,
) -> bool:
    expected_trace = (
        expected
        if isinstance(expected, PhysicalOutputTrace)
        else PhysicalOutputTrace.from_jsonl(expected)
    )
    actual_trace = (
        actual
        if isinstance(actual, PhysicalOutputTrace)
        else PhysicalOutputTrace.from_jsonl(actual)
    )
    return expected_trace.to_jsonl_bytes() == actual_trace.to_jsonl_bytes()


__all__ = [
    "PHYSICAL_OUTPUT_TRACE_SCHEMA_VERSION",
    "PhysicalOutputRecordingSink",
    "PhysicalOutputTrace",
    "PhysicalOutputTraceDecisionStatus",
    "PhysicalOutputTraceEvent",
    "PhysicalOutputTraceEventKind",
    "physical_output_traces_equivalent",
    "replay_physical_output_trace",
]
