"""左右独立の平面切替。状態は実行sessionに限定し、取得・物理状態を所有しない。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType

from xpotato_sim.schemas.viewer_input import ViewerCanonicalInputSample
from ._binding_validation import integer_pair as _integer_pair

SCHEMA = "gamepad-plane-control/v1"
SIDES = ("left", "right")
ZERO = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class StickBinding:
    axes: tuple[int, int]
    signs: tuple[int, int]
    mode_button: int

    def __post_init__(self) -> None:
        axes, signs = _integer_pair(self.axes, "axes"), _integer_pair(self.signs, "signs")
        if min(axes) < 0 or axes[0] == axes[1] or any(v not in (-1, 1) for v in signs):
            raise ValueError("invalid stick axis indices or signs")
        if type(self.mode_button) is not int or self.mode_button < 0:
            raise ValueError("mode_button requires a non-negative integer")
        object.__setattr__(self, "axes", axes)
        object.__setattr__(self, "signs", signs)


def _binding(value: object) -> StickBinding:
    if not isinstance(value, Mapping) or set(value) != {"axes", "signs", "mode_button"}:
        raise ValueError("stick binding requires axes, signs and mode_button")
    return StickBinding(value["axes"], value["signs"], value["mode_button"])


@dataclass(frozen=True, slots=True)
class PlaneControlConfig:
    output_side: str
    neutral_threshold: float
    left: StickBinding
    right: StickBinding

    def __post_init__(self) -> None:
        if self.output_side not in SIDES:
            raise ValueError("output_side must explicitly select left or right")
        if (type(self.neutral_threshold) not in (int, float)
                or not isfinite(self.neutral_threshold) or not 0 <= self.neutral_threshold <= 0.1):
            raise ValueError("neutral_threshold must be finite in 0..0.1 of raw stick range")
        if not isinstance(self.left, StickBinding) or not isinstance(self.right, StickBinding):
            raise TypeError("left and right require validated stick bindings")
        if len(set(self.left.axes + self.right.axes)) != 4:
            raise ValueError("left and right stick axes must be distinct")
        if self.left.mode_button == self.right.mode_button:
            raise ValueError("left and right mode buttons must be distinct")

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType({
            "schema": SCHEMA, "output_side": self.output_side,
            "neutral_threshold": float(self.neutral_threshold),
            **{side: MappingProxyType({"axes": getattr(self, side).axes,
                                      "signs": getattr(self, side).signs,
                                      "mode_button": getattr(self, side).mode_button}) for side in SIDES},
        })


def coerce_plane_control(value: object) -> PlaneControlConfig:
    if (not isinstance(value, Mapping)
            or set(value) != {"schema", "output_side", "neutral_threshold", "left", "right"}
            or value["schema"] != SCHEMA):
        raise ValueError("gamepad_plane_control requires the complete v1 configuration")
    return PlaneControlConfig(value["output_side"], value["neutral_threshold"],
                              _binding(value["left"]), _binding(value["right"]))


@dataclass(frozen=True, slots=True)
class StickModeState:
    plane: str = "xy"
    requested_plane: str = "xy"
    armed: bool = False
    switches: int = 0


def advance_stick(state: StickModeState, *, requested: str, neutral: bool) -> StickModeState:
    """押下・離上の双方で再arming。中立で切り替えた場合も、そのsampleの速度はゼロ。"""
    changed = requested != state.requested_plane
    armed = state.armed and not changed
    if neutral:
        armed = True
    return StickModeState(requested if armed else state.plane, requested,
                          armed, state.switches + int(changed))


@dataclass(slots=True)
class PlaneControlSession:
    states: dict[str, StickModeState] = field(default_factory=lambda: {side: StickModeState() for side in SIDES})
    _device: object = None
    _last_sequence: int | None = None
    _last_timestamp: float | None = None
    _last_payload: object = None
    _frozen_key: object = None
    _stream: str | None = None
    _retired_streams: set[str] = field(default_factory=set)

    def reset(self) -> None:
        self.states = {side: StickModeState() for side in SIDES}
        self._device = self._last_sequence = self._last_timestamp = self._last_payload = None
        # 設定の凍結は同じsession内の切断で解除しない。

    def update(self, sample: ViewerCanonicalInputSample | None, config: PlaneControlConfig,
               projected_axes: Sequence[float], *, settings_key: object) -> tuple[dict[str, tuple[float, float, float]], str | None]:
        if sample is None:
            self.reset()
            return {side: ZERO for side in SIDES}, "input_unavailable"
        key = (config, settings_key)
        if self._frozen_key is not None and key != self._frozen_key:
            self.reset()
            raise ValueError("plane-control configuration changed within the runtime session")
        self._frozen_key = key
        pad = None if sample is None else sample.gamepad
        valid = (sample is not None and sample.source_kind == "gamepad" and pad is not None
                 and pad.connected and pad.stale is not True
                 and sample.stale_reason in (None, "gamepad_inactive")
                 and (sample.source_active or sample.zero_state))
        if not valid:
            self.reset()
            return {side: ZERO for side in SIDES}, "input_unavailable"
        try:
            raw = pad.raw_axes
            if raw is None or any(not isfinite(v) for v in raw):
                raise ValueError("plane control requires finite raw_axes")
            required_axis = max(config.left.axes + config.right.axes)
            required_button = max(config.left.mode_button, config.right.mode_button)
            if len(raw) <= required_axis or len(projected_axes) <= required_axis or len(pad.buttons) <= required_button:
                raise ValueError("plane control sample is missing a configured axis or shoulder button")
            if sample.sequence is None:
                raise ValueError("plane control requires a source sequence")
            stream = sample.diagnostics.get("provider_session_id")
            if not isinstance(stream, str) or not stream:
                raise ValueError("plane control requires a provider session ID")
            if stream in self._retired_streams:
                raise ValueError("retired plane-control provider session")
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
            payload = (tuple(raw), tuple(projected_axes), tuple((b.pressed, b.value) for b in pad.buttons), sample.requested_control_frame)
            if self._last_sequence is not None:
                if sample.sequence < self._last_sequence or sample.timestamp_s < self._last_timestamp:
                    raise ValueError("out-of-order plane-control sample")
                if sample.sequence == self._last_sequence and (sample.timestamp_s != self._last_timestamp or payload != self._last_payload):
                    raise ValueError("plane-control sequence reused with different content")
            self._last_sequence, self._last_timestamp, self._last_payload = sample.sequence, sample.timestamp_s, payload
            outputs = {}
            for side in SIDES:
                binding = getattr(config, side)
                requested = "xz" if pad.buttons[binding.mode_button].pressed else "xy"
                neutral = all(abs(raw[i]) <= config.neutral_threshold for i in binding.axes)
                state = advance_stick(self.states[side], requested=requested, neutral=neutral)
                self.states[side] = state
                x, vertical = (projected_axes[i] * sign for i, sign in zip(binding.axes, binding.signs, strict=True))
                outputs[side] = (ZERO if not state.armed or not sample.source_active else
                                 (x, vertical, 0.0) if state.plane == "xy" else (x, 0.0, vertical))
            return outputs, None
        except (ValueError, TypeError):
            self.reset()
            raise

    def presentation(
        self,
        config: PlaneControlConfig,
        velocities: Mapping[str, Sequence[float]],
        reason: str | None,
        *,
        output_scope: str = "single_endpoint",
    ) -> dict[str, object]:
        if output_scope not in {"single_endpoint", "coordinated"}:
            raise ValueError("unsupported plane-control output scope")
        return {"schema": SCHEMA,
                "output_side": config.output_side if output_scope == "single_endpoint" else None,
                "output_scope": output_scope, "reason": reason,
                "sides": {side: {
                    "plane": self.states[side].plane,
                    "requested_plane": self.states[side].requested_plane,
                    "status": "armed" if self.states[side].armed else "waiting_neutral",
                    "switch_count": self.states[side].switches,
                    "mode_button": getattr(config, side).mode_button,
                    "velocity_m_s": tuple(velocities[side]),
                } for side in SIDES}}
