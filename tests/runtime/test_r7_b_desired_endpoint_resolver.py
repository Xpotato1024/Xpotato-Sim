from __future__ import annotations

import math

import pytest

from xpotato_sim.plugins.input_sources.selfrionette import NormalizedLoadcellInputIntent
from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping import LoadcellEndpointMotionCommandConverter
from xpotato_sim.runtime.control.desired_endpoint_resolver import (
    ResolvedDesiredEndpoint,
    resolve_desired_endpoint_from_motion_command,
)
from xpotato_sim.schemas import MotionCommand, TargetCommand


def test_resolve_desired_endpoint_from_motion_command_uses_command_metadata() -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        metadata={
            "desired_endpoint_m": [0.1, 0.2, 0.3],
            "source_kind": "programmed_target",
        },
    )

    resolved = resolve_desired_endpoint_from_motion_command(command)

    assert isinstance(resolved, ResolvedDesiredEndpoint)
    assert resolved.desired_endpoint_m == (0.1, 0.2, 0.3)
    assert isinstance(resolved.desired_endpoint_m, tuple)
    assert resolved.source == 'MotionCommand.metadata["desired_endpoint_m"]'
    assert resolved.metadata == {
        "desired_endpoint_m": [0.1, 0.2, 0.3],
        "source_kind": "programmed_target",
    }


@pytest.mark.parametrize(
    ("desired_endpoint_m", "match"),
    [
        ((0.1, 0.2), "must contain exactly three values"),
        ((0.1, 0.2, 0.3, 0.4), "must contain exactly three values"),
        ((0.1, math.nan, 0.3), "must contain only finite values at index 1"),
        ((0.1, math.inf, 0.3), "must contain only finite values at index 1"),
        (("bad", 0.2, 0.3), "must contain numeric values"),
    ],
)
def test_resolve_desired_endpoint_from_motion_command_rejects_malformed_desired_endpoint(
    desired_endpoint_m: tuple[float, ...] | tuple[object, ...],
    match: str,
) -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        metadata={"desired_endpoint_m": desired_endpoint_m},
    )

    with pytest.raises(ValueError, match=match):
        resolve_desired_endpoint_from_motion_command(command)


def test_resolve_desired_endpoint_from_motion_command_rejects_missing_desired_endpoint_by_default() -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        target=TargetCommand(position_m=(0.4, 0.5, 0.6)),
        metadata={"target_position_m": (0.7, 0.8, 0.9)},
    )

    with pytest.raises(ValueError, match='MotionCommand.metadata\\["desired_endpoint_m"\\] is required'):
        resolve_desired_endpoint_from_motion_command(command)


def test_resolve_desired_endpoint_from_motion_command_rejects_malformed_metadata() -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        metadata=[],
    )

    with pytest.raises(ValueError, match="MotionCommand.metadata must be a mapping"):
        resolve_desired_endpoint_from_motion_command(command)


def test_resolve_desired_endpoint_from_motion_command_uses_target_position_fallback_only_when_explicitly_enabled() -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        target=TargetCommand(position_m=(0.4, 0.5, 0.6)),
        metadata={"target_position_m": (0.7, 0.8, 0.9)},
    )

    with pytest.raises(ValueError, match='MotionCommand.metadata\\["desired_endpoint_m"\\] is required'):
        resolve_desired_endpoint_from_motion_command(command, allow_target_position_fallback=False)

    resolved = resolve_desired_endpoint_from_motion_command(command, allow_target_position_fallback=True)

    assert resolved.desired_endpoint_m == (0.4, 0.5, 0.6)
    assert resolved.source == "MotionCommand.target.position_m"


def test_resolve_desired_endpoint_from_motion_command_uses_metadata_target_position_fallback_when_explicitly_enabled() -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        metadata={"target_position_m": (0.7, 0.8, 0.9)},
    )

    resolved = resolve_desired_endpoint_from_motion_command(command, allow_target_position_fallback=True)

    assert resolved.desired_endpoint_m == (0.7, 0.8, 0.9)
    assert resolved.source == 'MotionCommand.metadata["target_position_m"]'


def test_resolve_desired_endpoint_from_loadcell_motion_command_output() -> None:
    converter = LoadcellEndpointMotionCommandConverter()
    intent = NormalizedLoadcellInputIntent(
        source="selfrionette",
        timestamp_s=2.0,
        values=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        metadata={"source_kind": "selfrionette"},
    )
    command = converter.convert(intent, current_tip_position_m=(0.1, 0.2, 0.3))

    resolved = resolve_desired_endpoint_from_motion_command(command)

    assert resolved.desired_endpoint_m == (0.1, 0.2, 0.3)
    assert resolved.source == 'MotionCommand.metadata["desired_endpoint_m"]'
    assert resolved.metadata["source_kind"] == "selfrionette"
