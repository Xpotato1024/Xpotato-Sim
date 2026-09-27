from __future__ import annotations

from collections.abc import Mapping

from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourceMode,
    InputSourcePlugin,
)
from xpotato_sim.runtime.experiment.contracts import (
    ParameterContract,
    VersionedIdentity,
)
from xpotato_sim.schemas import RawInputFrame


CONFORMANCE_INPUT_SOURCE = VersionedIdentity("conformance_input_source", 1)
CONFORMANCE_SAMPLE_SCHEMA = VersionedIdentity("conformance_sample", 1)


class ConformanceInputSourceReader:
    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.parameters = dict(parameters)
        self.started = False
        self.closed = False
        self.read_calls = 0
        self.health_calls = 0
        self.health = InputSourceHealth(InputSourceHealthStatus.ACTIVE)

    def read_frame(self) -> RawInputFrame:
        self.read_calls += 1
        return RawInputFrame(
            source="conformance_input_source",
            timestamp_s=1.0,
            values=(0.25, -0.5),
            metadata={"fixture": "deterministic"},
        )

    def current_health(self) -> InputSourceHealth:
        self.health_calls += 1
        return self.health

    def set_health(self, health: InputSourceHealth) -> None:
        self.health = health


class ManagedConformanceInputSourceReader(ConformanceInputSourceReader):
    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    @property
    def viewer_bridge_capability(self):
        return self

    def ingest_control_message(self, message):
        _ = message
        return self.read_frame()

    def ingest_control_message_json(self, message: str):
        _ = message
        return self.read_frame()

    def record_ingress_failure(self, reason: str) -> None:
        _ = reason

    def rebase_current_endpoint_m(self, endpoint_m) -> None:
        _ = endpoint_m

    def rebind_clock(self, clock) -> None:
        _ = clock


class ReaderWithoutHealth:
    def __init__(self, parameters: Mapping[str, object]) -> None:
        self.parameters = dict(parameters)

    def read_frame(self) -> RawInputFrame:
        return RawInputFrame(source="missing-health", timestamp_s=0.0)


class ReaderWithInvalidHealth(ConformanceInputSourceReader):
    def current_health(self) -> object:
        self.health_calls += 1
        return {"status": "active"}


class ReaderWithInvalidFrame(ConformanceInputSourceReader):
    def __init__(self, parameters: Mapping[str, object], value: object) -> None:
        super().__init__(parameters)
        self.value = value

    def read_frame(self) -> object:
        self.read_calls += 1
        return self.value


class ReaderWithFrameSequence(ConformanceInputSourceReader):
    def __init__(self, parameters: Mapping[str, object], frames: list[object]) -> None:
        super().__init__(parameters)
        self.frames = list(frames)

    def read_frame(self) -> object:
        self.read_calls += 1
        return self.frames.pop(0)


def build_conformance_input_source(
    *,
    mode: InputSourceMode = InputSourceMode.OFFLINE,
    parameter_contract: ParameterContract = ParameterContract(),
    produced_evidence: frozenset[VersionedIdentity] = frozenset(),
    factory_override=None,
) -> InputSourcePlugin:
    def factory(parameters: Mapping[str, object]) -> object:
        if mode in (InputSourceMode.LIVE, InputSourceMode.VIEWER_BRIDGE):
            return ManagedConformanceInputSourceReader(parameters)
        return ConformanceInputSourceReader(parameters)

    return InputSourcePlugin(
        identity=CONFORMANCE_INPUT_SOURCE,
        produced_sample_schema=CONFORMANCE_SAMPLE_SCHEMA,
        mode=mode,
        factory=factory_override or factory,
        parameter_contract=parameter_contract,
        initial_health=InputSourceHealth(InputSourceHealthStatus.ACTIVE),
        initial_metadata={"fixture": "deterministic", "mode": mode.value},
        produced_evidence=produced_evidence,
    )
