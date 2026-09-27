"""Canonical replay_mapping/v1 implementation."""

from __future__ import annotations

from collections.abc import Mapping

from xpotato_sim.plugins.mappings._command_routes import (
    joint_position_command_route,
)
from xpotato_sim.runtime.experiment.contracts import (
    ControlMappingPlugin,
    REPLAY_COMMAND_TO_JOINT_POSITION_V1,
    VersionedIdentity,
)
from xpotato_sim.schemas import InputIntent, MotionCommand, RawInputFrame


REPLAY_SAMPLE_SCHEMA = VersionedIdentity("replay_raw_input_frame", 1)
REPLAY_MAPPING_IDENTITY = VersionedIdentity("replay_mapping", 1)
REPLAY_MAPPING_SEMANTICS_IDENTITY = VersionedIdentity("replay_metadata_command", 1)


def build_input_intent_from_replay_frame(frame: RawInputFrame) -> InputIntent:
    return InputIntent(
        source=frame.source,
        timestamp_s=frame.timestamp_s,
        values=frame.values,
        buttons=frame.buttons,
        metadata=dict(frame.metadata),
    )


def build_motion_command_from_replay_frame(frame: RawInputFrame) -> MotionCommand:
    return MotionCommand(
        timestamp_s=frame.timestamp_s,
        metadata=dict(frame.metadata),
    )


class ReplayMappingStrategy:
    mapping_semantics_identity = REPLAY_MAPPING_SEMANTICS_IDENTITY

    def map_input(self, input_intent: object, parameters: Mapping[str, object]) -> InputIntent:
        _ = parameters
        if not isinstance(input_intent, RawInputFrame):
            raise TypeError("replay mapping requires RawInputFrame")
        return build_input_intent_from_replay_frame(input_intent)


REPLAY_CONTROL_MAPPING_PLUGIN = ControlMappingPlugin(
    identity=REPLAY_MAPPING_IDENTITY,
    strategy=ReplayMappingStrategy(),
    accepted_input_sample_schemas=frozenset({REPLAY_SAMPLE_SCHEMA}),
    mapping_semantics_identity=REPLAY_MAPPING_SEMANTICS_IDENTITY,
    command_semantics_routes=frozenset(
        {
            joint_position_command_route(
                route_identity=REPLAY_COMMAND_TO_JOINT_POSITION_V1,
                control_semantics_identity=REPLAY_MAPPING_SEMANTICS_IDENTITY,
            )
        }
    ),
)


__all__ = [
    "REPLAY_CONTROL_MAPPING_PLUGIN",
    "REPLAY_MAPPING_IDENTITY",
    "REPLAY_MAPPING_SEMANTICS_IDENTITY",
    "REPLAY_SAMPLE_SCHEMA",
    "ReplayMappingStrategy",
    "build_input_intent_from_replay_frame",
    "build_motion_command_from_replay_frame",
]
