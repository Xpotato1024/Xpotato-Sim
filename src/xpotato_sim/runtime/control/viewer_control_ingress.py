"""Viewer control ingress boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import monotonic

from xpotato_sim.plugins.input_sources.viewer import (
    DEFAULT_VIEWER_INPUT_COMMAND_TIMEOUT_MS,
    DEFAULT_VIEWER_SAFE_ENDPOINT_M,
    ViewerInputSource,
)
from xpotato_sim.runtime.experiment.input_source import ViewerBridgeRuntimeCapability
from xpotato_sim.schemas import RawInputFrame, ViewerControlMessage, coerce_viewer_control_message, parse_viewer_control_message_json


def build_viewer_input_source(
    *,
    clock: Callable[[], float] | None = None,
    timeout_ms: int = DEFAULT_VIEWER_INPUT_COMMAND_TIMEOUT_MS,
    initial_endpoint_m: tuple[float, float, float] = DEFAULT_VIEWER_SAFE_ENDPOINT_M,
) -> ViewerInputSource:
    return ViewerInputSource(
        clock=(clock if clock is not None else monotonic),
        timeout_ms=timeout_ms,
        initial_endpoint_m=initial_endpoint_m,
    )


def ingest_viewer_control_message(
    source: ViewerBridgeRuntimeCapability,
    message: ViewerControlMessage | str | Mapping[str, object],
    *, expected_provider_id: str | None = None,
) -> RawInputFrame:
    if expected_provider_id not in (None, "keyboard/v1", "gamepad/v1"):
        raise ValueError("unknown expected viewer input provider")
    try:
        if isinstance(message, str):
            validated_message = parse_viewer_control_message_json(message)
        elif isinstance(message, ViewerControlMessage):
            validated_message = message
        else:
            validated_message = coerce_viewer_control_message(message)
        if expected_provider_id is not None and validated_message.source_kind != expected_provider_id.split("/", 1)[0]:
            raise ValueError("viewer input provider does not match launch profile")
    except Exception as exc:
        source.record_ingress_failure(str(exc))
        raise

    return source.ingest_control_message(validated_message)


def ingest_viewer_control_message_json(source: ViewerBridgeRuntimeCapability, message: str) -> RawInputFrame:
    return ingest_viewer_control_message(source, message)


__all__ = [
    "build_viewer_input_source",
    "ingest_viewer_control_message",
    "ingest_viewer_control_message_json",
]
