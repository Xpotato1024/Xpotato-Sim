"""Test-only source registration used to prove zero-core-change onboarding."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from xpotato_sim.plugins.input_sources.catalog import InputSourceCatalog
from xpotato_sim.plugins.input_sources.registration import (
    InputSourcePluginRegistration,
    InputSourcePluginRequest,
)
from xpotato_sim.runtime.execution.input_source_adapters import (
    InputSourceExecutionSemantics,
    RuntimeInputSourceExecutionAdapter,
)
from xpotato_sim.runtime.experiment.contracts import (
    ParameterContract,
    ParameterField,
    PluginSelection,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourceMode,
    InputSourcePlugin,
)
from xpotato_sim.schemas import InputIntent, RawInputFrame


DUMMY_INPUT_SOURCE_ID = VersionedIdentity("test_dummy_input_source", 1)
DUMMY_SAMPLE_SCHEMA = VersionedIdentity("test_dummy_sample", 1)
DUMMY_MAPPING_ID = PluginSelection("test_dummy_mapping", 1)
DUMMY_PARAMETER_CONTRACT = ParameterContract(
    (ParameterField("amplitude", float),)
)


@dataclass(frozen=True, slots=True)
class DummySourceParameters:
    amplitude: float


class DummyInputSourceReader:
    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.parameters = DummySourceParameters(float(parameters["amplitude"]))

    def read_frame(self) -> RawInputFrame:
        return RawInputFrame(
            source=DUMMY_INPUT_SOURCE_ID.name,
            timestamp_s=0.0,
            values=(self.parameters.amplitude,),
            metadata={"source_kind": DUMMY_INPUT_SOURCE_ID.name},
        )

    def current_health(self) -> InputSourceHealth:
        return InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0)


def _build_reader(parameters: Mapping[str, object]) -> DummyInputSourceReader:
    return DummyInputSourceReader(parameters)


def _build_request(
    *,
    steps: int,
    frames,
    preset: str | None,
    **_: object,
) -> InputSourcePluginRequest:
    if steps < 1 or frames is not None or preset is not None:
        raise ValueError("test dummy input source accepts only its typed fixture request")
    parameters = {"amplitude": 0.25}
    frame = DummyInputSourceReader(parameters).read_frame()
    return InputSourcePluginRequest(
        parameters=parameters,
        frames=(frame,),
        loop=True,
        initial_metadata={"source_kind": DUMMY_INPUT_SOURCE_ID.name},
    )


DUMMY_PLUGIN = InputSourcePlugin(
    identity=DUMMY_INPUT_SOURCE_ID,
    produced_sample_schema=DUMMY_SAMPLE_SCHEMA,
    mode=InputSourceMode.OFFLINE,
    factory=_build_reader,
    parameter_contract=DUMMY_PARAMETER_CONTRACT,
    initial_health=InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0),
    initial_metadata={"source_kind": DUMMY_INPUT_SOURCE_ID.name},
)

DUMMY_EXECUTION_ADAPTER = RuntimeInputSourceExecutionAdapter(
    VersionedIdentity("test_dummy_input_execution", 1),
    InputSourceExecutionSemantics.REPLAY_COMPATIBILITY,
)

DUMMY_REGISTRATION = InputSourcePluginRegistration(
    plugin=DUMMY_PLUGIN,
    cli_aliases=("test_dummy_input",),
    request_builder=_build_request,
    execution_adapter=DUMMY_EXECUTION_ADAPTER,
)

DUMMY_CATALOG = InputSourceCatalog((DUMMY_REGISTRATION,))


class DummyCompatibleMapping:
    mapping_semantics_identity = VersionedIdentity("dummy_mapping_semantics", 1)

    def map_input(self, input_intent: object, parameters: Mapping[str, object]) -> InputIntent:
        if isinstance(input_intent, RawInputFrame):
            return InputIntent(
                source=input_intent.source,
                timestamp_s=input_intent.timestamp_s,
                values=input_intent.values,
                metadata=input_intent.metadata,
            )
        raise TypeError("test dummy mapping requires RawInputFrame")


__all__ = [
    "DUMMY_CATALOG",
    "DUMMY_EXECUTION_ADAPTER",
    "DUMMY_INPUT_SOURCE_ID",
    "DUMMY_MAPPING_ID",
    "DUMMY_PARAMETER_CONTRACT",
    "DUMMY_PLUGIN",
    "DUMMY_REGISTRATION",
    "DUMMY_SAMPLE_SCHEMA",
    "DummyCompatibleMapping",
    "DummyInputSourceReader",
    "DummySourceParameters",
]
