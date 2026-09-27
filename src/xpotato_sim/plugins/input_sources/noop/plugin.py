"""``noop/v1`` のfixed declaration entry point。

明示的なno-op sourceをcompatibility adapterへroutingする。import時にsourceを生成せず、
reader lifecycleを開始しない。runtimeがrequestを選択した場合だけreaderを構築する。
"""

from __future__ import annotations

from collections.abc import Sequence

from xpotato_sim.plugins.input_sources import noop
from xpotato_sim.plugins.input_sources.registration import (
    InputSourcePluginRegistration,
    InputSourcePluginRequest,
)
from xpotato_sim.runtime.execution.input_source_adapters import (
    REPLAY_COMPATIBILITY_EXECUTION_ADAPTER,
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
)
from xpotato_sim.schemas import RawInputFrame


_TARGET_POSITION = (0.6, 0.0, 0.1)


def _request(
    *,
    steps: int,
    frames: Sequence[RawInputFrame] | None,
    preset: str | None,
    **_: object,
) -> InputSourcePluginRequest:
    _ = steps
    if preset is not None:
        raise ValueError("preset is not supported for noop input source")
    if frames is not None:
        raise ValueError("noop input source does not accept custom frames")
    metadata = {
        "preset": "noop",
        "source_kind": "noop",
        "target_position_m": _TARGET_POSITION,
        "desired_endpoint_m": _TARGET_POSITION,
    }
    parameters = {"metadata": metadata}
    return InputSourcePluginRequest(
        parameters=parameters,
        frames=noop.build_frames(parameters),
        loop=True,
        initial_metadata=metadata,
    )


def _adapt_raw_frame(frame: object) -> object:
    if not isinstance(frame, RawInputFrame):
        raise TypeError("replay mapping input adapter requires RawInputFrame")
    return frame


_MAPPING_INPUT_ADAPTER = InputSourceMappingAdapterContract(
    input_schema=VersionedIdentity("noop_sample", 1),
    output_schema=VersionedIdentity("replay_raw_input_frame", 1),
    adapt=_adapt_raw_frame,
)
_PLUGIN = InputSourcePlugin(
    identity=VersionedIdentity("noop", 1),
    produced_sample_schema=VersionedIdentity("noop_sample", 1),
    mode=InputSourceMode.OFFLINE,
    factory=noop.build_reader,
    parameter_contract=ParameterContract((ParameterField("metadata", dict),)),
    initial_health=InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0),
    initial_metadata={"preset": "noop", "source_kind": "noop"},
    mapping_input_adapter=_MAPPING_INPUT_ADAPTER,
)
INPUT_SOURCE_PLUGIN = InputSourcePluginRegistration(
    plugin=_PLUGIN,
    cli_aliases=("noop",),
    request_builder=_request,
    execution_adapter=REPLAY_COMPATIBILITY_EXECUTION_ADAPTER,
)


__all__ = ["INPUT_SOURCE_PLUGIN"]
