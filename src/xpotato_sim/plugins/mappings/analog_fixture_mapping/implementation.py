"""analog_fixture_mapping/v1のcanonical実装。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol, runtime_checkable

from xpotato_sim.plugins.mappings._continuous_endpoint_velocity import (
    build_normalized_analog_fixture_intent,
)
from xpotato_sim.plugins.mappings._command_routes import (
    local_endpoint_velocity_command_route,
)
from xpotato_sim.runtime.experiment.contracts import (
    ControlMappingPlugin,
    LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
    ParameterContract,
    ParameterField,
    VersionedIdentity,
)
from xpotato_sim.schemas import InputIntent, RawInputFrame


Vector3 = tuple[float, float, float]
ANALOG_FIXTURE_SAMPLE_SCHEMA = VersionedIdentity("analog_fixture_sample", 1)
ANALOG_FIXTURE_MAPPING_IDENTITY = VersionedIdentity("analog_fixture_mapping", 1)
ANALOG_FIXTURE_MAPPING_SEMANTICS_IDENTITY = VersionedIdentity(
    "analog_fixture_endpoint_velocity", 1
)


def _number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a JSON number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _vector3(name: str, value: object) -> Vector3:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ValueError(f"{name} must contain exactly three JSON numbers")
    return tuple(_number(name, item) for item in value)  # type: ignore[return-value]


def _numbers(name: str, value: object) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError(f"{name} must be a non-empty sequence of JSON numbers")
    return tuple(_number(f"{name}[{index}]", item) for index, item in enumerate(value))


def _weight_matrix(value: object) -> tuple[Vector3, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError("channel_axis_weights must be a non-empty N x 3 matrix")
    return tuple(_vector3(f"channel_axis_weights[{index}]", row) for index, row in enumerate(value))


@runtime_checkable
class AnalogFixtureSampleLike(Protocol):
    timestamp_s: float
    raw_values: tuple[float, ...]
    active: bool
    stale_reason: str | None


@dataclass(frozen=True, slots=True)
class AnalogFixtureMappingConfig:
    """raw channelからendpointへのimmutable mapping設定。"""

    centers: tuple[float, ...]
    half_ranges: tuple[float, ...]
    channel_axis_weights: tuple[Vector3, ...]
    signs: tuple[int, int, int] = (1, 1, 1)
    scales: Vector3 = (1.0, 1.0, 1.0)
    deadzone: float = 0.0
    speed_m_s: float = 0.1
    max_delta_m: float = 0.01
    control_frame: str = "world"
    source_kind: str = "analog_fixture"

    def __post_init__(self) -> None:
        centers = _numbers("centers", self.centers)
        ranges = _numbers("half_ranges", self.half_ranges)
        weights = _weight_matrix(self.channel_axis_weights)
        if len(centers) != len(ranges) or len(centers) != len(weights):
            raise ValueError("centers, half_ranges, and channel_axis_weights must have equal channel counts")
        object.__setattr__(self, "centers", centers)
        if any(value <= 0.0 for value in ranges):
            raise ValueError("half_ranges must be positive")
        object.__setattr__(self, "half_ranges", ranges)
        object.__setattr__(self, "channel_axis_weights", weights)
        if not isinstance(self.signs, Sequence) or isinstance(self.signs, (str, bytes)):
            raise ValueError("signs must contain exactly three integer -1 or 1 values")
        signs = tuple(self.signs)
        if len(signs) != 3 or any(type(value) is not int or value not in (-1, 1) for value in signs):
            raise ValueError("signs must contain only integer -1 or 1")
        object.__setattr__(self, "signs", signs)
        scales = _vector3("scales", self.scales)
        if any(value < 0.0 for value in scales):
            raise ValueError("scales must be non-negative")
        object.__setattr__(self, "scales", scales)
        for name in ("deadzone", "speed_m_s", "max_delta_m"):
            value = _number(name, getattr(self, name))
            if value < 0.0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        if self.control_frame not in {"world", "tool"}:
            raise ValueError("control_frame must be 'world' or 'tool'")
        if not isinstance(self.source_kind, str) or not self.source_kind.strip():
            raise ValueError("source_kind must be a non-empty string")


def map_analog_fixture_sample(
    sample: AnalogFixtureSampleLike,
    config: AnalogFixtureMappingConfig,
):
    """recorded sample 1件をnormalize、clamp、project、sign、scaleする。"""

    if len(sample.raw_values) != len(config.centers):
        raise ValueError("fixture raw_values channel count must match mapping configuration")
    normalized_channels = tuple(
        max(
            -1.0,
            min(
                1.0,
                (sample.raw_values[index] - config.centers[index]) / config.half_ranges[index],
            ),
        )
        for index in range(len(config.centers))
    )
    weighted_axes = tuple(
        sum(
            value * config.channel_axis_weights[channel][axis]
            for channel, value in enumerate(normalized_channels)
        )
        for axis in range(3)
    )
    axes = tuple(weighted_axes[axis] * config.signs[axis] * config.scales[axis] for axis in range(3))
    return build_normalized_analog_fixture_intent(
        axes,
        source_timestamp_s=sample.timestamp_s,
        source_active=sample.active,
        stale_reason=sample.stale_reason,
        control_frame=config.control_frame,
        source_kind=config.source_kind,
        speed_m_s=config.speed_m_s,
        deadzone=config.deadzone,
        max_delta_m=config.max_delta_m,
        source_diagnostics={"raw_values": sample.raw_values},
    )


def _config_from_parameters(parameters: Mapping[str, object]) -> AnalogFixtureMappingConfig:
    value = parameters.get("mapping_config")
    if isinstance(value, AnalogFixtureMappingConfig):
        config = value
        if "control_frame" in parameters:
            raise ValueError(
                "control_frame must not be duplicated in a typed mapping_config"
            )
        return config
    if not isinstance(value, Mapping):
        raise ValueError("analog_fixture mapping requires mapping_config")
    config_values = dict(value)
    projected_frame = parameters.get("control_frame")
    if projected_frame is not None:
        if "control_frame" in config_values:
            raise ValueError(
                "control_frame must have one owner; use the top-level experiment projection"
            )
        config_values["control_frame"] = projected_frame
    return AnalogFixtureMappingConfig(**config_values)


@dataclass(frozen=True, slots=True)
class _FrameSample:
    timestamp_s: float
    raw_values: tuple[float, ...]
    active: bool
    stale_reason: str | None


class AnalogFixtureMappingStrategy:
    mapping_semantics_identity = ANALOG_FIXTURE_MAPPING_SEMANTICS_IDENTITY

    def map_input(self, input_intent: object, parameters: Mapping[str, object]) -> InputIntent:
        if not isinstance(input_intent, RawInputFrame):
            raise TypeError("analog_fixture mapping requires RawInputFrame")
        source_active = input_intent.metadata.get("source_active", True)
        stale_reason = input_intent.metadata.get("stale_reason")
        if type(source_active) is not bool:
            raise ValueError("analog_fixture source_active metadata must be boolean")
        if stale_reason is not None and not isinstance(stale_reason, str):
            raise ValueError("analog_fixture stale_reason metadata must be a string or null")
        intent = map_analog_fixture_sample(
            _FrameSample(
                timestamp_s=input_intent.timestamp_s,
                raw_values=input_intent.values,
                active=source_active,
                stale_reason=stale_reason,
            ),
            _config_from_parameters(parameters),
        )
        return InputIntent(
            source=intent.source_kind,
            timestamp_s=intent.source_timestamp_s,
            values=intent.axis_values,
            metadata=intent.to_metadata(),
        )


ANALOG_FIXTURE_CONTROL_MAPPING_PLUGIN = ControlMappingPlugin(
    identity=ANALOG_FIXTURE_MAPPING_IDENTITY,
    strategy=AnalogFixtureMappingStrategy(),
    accepted_input_sample_schemas=frozenset({ANALOG_FIXTURE_SAMPLE_SCHEMA}),
    parameter_contract=ParameterContract(
        (
            ParameterField("mapping_config", object),
            ParameterField(
                "control_frame",
                str,
                required=False,
                condition_specific=True,
            ),
        )
    ),
    control_frame=None,
    comparison_family_identity=VersionedIdentity("analog_fixture_comparison", 1),
    mapping_semantics_identity=ANALOG_FIXTURE_MAPPING_SEMANTICS_IDENTITY,
    command_semantics_routes=frozenset(
        {
            local_endpoint_velocity_command_route(
                route_identity=LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1,
                control_semantics_identity=ANALOG_FIXTURE_MAPPING_SEMANTICS_IDENTITY,
            )
        }
    ),
)


__all__ = [
    "ANALOG_FIXTURE_CONTROL_MAPPING_PLUGIN",
    "ANALOG_FIXTURE_MAPPING_IDENTITY",
    "ANALOG_FIXTURE_MAPPING_SEMANTICS_IDENTITY",
    "ANALOG_FIXTURE_SAMPLE_SCHEMA",
    "AnalogFixtureMappingConfig",
    "AnalogFixtureMappingStrategy",
    "AnalogFixtureSampleLike",
    "map_analog_fixture_sample",
]
