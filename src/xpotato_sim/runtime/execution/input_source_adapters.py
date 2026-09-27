"""Typed runtime execution semantics selected by input-source registration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from xpotato_sim.runtime.experiment.contracts import VersionedIdentity


class InputSourceExecutionSemantics(str, Enum):
    TARGET_METADATA = "target_metadata"
    REPLAY_COMPATIBILITY = "replay_compatibility"
    VIEWER_LOCAL_ENDPOINT_COMPATIBILITY = "viewer_local_endpoint_compatibility"
    LOADCELL_SOURCE = "loadcell_source"
    ANALOG_FIXTURE_SOURCE = "analog_fixture_source"


@dataclass(frozen=True, slots=True)
class RuntimeInputSourceExecutionAdapter:
    """Explicit execution binding; source IDs are not interpreted by runtime."""

    identity: VersionedIdentity
    semantics: InputSourceExecutionSemantics

    @property
    def annotates_target_position(self) -> bool:
        return self.semantics in (
            InputSourceExecutionSemantics.TARGET_METADATA,
            InputSourceExecutionSemantics.VIEWER_LOCAL_ENDPOINT_COMPATIBILITY,
            InputSourceExecutionSemantics.LOADCELL_SOURCE,
        )

    @property
    def uses_viewer_endpoint_compatibility(self) -> bool:
        return self.semantics is InputSourceExecutionSemantics.VIEWER_LOCAL_ENDPOINT_COMPATIBILITY

    @property
    def uses_replay_pipeline(self) -> bool:
        return self.semantics is InputSourceExecutionSemantics.REPLAY_COMPATIBILITY


TARGET_METADATA_EXECUTION_ADAPTER = RuntimeInputSourceExecutionAdapter(
    VersionedIdentity("target_metadata_input_execution", 1),
    InputSourceExecutionSemantics.TARGET_METADATA,
)
REPLAY_COMPATIBILITY_EXECUTION_ADAPTER = RuntimeInputSourceExecutionAdapter(
    VersionedIdentity("replay_compatibility_input_execution", 1),
    InputSourceExecutionSemantics.REPLAY_COMPATIBILITY,
)
VIEWER_LOCAL_ENDPOINT_EXECUTION_ADAPTER = RuntimeInputSourceExecutionAdapter(
    VersionedIdentity("viewer_local_endpoint_input_execution", 1),
    InputSourceExecutionSemantics.VIEWER_LOCAL_ENDPOINT_COMPATIBILITY,
)
LOADCELL_EXECUTION_ADAPTER = RuntimeInputSourceExecutionAdapter(
    VersionedIdentity("loadcell_input_execution", 1),
    InputSourceExecutionSemantics.LOADCELL_SOURCE,
)
ANALOG_FIXTURE_EXECUTION_ADAPTER = RuntimeInputSourceExecutionAdapter(
    VersionedIdentity("analog_fixture_input_execution", 1),
    InputSourceExecutionSemantics.ANALOG_FIXTURE_SOURCE,
)


__all__ = [
    "ANALOG_FIXTURE_EXECUTION_ADAPTER",
    "InputSourceExecutionSemantics",
    "LOADCELL_EXECUTION_ADAPTER",
    "REPLAY_COMPATIBILITY_EXECUTION_ADAPTER",
    "RuntimeInputSourceExecutionAdapter",
    "TARGET_METADATA_EXECUTION_ADAPTER",
    "VIEWER_LOCAL_ENDPOINT_EXECUTION_ADAPTER",
]
