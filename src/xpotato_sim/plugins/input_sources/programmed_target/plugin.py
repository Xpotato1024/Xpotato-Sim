"""``programmed_target/v1`` のfixed declaration entry point。

deterministic trajectory metadataをtarget adapterへroutingする。importはdeclaration検証
だけで、trajectory再生やproduction runnerへの接続を開始しない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from xpotato_sim.plugins.input_sources import programmed_target
from xpotato_sim.plugins.input_sources.registration import (
    InputSourcePluginRegistration,
    InputSourcePluginRequest,
)
from xpotato_sim.runtime.execution.input_source_adapters import (
    TARGET_METADATA_EXECUTION_ADAPTER,
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
    replay_initial_metadata: Mapping[str, object] | None = None,
    **_: object,
) -> InputSourcePluginRequest:
    _ = replay_initial_metadata
    if type(steps) is not int or steps < 1:
        raise ValueError("steps must be a positive integer")
    if preset not in (None, "sweep_x"):
        raise ValueError("unsupported programmed_target preset")
    if frames is not None:
        raise ValueError(
            "programmed_target input source does not accept custom frames"
        )
    parameters = {
        "steps": steps,
        "initial_position_m": _TARGET_POSITION,
        "preset": "sweep_x",
        "loop": False,
    }
    return InputSourcePluginRequest(
        parameters=parameters,
        frames=programmed_target.build_frames(parameters),
        loop=False,
        initial_metadata={
            "source_kind": "programmed_target",
            "trajectory_name": "sweep_x",
        },
    )


def _adapt_raw_frame(frame: object) -> object:
    if not isinstance(frame, RawInputFrame):
        raise TypeError("replay mapping input adapter requires RawInputFrame")
    return frame


_MAPPING_INPUT_ADAPTER = InputSourceMappingAdapterContract(
    input_schema=VersionedIdentity("programmed_target_sample", 1),
    output_schema=VersionedIdentity("replay_raw_input_frame", 1),
    adapt=_adapt_raw_frame,
)
_PLUGIN = InputSourcePlugin(
    identity=VersionedIdentity("programmed_target", 1),
    produced_sample_schema=VersionedIdentity("programmed_target_sample", 1),
    mode=InputSourceMode.OFFLINE,
    factory=programmed_target.build_reader,
    parameter_contract=ParameterContract(
        (
            ParameterField("steps", int),
            ParameterField("initial_position_m", tuple),
            ParameterField("preset", str, required=False),
            ParameterField("loop", bool, required=False),
        )
    ),
    initial_health=InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0),
    initial_metadata={
        "source_kind": "programmed_target",
        "trajectory_name": "sweep_x",
    },
    mapping_input_adapter=_MAPPING_INPUT_ADAPTER,
)
INPUT_SOURCE_PLUGIN = InputSourcePluginRegistration(
    plugin=_PLUGIN,
    cli_aliases=("programmed_target",),
    request_builder=_request,
    execution_adapter=TARGET_METADATA_EXECUTION_ADAPTER,
)


__all__ = ["INPUT_SOURCE_PLUGIN"]
