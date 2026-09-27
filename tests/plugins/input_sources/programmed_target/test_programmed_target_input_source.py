from __future__ import annotations

from xpotato_sim.plugins.input_sources.programmed_target import (
    ProgrammedTargetFrame,
    ProgrammedTargetInputSource,
    ProgrammedTargetTrajectory,
)
from xpotato_sim.schemas import RawInputFrame


def _build_trajectory() -> ProgrammedTargetTrajectory:
    return ProgrammedTargetTrajectory(
        name="linear_target",
        frames=(
            ProgrammedTargetFrame(
                t_s=0.0,
                target_position_m=(0.1, 0.2, 0.3),
                desired_endpoint_m=(0.1, 0.2, 0.3),
                target_velocity_mps=(0.01, 0.0, 0.0),
            ),
            ProgrammedTargetFrame(
                t_s=0.5,
                target_position_m=(0.4, 0.5, 0.6),
                desired_endpoint_m=(0.4, 0.5, 0.6),
            ),
        ),
    )


def test_programmed_target_input_source_emits_programmed_target_metadata() -> None:
    source = ProgrammedTargetInputSource(_build_trajectory(), loop=False)

    first_frame = source.read_frame()
    second_frame = source.read_frame()

    assert isinstance(first_frame, RawInputFrame)
    assert first_frame.source == "programmed_target"
    assert first_frame.timestamp_s == 0.0
    assert first_frame.metadata["source_kind"] == "programmed_target"
    assert first_frame.metadata["trajectory_name"] == "linear_target"
    assert first_frame.metadata["target_position_m"] == (0.1, 0.2, 0.3)
    assert first_frame.metadata["desired_endpoint_m"] == (0.1, 0.2, 0.3)
    assert first_frame.metadata["target_velocity_mps"] == (0.01, 0.0, 0.0)
    assert first_frame.metadata["t_s"] == 0.0
    assert first_frame.metadata["frame_index"] == 0

    assert second_frame.metadata["source_kind"] == "programmed_target"
    assert second_frame.metadata["trajectory_name"] == "linear_target"
    assert second_frame.metadata["target_position_m"] == (0.4, 0.5, 0.6)
    assert second_frame.metadata["desired_endpoint_m"] == (0.4, 0.5, 0.6)
    assert "target_velocity_mps" not in second_frame.metadata
    assert second_frame.metadata["t_s"] == 0.5
    assert second_frame.metadata["frame_index"] == 1


def test_programmed_target_input_source_returns_terminal_frame_after_eof() -> None:
    source = ProgrammedTargetInputSource(_build_trajectory(), loop=False)

    first_frame = source.read_frame()
    second_frame = source.read_frame()
    terminal_frame = source.read_frame()
    repeated_terminal_frame = source.read_frame()

    assert terminal_frame == second_frame
    assert repeated_terminal_frame == second_frame
    assert terminal_frame.metadata["frame_index"] == 1
    assert repeated_terminal_frame.metadata["frame_index"] == 1
    assert terminal_frame.metadata["trajectory_name"] == "linear_target"
    assert first_frame.metadata["frame_index"] == 0


def test_programmed_target_input_source_loops_back_to_start() -> None:
    source = ProgrammedTargetInputSource(_build_trajectory(), loop=True)

    frames = [source.read_frame() for _ in range(3)]

    assert [frame.metadata["frame_index"] for frame in frames] == [0, 1, 0]
    assert [frame.metadata["trajectory_name"] for frame in frames] == ["linear_target", "linear_target", "linear_target"]
    assert frames[2].metadata["target_position_m"] == (0.1, 0.2, 0.3)
