"""Input SourceとControl Mapping間のfrozen schema境界。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType

from xpotato_sim.schemas.types import Vector3

_VALID_CONTROL_FRAMES = {"world", "tool"}


@dataclass(frozen=True, slots=True)
class RawInputFrame:
    """source-owned timestamp / values / buttons / metadataの取得frame。

    ``timestamp_s`` のclock origin、valuesのunit、metadataの意味はsource contractが
    所有し、Mappingはsource / schema identityに従って解釈する。frozen dataclassは
    top-level fieldの再代入だけを防ぎ、受け取ったmetadata Mappingをdeep-freezeまたは
    JSON validationしない。
    """

    source: str
    timestamp_s: float
    values: tuple[float, ...] = ()
    buttons: tuple[bool, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InputIntent:
    """Mapping後の汎用intent envelope。command semanticsはconcrete schemaが所有する。"""

    source: str
    timestamp_s: float
    values: tuple[float, ...] = ()
    target_delta_m: Vector3 = (0.0, 0.0, 0.0)
    joint_delta_rad: tuple[float, ...] = ()
    buttons: tuple[bool, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


def _vector3(name: str, value: object) -> Vector3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three values")
    result = tuple(float(component) for component in value)
    if not all(isfinite(component) for component in result):
        raise ValueError(f"{name} must contain only finite values")
    return result  # type: ignore[return-value]


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


def _json_compatible_copy(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible_copy(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return tuple(_json_compatible_copy(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ContinuousEndpointVelocityIntent:
    """Requested continuous endpoint velocity before runtime frame resolution."""

    source_kind: str
    source_timestamp_s: float
    axis_values: Vector3
    deadzone_applied_axis_values: Vector3
    local_endpoint_velocity_m_s: Vector3
    control_frame: str
    source_active: bool
    stale_reason: str | None = None
    local_endpoint_speed_m_s: float = 0.0
    local_endpoint_max_delta_m: float = 0.0
    norm_clamped: bool = False
    source_diagnostics: Mapping[str, object] = field(default_factory=dict)
    intent_kind: str = field(default="local_endpoint_velocity", init=False)
    input_continuity: str = field(default="continuous", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, str) or not self.source_kind.strip():
            raise ValueError("source_kind must be a non-empty string")
        if not isfinite(self.source_timestamp_s):
            raise ValueError("source_timestamp_s must be finite")
        if self.control_frame not in _VALID_CONTROL_FRAMES:
            raise ValueError("control_frame must be 'world' or 'tool'")
        if not isfinite(self.local_endpoint_speed_m_s) or self.local_endpoint_speed_m_s < 0.0:
            raise ValueError("local_endpoint_speed_m_s must be finite and non-negative")
        if not isfinite(self.local_endpoint_max_delta_m) or self.local_endpoint_max_delta_m < 0.0:
            raise ValueError("local_endpoint_max_delta_m must be finite and non-negative")
        if self.source_active and self.stale_reason is not None:
            raise ValueError("an active source cannot have a stale_reason")
        if self.stale_reason is not None and (
            not isinstance(self.stale_reason, str) or not self.stale_reason
        ):
            raise ValueError("stale_reason must be None or a non-empty string")

        object.__setattr__(self, "axis_values", _vector3("axis_values", self.axis_values))
        object.__setattr__(
            self,
            "deadzone_applied_axis_values",
            _vector3("deadzone_applied_axis_values", self.deadzone_applied_axis_values),
        )
        object.__setattr__(
            self,
            "local_endpoint_velocity_m_s",
            _vector3("local_endpoint_velocity_m_s", self.local_endpoint_velocity_m_s),
        )
        object.__setattr__(self, "source_diagnostics", _freeze(self.source_diagnostics))

    @property
    def zero_input(self) -> bool:
        return all(component == 0.0 for component in self.axis_values)

    @property
    def stale(self) -> bool:
        return self.stale_reason is not None

    def to_metadata(self) -> Mapping[str, object]:
        """Return the canonical input-owned metadata subset."""
        return MappingProxyType(
            {
                "source_kind": self.source_kind,
                "source_timestamp_s": self.source_timestamp_s,
                "intent_kind": self.intent_kind,
                "input_continuity": self.input_continuity,
                "source_active": self.source_active,
                "stale_reason": self.stale_reason,
                "control_frame": self.control_frame,
                "axis_values": self.axis_values,
                "deadzone_applied_axis_values": self.deadzone_applied_axis_values,
                "local_endpoint_velocity_m_s": self.local_endpoint_velocity_m_s,
                "local_endpoint_velocity_frame": self.control_frame,
                "local_endpoint_speed_m_s": self.local_endpoint_speed_m_s,
                "local_endpoint_max_delta_m": self.local_endpoint_max_delta_m,
                "zero_input": self.zero_input,
                "norm_clamped": self.norm_clamped,
                "source_diagnostics": _json_compatible_copy(self.source_diagnostics),
            }
        )


__all__ = ["ContinuousEndpointVelocityIntent", "InputIntent", "RawInputFrame"]
