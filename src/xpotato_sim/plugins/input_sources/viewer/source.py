"""Backend viewer Input Source implementation.

This module owns message ingestion, canonical raw samples, lifecycle health,
timeout, and the legacy metadata projection. Keyboard/gamepad interpretation
is owned by ``plugins.mappings.viewer_keyboard_gamepad_mapping`` and endpoint continuity is owned by
the runtime step loop.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from time import monotonic

from xpotato_sim.schemas import (
    RawInputFrame,
    ViewerControlMessage,
    ViewerControlMessageError,
    ViewerCanonicalInputSample,
    coerce_viewer_control_message,
    parse_viewer_control_message_json,
    viewer_sample_to_metadata,
)

DEFAULT_VIEWER_INPUT_COMMAND_TIMEOUT_MS = 250
DEFAULT_VIEWER_SAFE_ENDPOINT_M: tuple[float, float, float] = (0.6, 0.0, 0.1)
_VIEWER_SOURCE_KIND = "viewer"
_VIEWER_CONTROL_SUMMARY_KEY = "viewer_control_message"
_SOURCE_INACTIVE_STALE_REASON = "source_inactive"
_VIEWER_KEYBOARD_INACTIVE_STALE_REASON = "keyboard_inactive"
_VIEWER_GAMEPAD_INACTIVE_STALE_REASON = "gamepad_inactive"


def _coerce_vector3(name: str, value: object) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")
    components = tuple(float(component) for component in value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")
    if not all(isfinite(component) for component in components):
        raise ValueError(f"{name} must contain only finite values")
    return components


def _elapsed_ms(now_s: float, last_update_s: float) -> int:
    age_ms = int(round((now_s - last_update_s) * 1000.0))
    return age_ms if age_ms >= 0 else 0


def _stale_reason_for_timeout(timeout_ms: int) -> str:
    return f"command_age_ms_exceeded_timeout_{timeout_ms}"


def _gamepad_zero_state(message: ViewerControlMessage) -> bool:
    gamepad = message.gamepad
    if gamepad is None:
        return True
    # ``zero_state`` is a provider-owned compatibility projection of the
    # frontend-normalized axes and buttons. It must remain the observable
    # gamepad/v1 source-activity signal for both raw and legacy messages;
    # canonical raw_axes is reserved for mapping.
    return gamepad.zero_state is True or not gamepad.connected


def _provider_contract(message: ViewerControlMessage) -> tuple[str, str, bool]:
    if message.source_kind == "keyboard":
        expected = ("keyboard/v1", "viewer_keyboard_sample/v1")
    elif message.source_kind == "gamepad":
        expected = ("gamepad/v1", "viewer_gamepad_sample/v1")
    else:
        raise ViewerControlMessageError("unknown viewer provider source kind")

    provider_id = expected[0] if message.provider_id is None else message.provider_id
    provider_schema = expected[1] if message.provider_schema is None else message.provider_schema
    if provider_id != expected[0]:
        raise ViewerControlMessageError(
            f"viewer provider identity does not match source_kind: {provider_id!r}"
        )
    if provider_schema != expected[1]:
        raise ViewerControlMessageError(
            f"viewer provider schema does not match provider_id: {provider_schema!r}"
        )
    return provider_id, provider_schema, message.provider_id is None


def _canonical_sample(
    message: ViewerControlMessage,
    *,
    source_active: bool,
    zero_state: bool,
    stale_reason: str | None,
) -> ViewerCanonicalInputSample:
    provider_id, provider_schema, legacy_message = _provider_contract(message)
    session_id = message.metadata.get("viewer_provider_session_id")
    if "viewer_provider_session_id" in message.metadata and (
        not isinstance(session_id, str) or not 1 <= len(session_id) <= 128
        or any(not (c.isascii() and (c.isalnum() or c in "-_")) for c in session_id)
    ):
        raise ValueError("invalid viewer_provider_session_id")
    return ViewerCanonicalInputSample(
        provider_id=provider_id,  # type: ignore[arg-type]
        provider_schema=provider_schema,  # type: ignore[arg-type]
        source_kind=message.source_kind,
        timestamp_s=message.timestamp_s,
        sequence=message.sequence,
        requested_control_frame=(
            message.metadata.get("control_frame", "world")
            if isinstance(message.metadata.get("control_frame", "world"), str)
            else "world"
        ),
        keyboard=message.keyboard,
        gamepad=message.gamepad,
        source_active=source_active,
        zero_state=zero_state,
        stale_reason=stale_reason,
        diagnostics={
            "legacy_message": legacy_message,
            **({"provider_session_id": session_id} if session_id is not None else {}),
            "provider_id": provider_id,
            "provider_schema": provider_schema,
        },
    )


def _control_summary(message: ViewerControlMessage) -> dict[str, object]:
    summary: dict[str, object] = {
        "viewer_source_kind": message.source_kind,
        "sequence": message.sequence,
        "keyboard": None,
        "gamepad": None,
        "metadata": dict(message.metadata),
        "intent_kind": message.metadata.get("intent_kind"),
        "input_continuity": message.metadata.get("input_continuity"),
        "control_frame": message.metadata.get("control_frame", "world"),
        "provider_id": message.provider_id,
        "provider_schema": message.provider_schema,
    }
    if message.keyboard is not None:
        keyboard_payload: dict[str, object] = {
            "active_key_codes": tuple(message.keyboard.active_key_codes),
            "key_state": dict(message.keyboard.key_state),
        }
        if message.keyboard.focus_state is not None:
            keyboard_payload["focus_state"] = message.keyboard.focus_state
        if message.keyboard.zero_state is not None:
            keyboard_payload["zero_state"] = message.keyboard.zero_state
        summary["keyboard"] = keyboard_payload
    if message.gamepad is not None:
        gamepad_payload: dict[str, object] = {
            "connected": message.gamepad.connected,
            "axes": tuple(message.gamepad.axes),
            "buttons": tuple(
                {
                    "pressed": button.pressed,
                    **({"value": button.value} if button.value is not None else {}),
                }
                for button in message.gamepad.buttons
            ),
        }
        if message.gamepad.index is not None:
            gamepad_payload["index"] = message.gamepad.index
        if message.gamepad.id is not None:
            gamepad_payload["id"] = message.gamepad.id
        if message.gamepad.stale is not None:
            gamepad_payload["stale"] = message.gamepad.stale
        if message.gamepad.zero_state is not None:
            gamepad_payload["zero_state"] = message.gamepad.zero_state
        summary["gamepad"] = gamepad_payload
    return summary


class ViewerInputSource:
    """Stateful viewer source that emits raw canonical provider samples."""

    def __init__(
        self,
        *,
        initial_endpoint_m: tuple[float, float, float] = DEFAULT_VIEWER_SAFE_ENDPOINT_M,
        timeout_ms: int = DEFAULT_VIEWER_INPUT_COMMAND_TIMEOUT_MS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        self._clock = clock
        self._timeout_ms = timeout_ms
        self._compatibility_endpoint_m = _coerce_vector3("initial_endpoint_m", initial_endpoint_m)
        self._last_update_monotonic_s: float | None = None
        self._last_message_kind: str | None = None
        self._last_control_message: ViewerControlMessage | None = None
        self._has_been_read = False
        self._last_frame = self._build_inactive_frame(
            stale_reason=_SOURCE_INACTIVE_STALE_REASON,
            timestamp_s=0.0,
            command_age_ms=0,
            viewer_source_kind=None,
            control_summary={
                "viewer_source_kind": None,
                "sequence": None,
                "keyboard": None,
                "gamepad": None,
                "metadata": {},
                "intent_kind": None,
                "input_continuity": None,
            },
            values=(),
            buttons=(),
        )
        self._invalid_reason: str | None = None

    @property
    def last_control_message(self) -> ViewerControlMessage | None:
        return self._last_control_message

    @property
    def last_control_message_kind(self) -> str | None:
        return self._last_message_kind

    @property
    def current_endpoint_m(self) -> tuple[float, float, float]:
        """Legacy continuity accessor; runtime owns the actual cursor."""
        return self._compatibility_endpoint_m

    def rebase_current_endpoint_m(self, endpoint_m: Sequence[float]) -> None:
        """Retain the old typed capability without integrating endpoint motion."""
        self._compatibility_endpoint_m = _coerce_vector3("endpoint_m", endpoint_m)

    def rebind_clock(self, clock: Callable[[], float]) -> None:
        if not callable(clock):
            raise TypeError("viewer clock must be callable")
        old_now_s = float(self._clock())
        new_now_s = float(clock())
        if not isfinite(old_now_s) or not isfinite(new_now_s):
            raise ValueError("viewer clock must return finite values")
        if self._last_update_monotonic_s is not None:
            elapsed_s = max(0.0, old_now_s - self._last_update_monotonic_s)
            self._last_update_monotonic_s = new_now_s - elapsed_s
        self._clock = clock

    @property
    def last_received_at_s(self) -> float | None:
        """最新sampleの実受信host monotonic時刻。read時に新鮮な測定へ読み替えない。"""
        return self._last_update_monotonic_s

    def health_snapshot(self) -> tuple[str, str | None, int, dict[str, object]]:
        """Return source-owned health primitives without importing runtime contracts."""

        metadata = self._last_frame.metadata
        age = metadata.get("command_age_ms")
        age_ms = age if type(age) is int and age >= 0 else 0
        health_metadata: dict[str, object] = {}
        if self._last_update_monotonic_s is not None and isinstance(metadata.get("source_kind"), str):
            health_metadata["source_kind"] = metadata["source_kind"]
        if self._invalid_reason is not None or metadata.get("source_health_status") == "invalid":
            return (
                "invalid",
                self._invalid_reason or str(metadata.get("stale_reason") or "invalid_viewer_control_message"),
                age_ms,
                health_metadata,
            )
        if self._last_update_monotonic_s is None or self._last_message_kind is None:
            return (
                "stale",
                "source_inactive" if self._has_been_read else "no_control_message_received",
                age_ms,
                health_metadata,
            )
        sample = metadata.get("viewer_input_sample")
        if isinstance(sample, Mapping):
            gamepad = sample.get("gamepad")
            if isinstance(gamepad, Mapping) and gamepad.get("connected") is False:
                return (
                    "disconnected",
                    str(metadata.get("stale_reason") or "gamepad_disconnected"),
                    age_ms,
                    health_metadata,
                )
        stale_reason = metadata.get("stale_reason")
        if stale_reason is not None:
            return (
                "stale",
                str(stale_reason),
                age_ms,
                health_metadata,
            )
        if age_ms > self._timeout_ms:
            return (
                "stale",
                _stale_reason_for_timeout(self._timeout_ms),
                age_ms,
                health_metadata,
            )
        if metadata.get("source_active") is False:
            return (
                "inactive",
                None,
                age_ms,
                health_metadata,
            )
        return (
            "active",
            None,
            age_ms,
            health_metadata,
        )

    def _mark_invalid(self, reason: object) -> None:
        invalid_reason = str(reason) or "invalid_viewer_control_message"
        self._invalid_reason = invalid_reason
        invalid_metadata = dict(self._last_frame.metadata)
        invalid_metadata.update(
            {
                "source_active": False,
                "stale_reason": invalid_reason,
                "source_health_status": "invalid",
                "command_age_ms": invalid_metadata.get("command_age_ms", 0),
            }
        )
        sample = invalid_metadata.get("viewer_input_sample")
        if isinstance(sample, Mapping):
            invalid_sample = dict(sample)
            invalid_sample.update(
                {
                    "source_active": False,
                    "zero_state": True,
                    "stale_reason": invalid_reason,
                    "diagnostics": {
                        **(
                            dict(sample.get("diagnostics", {}))
                            if isinstance(sample.get("diagnostics"), Mapping)
                            else {}
                        ),
                        "invalid_reason": invalid_reason,
                    },
                }
            )
            invalid_metadata["viewer_input_sample"] = invalid_sample
        self._last_frame = RawInputFrame(
            source=self._last_frame.source,
            timestamp_s=self._last_frame.timestamp_s,
            values=(),
            buttons=(),
            metadata=invalid_metadata,
        )

    def record_ingress_failure(self, reason: str) -> None:
        """Record a parse/schema failure before a typed message reaches the source."""

        self._mark_invalid(reason)

    def _build_inactive_frame(
        self,
        *,
        stale_reason: str,
        timestamp_s: float,
        command_age_ms: int | None,
        viewer_source_kind: str | None,
        control_summary: Mapping[str, object],
        values: tuple[float, ...],
        buttons: tuple[bool, ...],
    ) -> RawInputFrame:
        return RawInputFrame(
            source=_VIEWER_SOURCE_KIND,
            timestamp_s=timestamp_s,
            values=values,
            buttons=buttons,
            metadata={
                "source_kind": _VIEWER_SOURCE_KIND,
                "source_active": False,
                "command_age_ms": command_age_ms,
                "stale_reason": stale_reason,
                "viewer_source_kind": viewer_source_kind,
                "control_frame": "world",
                _VIEWER_CONTROL_SUMMARY_KEY: dict(control_summary),
                "intent_kind": None,
                "input_continuity": None,
                "local_endpoint_velocity_frame": "world",
            },
        )

    def _build_source_frame(
        self,
        message: ViewerControlMessage,
        *,
        source_active: bool,
        stale_reason: str | None,
    ) -> RawInputFrame:
        zero_state = (
            message.keyboard.zero_state is True
            if message.keyboard is not None
            else _gamepad_zero_state(message)
        )
        sample = _canonical_sample(
            message,
            source_active=source_active,
            zero_state=zero_state,
            stale_reason=stale_reason,
        )
        values = () if message.keyboard is not None else tuple(message.gamepad.axes)  # type: ignore[union-attr]
        buttons = (
            tuple(message.keyboard.key_state.get(code, False) for code in message.keyboard.active_key_codes)
            if message.keyboard is not None
            else tuple(button.pressed for button in message.gamepad.buttons)  # type: ignore[union-attr]
        )
        metadata = {
            "source_kind": "viewer_keyboard" if message.keyboard is not None else "viewer_gamepad",
            "source_active": source_active,
            "command_age_ms": 0,
            "stale_reason": stale_reason,
            "viewer_source_kind": message.source_kind,
            "control_frame": message.metadata.get("control_frame", "world"),
            "intent_kind": message.metadata.get("intent_kind"),
            "input_continuity": message.metadata.get("input_continuity"),
            "local_endpoint_velocity_frame": message.metadata.get("control_frame", "world"),
            _VIEWER_CONTROL_SUMMARY_KEY: _control_summary(message),
            "viewer_input_sample": viewer_sample_to_metadata(sample),
        }
        return RawInputFrame(
            source=_VIEWER_SOURCE_KIND,
            timestamp_s=message.timestamp_s,
            values=values,
            buttons=buttons,
            metadata=metadata,
        )

    def ingest_control_message(self, message: ViewerControlMessage) -> RawInputFrame:
        if not isinstance(message, ViewerControlMessage):
            self._mark_invalid("invalid_viewer_control_message")
            raise TypeError("viewer control source requires ViewerControlMessage")
        try:
            if message.keyboard is not None:
                source_active = not (
                    message.keyboard.zero_state is True
                    or message.keyboard.focus_state == "blurred"
                )
                stale_reason = None if source_active else _VIEWER_KEYBOARD_INACTIVE_STALE_REASON
            elif message.gamepad is not None:
                # Preserve the established gamepad/v1 observable activity
                # semantics. raw_axes is an authoritative mapping sample, not
                # a replacement for the provider's zero_state projection.
                source_active = not (
                    _gamepad_zero_state(message)
                    or message.gamepad.stale is True
                    or message.gamepad.connected is False
                )
                stale_reason = None if source_active else _VIEWER_GAMEPAD_INACTIVE_STALE_REASON
            else:
                raise ViewerControlMessageError("viewer control payload is required")
            frame = self._build_source_frame(
                message, source_active=source_active, stale_reason=stale_reason
            )
        except Exception as exc:
            self._mark_invalid(exc)
            raise

        self._invalid_reason = None
        self._last_update_monotonic_s = self._clock()
        self._last_message_kind = message.source_kind
        self._last_control_message = message
        self._last_frame = frame
        return frame

    def ingest_control_message_json(self, message: str) -> RawInputFrame:
        try:
            return self.ingest_control_message(parse_viewer_control_message_json(message))
        except Exception as exc:
            self._mark_invalid(exc)
            raise

    def read_frame(self) -> RawInputFrame:
        self._has_been_read = True
        if self._last_update_monotonic_s is None or self._last_message_kind is None:
            return self._last_frame

        age_ms = _elapsed_ms(self._clock(), self._last_update_monotonic_s)
        metadata = dict(self._last_frame.metadata)
        source_active = bool(metadata.get("source_active", False))
        stale_reason = metadata.get("stale_reason")
        if metadata.get("source_health_status") != "invalid" and age_ms > self._timeout_ms:
            source_active = False
            stale_reason = _stale_reason_for_timeout(self._timeout_ms)
        metadata["source_active"] = source_active
        metadata["command_age_ms"] = age_ms
        metadata["stale_reason"] = stale_reason
        sample = metadata.get("viewer_input_sample")
        if isinstance(sample, Mapping):
            sample = dict(sample)
            sample["source_active"] = source_active
            sample["stale_reason"] = stale_reason
            metadata["viewer_input_sample"] = sample
        self._last_frame = RawInputFrame(
            source=self._last_frame.source,
            timestamp_s=self._last_frame.timestamp_s,
            values=self._last_frame.values,
            buttons=self._last_frame.buttons,
            metadata=metadata,
        )
        return self._last_frame


__all__ = [
    "DEFAULT_VIEWER_INPUT_COMMAND_TIMEOUT_MS",
    "DEFAULT_VIEWER_SAFE_ENDPOINT_M",
    "ViewerInputSource",
]
