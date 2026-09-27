"""Keyboard-to-axis mapping primitives owned by the Control Mapping plugin."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from xpotato_sim.plugins.mappings._continuous_endpoint_velocity import (
    build_continuous_endpoint_velocity_intent,
)
from xpotato_sim.schemas import ContinuousEndpointVelocityIntent, MotionCommand, RawInputFrame

_VALID_AXES = {"x", "y", "z"}
_DEFAULT_CONFIG_PATH = Path(__file__).with_name("resources") / "keyboard_default.json"


@dataclass(frozen=True, slots=True)
class KeyboardBinding:
    axis: str
    direction: int

    def __post_init__(self) -> None:
        if self.axis not in _VALID_AXES:
            raise ValueError(f"unknown axis: {self.axis!r}")
        if self.direction not in {-1, 1}:
            raise ValueError("direction must be -1 or +1")


@dataclass(frozen=True, slots=True)
class KeyboardInputConfig:
    bindings: Mapping[str, KeyboardBinding]
    speed_m_s: float
    deadzone: float
    max_delta_m: float

    def __post_init__(self) -> None:
        if not isfinite(self.speed_m_s):
            raise ValueError("speed_m_s must be finite")
        if self.speed_m_s < 0.0:
            raise ValueError("speed_m_s must be non-negative")
        if not isfinite(self.deadzone):
            raise ValueError("deadzone must be finite")
        if self.deadzone < 0.0:
            raise ValueError("deadzone must be non-negative")
        if not isfinite(self.max_delta_m):
            raise ValueError("max_delta_m must be finite")
        if self.max_delta_m < 0.0:
            raise ValueError("max_delta_m must be non-negative")
        for key_code, binding in self.bindings.items():
            if not isinstance(key_code, str) or not key_code:
                raise ValueError("binding key codes must be non-empty strings")
            if getattr(binding, "axis", None) not in _VALID_AXES:
                raise ValueError(f"unknown axis: {getattr(binding, 'axis', None)!r}")
            if getattr(binding, "direction", None) not in {-1, 1}:
                raise ValueError("direction must be +1 or -1")


def _coerce_vector3(name: str, value: object) -> tuple[float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three values")
    result = tuple(float(component) for component in value)
    for component_index, component in enumerate(result):
        if not isfinite(component):
            raise ValueError(f"{name} must contain only finite values at index {component_index}")
    return result  # type: ignore[return-value]


def _load_keyboard_config_from_path(path: Path) -> KeyboardInputConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("source_kind") != "keyboard":
        raise ValueError("keyboard config source_kind must be 'keyboard'")
    bindings_payload = payload.get("bindings")
    if not isinstance(bindings_payload, dict):
        raise ValueError("keyboard config bindings must be a JSON object")
    bindings: dict[str, KeyboardBinding] = {}
    for key_code, binding_payload in bindings_payload.items():
        if not isinstance(binding_payload, dict) or "axis" not in binding_payload or "direction" not in binding_payload:
            raise ValueError(f"binding {key_code!r} must include axis and direction")
        bindings[key_code] = KeyboardBinding(
            axis=str(binding_payload["axis"]),
            direction=int(binding_payload["direction"]),
        )
    return KeyboardInputConfig(
        bindings=bindings,
        speed_m_s=float(payload["speed_m_s"]),
        deadzone=float(payload["deadzone"]),
        max_delta_m=float(payload["max_delta_m"]),
    )


def build_default_keyboard_input_config() -> KeyboardInputConfig:
    return _load_keyboard_config_from_path(_DEFAULT_CONFIG_PATH)


def build_keyboard_continuous_velocity_intent(
    pressed_keys: Iterable[str],
    *,
    timestamp_s: float,
    config: KeyboardInputConfig | None = None,
    control_frame: str = "world",
    source_active: bool = True,
    stale_reason: str | None = None,
    source_kind: str = "keyboard",
) -> ContinuousEndpointVelocityIntent:
    keyboard_config = build_default_keyboard_input_config() if config is None else config
    pressed_key_tuple = tuple(sorted(dict.fromkeys(pressed_keys)))
    axis_by_axis = {"x": 0.0, "y": 0.0, "z": 0.0}
    for key_code in pressed_key_tuple:
        binding = keyboard_config.bindings.get(key_code)
        if binding is not None:
            axis_by_axis[binding.axis] += float(binding.direction)
    return build_continuous_endpoint_velocity_intent(
        (axis_by_axis["x"], axis_by_axis["y"], axis_by_axis["z"]),
        source_kind=source_kind,
        source_timestamp_s=timestamp_s,
        speed_m_s=keyboard_config.speed_m_s,
        deadzone=keyboard_config.deadzone,
        max_delta_m=keyboard_config.max_delta_m,
        control_frame=control_frame,
        source_active=source_active,
        stale_reason=stale_reason,
        source_diagnostics={"pressed_keys": pressed_key_tuple},
        clamp_before_deadzone=True,
    )


def build_keyboard_motion_command(
    pressed_keys: Iterable[str],
    *,
    current_tip_position_m: tuple[float, float, float],
    timestamp_s: float,
    config: KeyboardInputConfig | None = None,
) -> MotionCommand:
    intent = build_keyboard_continuous_velocity_intent(
        pressed_keys, timestamp_s=timestamp_s, config=config
    )
    endpoint_velocity_m_s = intent.local_endpoint_velocity_m_s
    metadata = dict(intent.to_metadata())
    metadata.update(
        {
            "pressed_keys": intent.source_diagnostics["pressed_keys"],
            "resolved_world_endpoint_velocity_m_s": endpoint_velocity_m_s,
            "endpoint_velocity_m_s": endpoint_velocity_m_s,
            "endpoint_velocity_frame": "mujoco_world",
            "current_tip_position_m": _coerce_vector3("current_tip_position_m", current_tip_position_m),
        }
    )
    return MotionCommand(timestamp_s=timestamp_s, metadata=metadata)


__all__ = [
    "KeyboardBinding",
    "KeyboardInputConfig",
    "build_default_keyboard_input_config",
    "build_keyboard_continuous_velocity_intent",
    "build_keyboard_motion_command",
]
