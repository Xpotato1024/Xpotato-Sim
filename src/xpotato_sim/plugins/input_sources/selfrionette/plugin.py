"""``selfrionette/v1`` のfixed declaration entry point。

serial/injected-line sourceをloadcell adapterへroutingする。importとcatalog discoveryでは
serial module import、port open、read/close lifecycleを一切開始しない。
"""

from __future__ import annotations

from collections.abc import Sequence

from xpotato_sim.plugins.input_sources.registration import (
    InputSourcePluginRegistration,
    InputSourcePluginRequest,
)
from xpotato_sim.plugins.input_sources import selfrionette
from xpotato_sim.runtime.execution.input_source_adapters import (
    LOADCELL_EXECUTION_ADAPTER,
)
from xpotato_sim.runtime.experiment.contracts import (
    ParameterContract,
    ParameterField,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourceMappingAdapterContract,
    InputSourceMode,
    InputSourcePlugin,
    InputSourceRuntimeDependencies,
)
from xpotato_sim.schemas import RawInputFrame


_MAPPING_INPUT_ADAPTER = InputSourceMappingAdapterContract(
    input_schema=VersionedIdentity("loadcell_vector_sample", 1),
    output_schema=VersionedIdentity("loadcell_normalized_input_intent", 1),
    adapt=selfrionette.normalize_loadcell_frame_for_mapping,
)


def _request(
    *,
    steps: int,
    frames: Sequence[RawInputFrame] | None,
    preset: str | None,
    line_source: Sequence[str] | None = None,
    **_: object,
) -> InputSourcePluginRequest:
    _ = (steps, frames)
    if preset is not None:
        raise ValueError("preset is not supported for Selfrionette input source")
    if line_source is None:
        raise ValueError("Selfrionette input source requires injected lines")
    lines = tuple(line_source)
    return InputSourcePluginRequest(
        parameters={"lines": lines},
        frames=(),
        loop=False,
        initial_metadata={
            "source_kind": "selfrionette",
            "acquisition_backend": "injected_lines",
        },
        runtime_dependencies=InputSourceRuntimeDependencies(line_source=lines),
    )


_PLUGIN = InputSourcePlugin(
    identity=VersionedIdentity("selfrionette", 1),
    produced_sample_schema=VersionedIdentity("loadcell_vector_sample", 1),
    mode=InputSourceMode.LIVE,
    factory=selfrionette.build_reader,
    parameter_contract=ParameterContract(
        (
            ParameterField("port", str, required=False),
            ParameterField("baud_rate", int, required=False),
            ParameterField("lines", tuple, required=False),
        )
    ),
    initial_health=InputSourceHealth(
        InputSourceHealthStatus.DISCONNECTED,
        reason="not_started",
        age_ms=0,
    ),
    initial_metadata={"source_kind": "selfrionette", "baud_rate": 115200},
    mapping_input_adapter=_MAPPING_INPUT_ADAPTER,
)
INPUT_SOURCE_PLUGIN = InputSourcePluginRegistration(
    plugin=_PLUGIN,
    cli_aliases=("selfrionette",),
    request_builder=_request,
    execution_adapter=LOADCELL_EXECUTION_ADAPTER,
)


__all__ = ["INPUT_SOURCE_PLUGIN"]
