"""Canonical backend sample produced by browser viewer input providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

from xpotato_sim.schemas.viewer_control import (
    ViewerControlGamepadMessage,
    ViewerControlKeyboardMessage,
    ViewerControlSourceKind,
)

ViewerProviderId = Literal["keyboard/v1", "gamepad/v1"]
ViewerProviderSchema = Literal["viewer_keyboard_sample/v1", "viewer_gamepad_sample/v1"]
VIEWER_CONTROL_SAMPLE_SCHEMA = "viewer_control_sample/v1"


@dataclass(frozen=True, slots=True)
class ViewerCanonicalInputSample:
    """Provider-neutral raw sample after viewer message validation.

    The source owns this sample and its health fields. Mapping owns all
    interpretation of the provider-specific payload.
    """

    provider_id: ViewerProviderId
    provider_schema: ViewerProviderSchema
    source_kind: ViewerControlSourceKind
    timestamp_s: float
    sequence: int | None
    requested_control_frame: str = "world"
    keyboard: ViewerControlKeyboardMessage | None = None
    gamepad: ViewerControlGamepadMessage | None = None
    source_active: bool = False
    zero_state: bool = True
    stale_reason: str | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isfinite(self.timestamp_s):
            raise ValueError("viewer sample timestamp_s must be finite")
        if self.sequence is not None and (
            type(self.sequence) is not int or self.sequence < 0
        ):
            raise ValueError("viewer sample sequence must be a non-negative integer")
        if self.source_kind == "keyboard":
            if self.provider_id != "keyboard/v1" or self.provider_schema != "viewer_keyboard_sample/v1":
                raise ValueError("keyboard provider identity/schema mismatch")
            if self.keyboard is None or self.gamepad is not None:
                raise ValueError("keyboard viewer sample payload mismatch")
        elif self.source_kind == "gamepad":
            if self.provider_id != "gamepad/v1" or self.provider_schema != "viewer_gamepad_sample/v1":
                raise ValueError("gamepad provider identity/schema mismatch")
            if self.gamepad is None or self.keyboard is not None:
                raise ValueError("gamepad viewer sample payload mismatch")
        else:
            raise ValueError("unknown viewer sample source kind")
        if not isinstance(self.requested_control_frame, str):
            raise ValueError("viewer sample requested_control_frame must be a string")
        if self.source_active and self.stale_reason is not None:
            raise ValueError("active viewer sample cannot have a stale reason")


def viewer_sample_to_metadata(sample: ViewerCanonicalInputSample) -> dict[str, object]:
    """Return the JSON-compatible canonical source sample projection."""

    payload: dict[str, object] = {
        "schema": VIEWER_CONTROL_SAMPLE_SCHEMA,
        "provider_id": sample.provider_id,
        "provider_schema": sample.provider_schema,
        "source_kind": sample.source_kind,
        "timestamp_s": sample.timestamp_s,
        "sequence": sample.sequence,
        "requested_control_frame": sample.requested_control_frame,
        "source_active": sample.source_active,
        "zero_state": sample.zero_state,
        "stale_reason": sample.stale_reason,
        "diagnostics": dict(sample.diagnostics),
    }
    if sample.keyboard is not None:
        keyboard_payload: dict[str, object] = {
            "active_key_codes": tuple(sample.keyboard.active_key_codes),
            "key_state": dict(sample.keyboard.key_state),
        }
        if sample.keyboard.focus_state is not None:
            keyboard_payload["focus_state"] = sample.keyboard.focus_state
        if sample.keyboard.zero_state is not None:
            keyboard_payload["zero_state"] = sample.keyboard.zero_state
        payload["keyboard"] = keyboard_payload
    if sample.gamepad is not None:
        gamepad_payload: dict[str, object] = {
            "connected": sample.gamepad.connected,
            "axes": tuple(sample.gamepad.axes),
            "buttons": tuple(
                {
                    "pressed": button.pressed,
                    **({"value": button.value} if button.value is not None else {}),
                }
                for button in sample.gamepad.buttons
            ),
        }
        if sample.gamepad.raw_axes is not None:
            gamepad_payload["raw_axes"] = tuple(sample.gamepad.raw_axes)
        if sample.gamepad.index is not None:
            gamepad_payload["index"] = sample.gamepad.index
        if sample.gamepad.id is not None:
            gamepad_payload["id"] = sample.gamepad.id
        if sample.gamepad.stale is not None:
            gamepad_payload["stale"] = sample.gamepad.stale
        if sample.gamepad.zero_state is not None:
            gamepad_payload["zero_state"] = sample.gamepad.zero_state
        payload["gamepad"] = gamepad_payload
    return payload


__all__ = [
    "VIEWER_CONTROL_SAMPLE_SCHEMA",
    "ViewerCanonicalInputSample",
    "ViewerProviderId",
    "ViewerProviderSchema",
    "viewer_sample_to_metadata",
]
