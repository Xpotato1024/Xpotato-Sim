"""Canonical loadcell_endpoint_mapping/v1 implementation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Protocol, cast, runtime_checkable

from xpotato_sim.plugins.mappings._command_routes import (
    endpoint_delta_command_route,
)
from xpotato_sim.runtime.experiment.contracts import (
    ControlMappingPlugin,
    ENDPOINT_DELTA_TO_JOINT_POSITION_V1,
    ParameterContract,
    ParameterField,
    VersionedIdentity,
)
from xpotato_sim.schemas import InputIntent, MotionCommand


Vector3 = tuple[float, float, float]
LOADCELL_VECTOR_SAMPLE_SCHEMA = VersionedIdentity("loadcell_vector_sample", 1)
LOADCELL_NORMALIZED_SAMPLE_SCHEMA = VersionedIdentity(
    "loadcell_normalized_input_intent", 1
)
LOADCELL_ENDPOINT_MAPPING_IDENTITY = VersionedIdentity("loadcell_endpoint_mapping", 1)
LOADCELL_MAPPING_SEMANTICS_IDENTITY = VersionedIdentity(
    "loadcell_endpoint_delta", 1
)


@runtime_checkable
class NormalizedLoadcellInputIntentLike(Protocol):
    source: str
    timestamp_s: float
    values: tuple[float, ...]
    active_channels: tuple[int, ...]
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LoadcellEndpointMappingConfig:
    channel_axis_weights: tuple[
        Vector3,
        Vector3,
        Vector3,
        Vector3,
        Vector3,
        Vector3,
        Vector3,
    ] = (
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
    )
    max_delta_m: float = 0.03
    gain_m: float = 0.01
    operational_deadzone: float = 0.0

    def __post_init__(self) -> None:
        if len(self.channel_axis_weights) != 7:
            raise ValueError("channel_axis_weights must contain exactly 7 channel weights")
        weights = tuple(
            _coerce_vector3(f"channel_axis_weights[{channel_index}]", weight)
            for channel_index, weight in enumerate(self.channel_axis_weights)
        )
        object.__setattr__(self, "channel_axis_weights", weights)
        gain_m = _coerce_finite_number("gain_m", self.gain_m)
        if gain_m < 0.0:
            raise ValueError("gain_m must be non-negative")
        object.__setattr__(self, "gain_m", gain_m)
        operational_deadzone = _coerce_finite_number(
            "operational_deadzone",
            self.operational_deadzone,
        )
        if operational_deadzone < 0.0:
            raise ValueError("operational_deadzone must be non-negative")
        object.__setattr__(self, "operational_deadzone", operational_deadzone)
        max_delta_m = _coerce_finite_number("max_delta_m", self.max_delta_m)
        if max_delta_m <= 0.0:
            raise ValueError("max_delta_m must be positive")
        object.__setattr__(self, "max_delta_m", max_delta_m)


def _coerce_finite_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _coerce_vector3(name: str, value: object) -> Vector3:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")
    if len(value) != 3:
        raise ValueError(f"{name} must contain exactly three values")
    components = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise ValueError(f"{name} must contain only finite values")
        converted = float(component)
        if not isfinite(converted):
            raise ValueError(f"{name} must contain only finite values")
        components.append(converted)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")
    return cast(Vector3, tuple(components))


def _coerce_normalized_values(
    values: tuple[float, ...],
) -> tuple[float, float, float, float, float, float, float]:
    if len(values) != 7:
        raise ValueError("loadcell vector must contain exactly 7 values")
    coerced_values = []
    for channel_index, raw_value in enumerate(values):
        if not isfinite(raw_value):
            raise ValueError(f"non-finite loadcell value at index {channel_index}")
        coerced_values.append(float(raw_value))
    return cast(tuple[float, float, float, float, float, float, float], tuple(coerced_values))


def _clamp_vector3_components(value: Vector3, *, limit: float) -> Vector3:
    return cast(Vector3, tuple(max(-limit, min(limit, component)) for component in value))


def _add_vector3(left: Vector3, right: Vector3) -> Vector3:
    return cast(Vector3, tuple(left[index] + right[index] for index in range(3)))


def _compute_endpoint_delta_m(
    values: tuple[float, float, float, float, float, float, float],
    config: LoadcellEndpointMappingConfig,
) -> Vector3:
    endpoint_delta_m = [0.0, 0.0, 0.0]
    for channel_value, channel_weights in zip(values, config.channel_axis_weights, strict=True):
        if abs(channel_value) <= config.operational_deadzone:
            channel_value = 0.0
        endpoint_delta_m[0] += channel_value * channel_weights[0]
        endpoint_delta_m[1] += channel_value * channel_weights[1]
        endpoint_delta_m[2] += channel_value * channel_weights[2]
    scaled_delta_m = cast(
        Vector3,
        tuple(component * config.gain_m for component in endpoint_delta_m),
    )
    return _clamp_vector3_components(scaled_delta_m, limit=config.max_delta_m)


def _build_loadcell_motion_metadata(
    *,
    intent: NormalizedLoadcellInputIntentLike,
    current_tip_position_m: Vector3,
    endpoint_delta_m: Vector3,
    desired_endpoint_m: Vector3,
) -> dict[str, object]:
    metadata = dict(intent.metadata)
    metadata["active_channels"] = intent.active_channels
    metadata["current_tip_position_m"] = current_tip_position_m
    metadata["endpoint_delta_m"] = endpoint_delta_m
    metadata["desired_endpoint_m"] = desired_endpoint_m
    return metadata


def build_r7_a_lite_smoke_endpoint_mapping_config(
    *,
    gain_m: float,
    max_delta_m: float,
    operational_deadzone: float = 0.0,
) -> LoadcellEndpointMappingConfig:
    return LoadcellEndpointMappingConfig(
        channel_axis_weights=(
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        ),
        gain_m=gain_m,
        max_delta_m=max_delta_m,
        operational_deadzone=operational_deadzone,
    )


def build_motion_command_from_normalized_loadcell_intent(
    intent: NormalizedLoadcellInputIntentLike,
    *,
    current_tip_position_m: Vector3,
    config: LoadcellEndpointMappingConfig | None = None,
) -> MotionCommand:
    endpoint_config = LoadcellEndpointMappingConfig() if config is None else config
    normalized_values = _coerce_normalized_values(intent.values)
    current_tip_position_m = _coerce_vector3("current_tip_position_m", current_tip_position_m)
    endpoint_delta_m = _compute_endpoint_delta_m(normalized_values, endpoint_config)
    desired_endpoint_m = _add_vector3(current_tip_position_m, endpoint_delta_m)
    return MotionCommand(
        timestamp_s=intent.timestamp_s,
        metadata=_build_loadcell_motion_metadata(
            intent=intent,
            current_tip_position_m=current_tip_position_m,
            endpoint_delta_m=endpoint_delta_m,
            desired_endpoint_m=desired_endpoint_m,
        ),
    )


class LoadcellEndpointMotionCommandConverter:
    """Convert normalized loadcell intent into a desired-endpoint MotionCommand."""

    def __init__(self, config: LoadcellEndpointMappingConfig | None = None) -> None:
        self._config = LoadcellEndpointMappingConfig() if config is None else config

    @property
    def config(self) -> LoadcellEndpointMappingConfig:
        return self._config

    def convert(
        self,
        intent: NormalizedLoadcellInputIntentLike,
        *,
        current_tip_position_m: Vector3,
    ) -> MotionCommand:
        return build_motion_command_from_normalized_loadcell_intent(
            intent,
            current_tip_position_m=current_tip_position_m,
            config=self._config,
        )


def _config_from_parameters(parameters: Mapping[str, object]) -> LoadcellEndpointMappingConfig:
    value = parameters.get("mapping_config")
    if isinstance(value, LoadcellEndpointMappingConfig):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("loadcell mapping requires mapping_config")
    return LoadcellEndpointMappingConfig(**dict(value))


def _current_tip_from_parameters(parameters: Mapping[str, object]) -> Vector3:
    return _coerce_vector3("current_tip_position_m", parameters.get("current_tip_position_m"))


def normalize_loadcell_endpoint_mapping_parameters(
    parameters: Mapping[str, object],
    *, allow_missing_context: bool = False,
) -> Mapping[str, object]:
    """Validate and canonicalize loadcell mapping parameters before source use."""

    if not isinstance(parameters, Mapping):
        raise TypeError("loadcell mapping parameters must use a mapping")
    mapping_config = parameters.get("mapping_config")
    if isinstance(mapping_config, LoadcellEndpointMappingConfig):
        normalized_config = mapping_config
    elif isinstance(mapping_config, Mapping):
        normalized_config = LoadcellEndpointMappingConfig(**dict(mapping_config))
    else:
        raise ValueError("loadcell mapping requires mapping_config")
    normalized = {"mapping_config": normalized_config}
    if not allow_missing_context or "current_tip_position_m" in parameters:
        normalized["current_tip_position_m"] = _current_tip_from_parameters(parameters)
    return MappingProxyType(normalized)


def normalize_loadcell_runtime_parameters(parameters: Mapping[str, object]) -> Mapping[str, object]:
    """runtimeが後段で供給する観測位置を、任意のplaceholderで補完しない。"""
    return normalize_loadcell_endpoint_mapping_parameters(parameters, allow_missing_context=True)


class LoadcellEndpointMappingStrategy:
    mapping_semantics_identity = LOADCELL_MAPPING_SEMANTICS_IDENTITY

    def map_input(self, input_intent: object, parameters: Mapping[str, object]) -> InputIntent:
        if not isinstance(input_intent, NormalizedLoadcellInputIntentLike):
            raise TypeError(
                "loadcell endpoint mapping requires the source-normalized intent boundary"
            )
        normalized_parameters = normalize_loadcell_endpoint_mapping_parameters(parameters)
        command = build_motion_command_from_normalized_loadcell_intent(
            input_intent,
            current_tip_position_m=_current_tip_from_parameters(normalized_parameters),
            config=_config_from_parameters(normalized_parameters),
        )
        return InputIntent(
            source=input_intent.source,
            timestamp_s=input_intent.timestamp_s,
            values=input_intent.values,
            metadata=command.metadata,
        )


LOADCELL_ENDPOINT_MAPPING_PLUGIN = ControlMappingPlugin(
    identity=LOADCELL_ENDPOINT_MAPPING_IDENTITY,
    strategy=LoadcellEndpointMappingStrategy(),
    accepted_input_sample_schemas=frozenset({LOADCELL_NORMALIZED_SAMPLE_SCHEMA}),
    parameter_contract=ParameterContract(
        (
            ParameterField("mapping_config", object),
            ParameterField("current_tip_position_m", tuple),
        )
    ),
    comparison_family_identity=VersionedIdentity("loadcell_comparison", 1),
    mapping_semantics_identity=LOADCELL_MAPPING_SEMANTICS_IDENTITY,
    command_semantics_routes=frozenset(
        {
            endpoint_delta_command_route(
                route_identity=ENDPOINT_DELTA_TO_JOINT_POSITION_V1,
                control_semantics_identity=LOADCELL_MAPPING_SEMANTICS_IDENTITY,
            )
        }
    ),
    parameter_normalizer=normalize_loadcell_endpoint_mapping_parameters,
    runtime_context_parameters=frozenset({"current_tip_position_m"}),
    runtime_parameter_normalizer=normalize_loadcell_runtime_parameters,
)


__all__ = [
    "LOADCELL_ENDPOINT_MAPPING_IDENTITY",
    "LOADCELL_ENDPOINT_MAPPING_PLUGIN",
    "LOADCELL_MAPPING_SEMANTICS_IDENTITY",
    "LOADCELL_NORMALIZED_SAMPLE_SCHEMA",
    "LOADCELL_VECTOR_SAMPLE_SCHEMA",
    "LoadcellEndpointMappingConfig",
    "LoadcellEndpointMappingStrategy",
    "LoadcellEndpointMotionCommandConverter",
    "NormalizedLoadcellInputIntentLike",
    "build_motion_command_from_normalized_loadcell_intent",
    "build_r7_a_lite_smoke_endpoint_mapping_config",
    "normalize_loadcell_endpoint_mapping_parameters",
]
