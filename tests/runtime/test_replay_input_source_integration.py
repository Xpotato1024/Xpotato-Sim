from __future__ import annotations

import pytest

from xpotato_sim.plugins.input_sources.replay import ReplayInputSource
from xpotato_sim.plugins.mappings.replay_mapping import (
    REPLAY_CONTROL_MAPPING_PLUGIN,
    build_motion_command_from_replay_frame,
)
from xpotato_sim.runtime.control.desired_endpoint_resolver import resolve_desired_endpoint_from_motion_command
from xpotato_sim.schemas import RawInputFrame


def test_replay_fixture_motion_command_resolves_desired_endpoint() -> None:
    frame = RawInputFrame(
        source="replay",
        timestamp_s=1.25,
        metadata={
            "source_kind": "replay",
            "desired_endpoint_m": (0.4, 0.5, 0.6),
            "target_position_m": (9.0, 9.0, 9.0),
        },
    )
    source = ReplayInputSource((frame,))

    replay_frame = source.read_frame()
    intent = REPLAY_CONTROL_MAPPING_PLUGIN.strategy.map_input(replay_frame, {})
    command = build_motion_command_from_replay_frame(
        RawInputFrame(
            source=intent.source,
            timestamp_s=intent.timestamp_s,
            metadata=intent.metadata,
        )
    )

    resolved = resolve_desired_endpoint_from_motion_command(command)

    assert resolved.desired_endpoint_m == (0.4, 0.5, 0.6)
    assert resolved.source == 'MotionCommand.metadata["desired_endpoint_m"]'
    assert command.metadata["target_position_m"] == (9.0, 9.0, 9.0)
    assert command.target is None


def test_replay_fixture_target_position_is_not_primary_command() -> None:
    command = build_motion_command_from_replay_frame(
        RawInputFrame(
            source="replay",
            timestamp_s=0.0,
            metadata={"target_position_m": (1.0, 2.0, 3.0)},
        )
    )

    with pytest.raises(ValueError, match='MotionCommand.metadata\\["desired_endpoint_m"\\] is required'):
        resolve_desired_endpoint_from_motion_command(command)
