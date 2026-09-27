from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from math import isfinite
from typing import cast

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.input_sources.selfrionette import NormalizedLoadcellInputIntent
from xpotato_sim.plugins.mappings.catalog import resolve_control_mapping_plugin
from xpotato_sim.runtime.experiment.contracts import PluginSelection
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceRuntimeDependencies,
    ManagedInputSource,
)
from xpotato_sim.runtime.runners.offline_input_smoke import run_offline_input_runtime_stepping_smoke
from xpotato_sim.schemas import InputIntent, MotionCommand

DEFAULT_LIVE_SELFRIONETTE_BAUD_RATE = 115200
DEFAULT_LIVE_SELFRIONETTE_MAX_FRAMES = 300
DEFAULT_LIVE_SELFRIONETTE_CURRENT_TIP_POSITION_M = (0.1, 0.0, 0.3)
DEFAULT_LIVE_SELFRIONETTE_STEPS_PER_FRAME = 1


@dataclass(frozen=True, slots=True)
class LiveSelfrionetteRuntimeRunnerConfig:
    port: str | None
    baud_rate: int = DEFAULT_LIVE_SELFRIONETTE_BAUD_RATE
    max_frames: int = DEFAULT_LIVE_SELFRIONETTE_MAX_FRAMES
    current_tip_position_m: tuple[
        float, float, float
    ] = DEFAULT_LIVE_SELFRIONETTE_CURRENT_TIP_POSITION_M
    steps_per_frame: int = DEFAULT_LIVE_SELFRIONETTE_STEPS_PER_FRAME

    def __post_init__(self) -> None:
        if self.port is not None and not self.port.strip():
            raise ValueError("port must not be empty")
        if self.baud_rate <= 0:
            raise ValueError("baud_rate must be positive")
        if self.max_frames < 1:
            raise ValueError("max_frames must be a positive integer")
        if self.steps_per_frame < 1:
            raise ValueError("steps_per_frame must be a positive integer")
        object.__setattr__(
            self,
            "current_tip_position_m",
            _coerce_vector3("current_tip_position_m", self.current_tip_position_m),
        )


def _coerce_vector3(name: str, value: object) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    if not isinstance(value, tuple):
        try:
            value = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:  # pragma: no cover - defensive
            raise ValueError(f"{name} must contain exactly three values") from exc

    components = cast(tuple[float, float, float], value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    coerced_components = []
    for component_index, component in enumerate(components):
        component = float(component)
        if not isfinite(component):
            raise ValueError(
                f"{name} must contain only finite values at index {component_index}"
            )
        coerced_components.append(component)

    return cast(tuple[float, float, float], tuple(coerced_components))


def _build_runtime_intent(
    intent: NormalizedLoadcellInputIntent,
    *,
    frame_index: int,
    serial_timestamp_s: float,
    config: LiveSelfrionetteRuntimeRunnerConfig,
) -> NormalizedLoadcellInputIntent:
    metadata = dict(intent.metadata)
    metadata.setdefault("source_kind", "selfrionette")
    metadata.setdefault(
        "acquisition_backend",
        "live_serial" if config.port is not None else "injected_lines",
    )
    metadata["frame_index"] = frame_index
    metadata["serial_timestamp_s"] = serial_timestamp_s
    if config.port is not None:
        metadata["serial_port"] = config.port
        metadata["baud_rate"] = config.baud_rate

    return replace(intent, metadata=metadata)


def run_live_selfrionette_runtime_runner(
    config: LiveSelfrionetteRuntimeRunnerConfig,
    *,
    line_source: Iterable[str] | None = None,
) -> list[Mapping[str, object]]:
    if config.port is None and line_source is None:
        raise ValueError("port is required for live Selfrionette serial backend")

    materialized_lines = tuple(line_source) if line_source is not None else None
    registration = INPUT_SOURCE_CATALOG.resolve("selfrionette")
    source_parameters = (
        {"lines": materialized_lines}
        if materialized_lines is not None
        else {"port": config.port, "baud_rate": config.baud_rate}
    )
    mapping_plugin = resolve_control_mapping_plugin(
        PluginSelection("loadcell_endpoint_mapping", 1)
    )
    effective_mapping_schema = registration.plugin.effective_mapping_input_sample_schema
    if effective_mapping_schema not in mapping_plugin.accepted_input_sample_schemas:
        raise ValueError(
            "Selfrionette source/mapping schema compatibility mismatch: "
            f"mapping input is {effective_mapping_schema.canonical_id!r}"
        )
    mapping_parameters = mapping_plugin.normalize_parameters(
        {
            "mapping_config": {},
            "current_tip_position_m": config.current_tip_position_m,
        }
    )
    source = registration.plugin.create_runtime_reader(
        source_parameters,
        runtime_dependencies=(
            InputSourceRuntimeDependencies(line_source=materialized_lines)
            if materialized_lines is not None
            else None
        ),
    )
    if not isinstance(source, ManagedInputSource):
        raise TypeError("Selfrionette input source must provide managed lifecycle")
    payloads: list[Mapping[str, object]] = []
    start_attempted = False
    primary_failure: BaseException | None = None
    try:
        start_attempted = True
        source.start()
        for frame_index in range(1, config.max_frames + 1):
            try:
                raw_frame = source.read_frame()
            except StopIteration:
                break

            mapping_adapter = registration.plugin.mapping_input_adapter
            if mapping_adapter is None:
                raise ValueError(
                    "Selfrionette input source is missing its mapping input adapter"
                )
            normalized_intent = mapping_adapter(raw_frame)
            if not isinstance(normalized_intent, NormalizedLoadcellInputIntent):
                raise TypeError(
                    "loadcell mapping input adapter returned an invalid normalized intent"
                )
            runtime_intent = _build_runtime_intent(
                normalized_intent,
                frame_index=frame_index,
                serial_timestamp_s=raw_frame.timestamp_s,
                config=config,
            )
            mapped_intent = mapping_plugin.strategy.map_input(
                runtime_intent,
                mapping_parameters,
            )
            if not isinstance(mapped_intent, InputIntent):
                raise TypeError("loadcell mapping strategy returned an invalid input intent")
            motion_command = MotionCommand(
                timestamp_s=mapped_intent.timestamp_s,
                metadata=mapped_intent.metadata,
            )
            runtime_result = run_offline_input_runtime_stepping_smoke(
                motion_command,
                steps=config.steps_per_frame,
                control_mapping=mapping_plugin,
            )
            if runtime_result.payload is None:  # pragma: no cover - defensive
                raise RuntimeError("offline runtime smoke did not produce a payload")
            payloads.append(runtime_result.payload)
    except BaseException as failure:
        primary_failure = failure
        raise
    finally:
        if start_attempted:
            try:
                source.close()
            except BaseException as cleanup_failure:
                if primary_failure is not None:
                    primary_failure.add_note(
                        f"input source cleanup failed: {cleanup_failure!r}"
                    )
                else:
                    raise

    return payloads


__all__ = [
    "DEFAULT_LIVE_SELFRIONETTE_BAUD_RATE",
    "DEFAULT_LIVE_SELFRIONETTE_CURRENT_TIP_POSITION_M",
    "DEFAULT_LIVE_SELFRIONETTE_MAX_FRAMES",
    "DEFAULT_LIVE_SELFRIONETTE_STEPS_PER_FRAME",
    "LiveSelfrionetteRuntimeRunnerConfig",
    "run_live_selfrionette_runtime_runner",
]
