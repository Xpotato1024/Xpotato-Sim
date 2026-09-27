"""Command-side desired endpoint resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite

from xpotato_sim.schemas import MotionCommand, Vector3

_DESIRED_ENDPOINT_SOURCE = 'MotionCommand.metadata["desired_endpoint_m"]'
_TARGET_POSITION_SOURCE = 'MotionCommand.metadata["target_position_m"]'
_TARGET_COMMAND_SOURCE = "MotionCommand.target.position_m"


@dataclass(frozen=True, slots=True)
class ResolvedDesiredEndpoint:
    desired_endpoint_m: Vector3
    source: str
    metadata: Mapping[str, object]


def _coerce_vector3(name: str, value: object) -> Vector3:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    try:
        components = tuple(float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc

    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    for component_index, component in enumerate(components):
        if not isfinite(component):
            raise ValueError(f"{name} must contain only finite values at index {component_index}")

    return components


def _coerce_metadata(metadata: object) -> dict[str, object]:
    if not isinstance(metadata, Mapping):
        raise ValueError("MotionCommand.metadata must be a mapping")

    return dict(metadata)


def _resolve_target_position_fallback(command: MotionCommand) -> tuple[object | None, str | None]:
    target = command.target
    if target is not None:
        target_position_m = getattr(target, "position_m", None)
        if target_position_m is not None:
            return target_position_m, _TARGET_COMMAND_SOURCE

    metadata_target_position_m = command.metadata.get("target_position_m")
    if metadata_target_position_m is not None:
        return metadata_target_position_m, _TARGET_POSITION_SOURCE

    return None, None


def resolve_desired_endpoint_from_motion_command(
    command: MotionCommand,
    *,
    allow_target_position_fallback: bool = False,
) -> ResolvedDesiredEndpoint:
    metadata = _coerce_metadata(command.metadata)

    desired_endpoint_m = metadata.get("desired_endpoint_m")
    if desired_endpoint_m is not None:
        return ResolvedDesiredEndpoint(
            desired_endpoint_m=_coerce_vector3(_DESIRED_ENDPOINT_SOURCE, desired_endpoint_m),
            source=_DESIRED_ENDPOINT_SOURCE,
            metadata=metadata,
        )

    if not allow_target_position_fallback:
        raise ValueError(f"{_DESIRED_ENDPOINT_SOURCE} is required")

    fallback_endpoint_m, fallback_source = _resolve_target_position_fallback(command)
    if fallback_endpoint_m is None or fallback_source is None:
        raise ValueError(f"{_DESIRED_ENDPOINT_SOURCE} is required")

    return ResolvedDesiredEndpoint(
        desired_endpoint_m=_coerce_vector3(fallback_source, fallback_endpoint_m),
        source=fallback_source,
        metadata=metadata,
    )


__all__ = [
    "ResolvedDesiredEndpoint",
    "resolve_desired_endpoint_from_motion_command",
]
