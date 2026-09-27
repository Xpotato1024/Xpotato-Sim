from __future__ import annotations

import ast
from pathlib import Path

import pytest

from xpotato_sim.plugins.input_sources.programmed_target import (
    ProgrammedTargetInputSource,
    build_sweep_x_input_source,
    build_sweep_x_trajectory,
)


ROOT = Path(__file__).resolve().parents[4]
PROGRAMMED_TARGET_MODULE = (
    ROOT
    / "src"
    / "xpotato_sim"
    / "plugins"
    / "input_sources"
    / "programmed_target"
    / "source.py"
)


def test_sweep_x_trajectory_is_deterministic_and_phase_annotated() -> None:
    trajectory = build_sweep_x_trajectory()
    source_a = ProgrammedTargetInputSource(trajectory, loop=False)
    source_b = ProgrammedTargetInputSource(build_sweep_x_trajectory(), loop=False)

    frames_a = [source_a.read_frame() for _ in range(len(trajectory.frames))]
    frames_b = [source_b.read_frame() for _ in range(len(trajectory.frames))]

    assert frames_a == frames_b
    assert trajectory.name == "sweep_x"
    assert len(frames_a) == 21

    metadata = [frame.metadata for frame in frames_a]
    phases = [entry["phase"] for entry in metadata]

    assert phases[:3] == ["initial_hold", "initial_hold", "initial_hold"]
    assert phases[3:9] == ["move_positive_x"] * 6
    assert phases[9:12] == ["slow_or_hold_at_positive_x"] * 3
    assert phases[12:18] == ["return_to_initial"] * 6
    assert phases[18:] == ["final_hold"] * 3

    x_positions = [entry["target_position_m"][0] for entry in metadata]
    desired_x_positions = [entry["desired_endpoint_m"][0] for entry in metadata]
    y_positions = [entry["target_position_m"][1] for entry in metadata]
    z_positions = [entry["target_position_m"][2] for entry in metadata]

    assert x_positions[0] == pytest.approx(0.0)
    assert x_positions[3] > x_positions[2]
    assert x_positions[8] == pytest.approx(0.1)
    assert x_positions[12] < x_positions[11]
    assert x_positions[17] == pytest.approx(0.0)
    assert x_positions[18:] == [0.0, 0.0, 0.0]
    assert y_positions == [0.0] * 21
    assert z_positions == [0.0] * 21
    assert desired_x_positions == pytest.approx(x_positions)

    assert metadata[0]["source_kind"] == "programmed_target"
    assert metadata[0]["trajectory_name"] == "sweep_x"
    assert metadata[0]["target_position_m"] == (0.0, 0.0, 0.0)
    assert metadata[0]["desired_endpoint_m"] == (0.0, 0.0, 0.0)
    assert metadata[0]["target_velocity_mps"] == (0.0, 0.0, 0.0)
    assert metadata[0]["t_s"] == 0.0
    assert metadata[0]["frame_index"] == 0
    assert metadata[0]["phase"] == "initial_hold"

    move_frame = metadata[3]
    hold_frame = metadata[9]
    return_frame = metadata[12]
    final_hold_frame = metadata[-1]

    assert move_frame["target_position_m"] == pytest.approx((0.016666666666666666, 0.0, 0.0))
    assert move_frame["target_velocity_mps"] == (0.5, 0.0, 0.0)
    assert move_frame["desired_endpoint_m"] == pytest.approx((0.016666666666666666, 0.0, 0.0))
    assert hold_frame["target_velocity_mps"] == (0.0, 0.0, 0.0)
    assert return_frame["target_velocity_mps"] == (-0.5, 0.0, 0.0)
    assert return_frame["desired_endpoint_m"] == pytest.approx((0.08333333333333334, 0.0, 0.0))
    assert final_hold_frame["phase"] == "final_hold"
    assert final_hold_frame["target_position_m"] == pytest.approx((0.0, 0.0, 0.0))


def test_sweep_x_input_source_loop_false_holds_terminal_frame() -> None:
    source = build_sweep_x_input_source(loop=False)
    trajectory = build_sweep_x_trajectory()

    frames = [source.read_frame() for _ in range(len(trajectory.frames) + 2)]

    assert frames[-1] == frames[-2]
    assert frames[-1].metadata["frame_index"] == len(trajectory.frames) - 1
    assert frames[-1].metadata["phase"] == "final_hold"


def test_sweep_x_input_source_loop_true_repeats_the_sequence() -> None:
    source = build_sweep_x_input_source(loop=True)
    trajectory = build_sweep_x_trajectory()

    frames = [source.read_frame() for _ in range(len(trajectory.frames) + 1)]

    assert frames[0] == frames[-1]
    assert frames[-1].metadata["frame_index"] == 0
    assert frames[-1].metadata["phase"] == "initial_hold"


def test_sweep_x_programmed_target_module_does_not_import_noop_motion_generator() -> None:
    source_text = PROGRAMMED_TARGET_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source_text, filename=str(PROGRAMMED_TARGET_MODULE))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module != "tests.support.motion_doubles"
            imported_names = {alias.name for alias in node.names}
            assert "NoOpMotionGenerator" not in imported_names
        elif isinstance(node, ast.Import):
            imported_names = {alias.name for alias in node.names}
            assert "tests.support.motion_doubles" not in imported_names

    assert "NoOpMotionGenerator" not in source_text
