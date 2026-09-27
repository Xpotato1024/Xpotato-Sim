"""``replay/v1`` のfixed declaration entry point。

caller-owned frozen frame列をreplay compatibility adapterへroutingする。import時に
fileを読まず、reader construction/read lifecycleも開始しない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from xpotato_sim.plugins.input_sources import replay
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
    InputSourceMode,
    InputSourcePlugin,
    InputSourceRuntimeDependencies,
)
from xpotato_sim.schemas import RawInputFrame


_TARGET_POSITION = (0.6, 0.0, 0.1)


def _request(
    *,
    steps: int,
    frames: Sequence[RawInputFrame] | None,
    preset: str | None,
    replay_initial_metadata: Mapping[str, object] | None = None,
    **_: object,
) -> InputSourcePluginRequest:
    _ = steps
    if preset is not None:
        raise ValueError("preset is not supported for replay input source")
    metadata = {
        "preset": "r6-h-p5-default",
        "target_position_m": _TARGET_POSITION,
        "desired_endpoint_m": _TARGET_POSITION,
    }
    if replay_initial_metadata is not None:
        metadata = dict(replay_initial_metadata)
    parameters: dict[str, object] = {"metadata": metadata, "loop": True}
    runtime_dependencies = (
        None
        if frames is None
        else InputSourceRuntimeDependencies(replay_frames=tuple(frames))
    )
    selected = (
        replay.build_frames(parameters) if frames is None else tuple(frames)
    )
    return InputSourcePluginRequest(
        parameters=parameters,
        frames=selected,
        loop=True,
        initial_metadata=metadata,
        runtime_dependencies=runtime_dependencies,
    )


_PLUGIN = InputSourcePlugin(
    identity=VersionedIdentity("replay", 1),
    produced_sample_schema=VersionedIdentity("replay_raw_input_frame", 1),
    mode=InputSourceMode.REPLAY,
    factory=replay.build_reader,
    parameter_contract=ParameterContract(
        (ParameterField("metadata", dict), ParameterField("loop", bool))
    ),
    initial_health=InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0),
    initial_metadata={"preset": "r6-h-p5-default"},
)
INPUT_SOURCE_PLUGIN = InputSourcePluginRegistration(
    plugin=_PLUGIN,
    cli_aliases=("replay",),
    request_builder=_request,
    execution_adapter=REPLAY_COMPATIBILITY_EXECUTION_ADAPTER,
)


__all__ = ["INPUT_SOURCE_PLUGIN"]
