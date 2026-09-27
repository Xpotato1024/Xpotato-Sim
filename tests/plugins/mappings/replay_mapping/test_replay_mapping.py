from __future__ import annotations

from xpotato_sim.plugins.input_sources.programmed_target import (
    build_sweep_x_input_source,
)
from xpotato_sim.plugins.mappings.replay_mapping import (
    REPLAY_CONTROL_MAPPING_PLUGIN,
)
from xpotato_sim.schemas import InputIntent, RawInputFrame


def test_replay_mapping_preserves_raw_frame_fields_with_shallow_metadata_copy() -> None:
    nested = {"count": 1}
    frame = RawInputFrame(
        source="replay",
        timestamp_s=2.0,
        values=(1.0, -1.0),
        buttons=("primary",),
        metadata={"preset": "unit", "nested": nested},
    )

    intent = REPLAY_CONTROL_MAPPING_PLUGIN.strategy.map_input(frame, {})

    assert isinstance(intent, InputIntent)
    assert intent.source == frame.source
    assert intent.timestamp_s == frame.timestamp_s
    assert intent.values == frame.values
    assert intent.buttons == frame.buttons
    assert intent.metadata == frame.metadata
    assert intent.metadata is not frame.metadata
    assert intent.metadata["nested"] is nested
    assert not hasattr(intent, "target_position_m")
    assert not hasattr(intent, "joint_angles_rad")


def test_programmed_target_metadata_is_preserved_by_canonical_replay_mapping() -> None:
    frame = build_sweep_x_input_source().read_frame()

    intent = REPLAY_CONTROL_MAPPING_PLUGIN.strategy.map_input(frame, {})

    assert intent.metadata == frame.metadata
    assert intent.metadata is not frame.metadata
    assert intent.metadata["source_kind"] == "programmed_target"
    assert intent.metadata["trajectory_name"] == "sweep_x"
    assert intent.metadata["target_position_m"] == (0.0, 0.0, 0.0)
    assert intent.metadata["desired_endpoint_m"] == (0.0, 0.0, 0.0)
    assert intent.metadata["target_velocity_mps"] == (0.0, 0.0, 0.0)
    assert intent.metadata["t_s"] == 0.0
    assert intent.metadata["frame_index"] == 0
    assert intent.metadata["phase"] == "initial_hold"
