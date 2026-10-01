"""Gamepad stick XY + analog trigger Z mapping."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType

from xpotato_sim.schemas.viewer_input import ViewerCanonicalInputSample
from ._binding_validation import integer_pair as _integer_pair

SCHEMA = "gamepad-trigger-control/v1"
SIDES = ("left", "right")
ZERO = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class TriggerBinding:
    axes: tuple[int, int]
    signs: tuple[int, int]
    trigger_button: int
    sign_button: int
    def __post_init__(self) -> None:
        axes, signs = _integer_pair(self.axes, "axes"), _integer_pair(self.signs, "signs")
        if min(axes) < 0 or axes[0] == axes[1] or any(v not in (-1, 1) for v in signs):
            raise ValueError("invalid stick axis indices or signs")
        for name, value in (("trigger_button", self.trigger_button), ("sign_button", self.sign_button)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} requires a non-negative integer")
        if self.trigger_button == self.sign_button:
            raise ValueError("trigger and sign buttons must be distinct")
        object.__setattr__(self, "axes", axes)
        object.__setattr__(self, "signs", signs)


def _binding(value: object) -> TriggerBinding:
    required = {"axes", "signs", "trigger_button", "sign_button"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("trigger binding requires axes, signs, trigger_button and sign_button")
    return TriggerBinding(value["axes"], value["signs"], value["trigger_button"], value["sign_button"])


@dataclass(frozen=True, slots=True)
class TriggerControlConfig:
    output_side: str
    neutral_threshold: float
    left: TriggerBinding
    right: TriggerBinding
    def __post_init__(self) -> None:
        if self.output_side not in SIDES:
            raise ValueError("output_side must explicitly select left or right")
        if (type(self.neutral_threshold) not in (int, float)
                or not isfinite(self.neutral_threshold) or not 0 <= self.neutral_threshold <= 0.1):
            raise ValueError("neutral_threshold must be finite in 0..0.1")
        if not isinstance(self.left, TriggerBinding) or not isinstance(self.right, TriggerBinding):
            raise TypeError("left and right require validated trigger bindings")
        if len(set(self.left.axes + self.right.axes)) != 4:
            raise ValueError("left and right stick axes must be distinct")
        buttons = (
            self.left.trigger_button, self.left.sign_button,
            self.right.trigger_button, self.right.sign_button,
        )
        if len(set(buttons)) != len(buttons):
            raise ValueError("trigger/sign buttons must be distinct across sides")

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema": SCHEMA,
            "output_side": self.output_side,
            "neutral_threshold": float(self.neutral_threshold),
            **{side: MappingProxyType({
                "axes": getattr(self, side).axes,
                "signs": getattr(self, side).signs,
                "trigger_button": getattr(self, side).trigger_button,
                "sign_button": getattr(self, side).sign_button,
            }) for side in SIDES},
        })

def coerce_trigger_control(value: object) -> TriggerControlConfig:
    if (not isinstance(value, Mapping)
            or set(value) != {"schema", "output_side", "neutral_threshold", "left", "right"}
            or value["schema"] != SCHEMA):
        raise ValueError("gamepad_trigger_control requires the complete v1 configuration")
    return TriggerControlConfig(
        value["output_side"], value["neutral_threshold"],
        _binding(value["left"]), _binding(value["right"]),
    )


@dataclass(frozen=True, slots=True)
class TriggerSideState:
    z_sign: int = 1
    armed: bool = False


@dataclass(slots=True)
class TriggerControlSession:
    states: dict[str, TriggerSideState] = field(
        default_factory=lambda: {side: TriggerSideState() for side in SIDES}
    )
    _device: object = None
    _last_sequence: int | None = None
    _last_timestamp: float | None = None
    _last_payload: object = None
    _frozen_key: object = None
    _stream: str | None = None
    _retired_streams: set[str] = field(default_factory=set)
    def reset(self) -> None:
        self.states = {side: TriggerSideState() for side in SIDES}
        self._device = self._last_sequence = self._last_timestamp = self._last_payload = None

    def update(
        self,
        sample: ViewerCanonicalInputSample | None,
        config: TriggerControlConfig,
        projected_axes: Sequence[float],
        *,
        settings_key: object,
    ) -> tuple[dict[str, tuple[float, float, float]], dict[str, float], str | None]:
        if sample is None:
            self.reset()
            return {side: ZERO for side in SIDES}, {side: 0.0 for side in SIDES}, "input_unavailable"
        key = (config, settings_key)
        if self._frozen_key is not None and key != self._frozen_key:
            self.reset()
            raise ValueError("trigger-control configuration changed within the runtime session")
        self._frozen_key = key
        pad = sample.gamepad
        valid = (
            sample.source_kind == "gamepad" and pad is not None and pad.connected and pad.stale is not True
            and sample.stale_reason in (None, "gamepad_inactive") and (sample.source_active or sample.zero_state)
        )
        if not valid:
            self.reset()
            return {side: ZERO for side in SIDES}, {side: 0.0 for side in SIDES}, "input_unavailable"
        try:
            raw = pad.raw_axes
            if raw is None or any(not isfinite(v) for v in raw):
                raise ValueError("trigger control requires finite raw_axes")
            required_axis = max(config.left.axes + config.right.axes)
            required_button = max(
                config.left.trigger_button, config.left.sign_button,
                config.right.trigger_button, config.right.sign_button,
            )
            if len(raw) <= required_axis or len(projected_axes) <= required_axis or len(pad.buttons) <= required_button:
                raise ValueError("trigger control sample is missing a configured axis or button")
            if sample.sequence is None:
                raise ValueError("trigger control requires a source sequence")
            stream = sample.diagnostics.get("provider_session_id")
            if not isinstance(stream, str) or not stream:
                raise ValueError("trigger control requires a provider session ID")
            if stream in self._retired_streams:
                raise ValueError("retired trigger-control provider session")
            if stream != self._stream:
                if len(self._retired_streams) >= 128:
                    raise ValueError("too many provider sessions; start a new run")
                if self._stream is not None:
                    self._retired_streams.add(self._stream)
                self._stream = stream
                self.reset()
            device = (pad.index, pad.id)
            if device != self._device:
                self.reset()
                self._device = device
            payload = (
                tuple(raw), tuple(projected_axes),
                tuple((b.pressed, b.value) for b in pad.buttons),
                sample.requested_control_frame,
            )
            if self._last_sequence is not None:
                if sample.sequence < self._last_sequence or sample.timestamp_s < self._last_timestamp:
                    raise ValueError("out-of-order trigger-control sample")
                if sample.sequence == self._last_sequence and (
                    sample.timestamp_s != self._last_timestamp or payload != self._last_payload
                ):
                    raise ValueError("trigger-control sequence reused with different content")
            self._last_sequence, self._last_timestamp, self._last_payload = (
                sample.sequence, sample.timestamp_s, payload
            )

            outputs: dict[str, tuple[float, float, float]] = {}
            trigger_values: dict[str, float] = {}
            for side in SIDES:
                binding = getattr(config, side)
                trigger = pad.buttons[binding.trigger_button].value
                if trigger is None or not isfinite(trigger) or not 0.0 <= trigger <= 1.0:
                    raise ValueError("trigger control requires analog trigger values in 0..1")
                trigger_values[side] = float(trigger)
                state = self.states[side]
                if trigger <= config.neutral_threshold:
                    state = TriggerSideState(
                        z_sign=-1 if pad.buttons[binding.sign_button].pressed else 1,
                        armed=True,
                    )
                    self.states[side] = state
                x, y = (
                    projected_axes[i] * sign
                    for i, sign in zip(binding.axes, binding.signs, strict=True)
                )
                scaled_trigger = (
                    0.0 if trigger <= config.neutral_threshold
                    else (trigger - config.neutral_threshold) / max(1.0 - config.neutral_threshold, 1e-12)
                )
                z = scaled_trigger * state.z_sign if state.armed else 0.0
                outputs[side] = (x, y, z) if sample.source_active else ZERO
            return outputs, trigger_values, None
        except (ValueError, TypeError):
            self.reset()
            raise

    def presentation(
        self,
        config: TriggerControlConfig,
        velocities: Mapping[str, Sequence[float]],
        trigger_values: Mapping[str, float],
        reason: str | None,
        *,
        output_scope: str = "single_endpoint",
    ) -> dict[str, object]:
        if output_scope not in {"single_endpoint", "coordinated"}:
            raise ValueError("unsupported trigger-control output scope")
        return {
            "schema": SCHEMA,
            "output_side": config.output_side if output_scope == "single_endpoint" else None,
            "output_scope": output_scope,
            "reason": reason,
            "sides": {
                side: {
                    "status": "armed" if self.states[side].armed else "waiting_trigger_neutral",
                    "z_sign": self.states[side].z_sign,
                    "trigger_value": float(trigger_values.get(side, 0.0)),
                    "trigger_button": getattr(config, side).trigger_button,
                    "sign_button": getattr(config, side).sign_button,
                    "velocity_m_s": tuple(velocities[side]),
                }
                for side in SIDES
            },
        }
