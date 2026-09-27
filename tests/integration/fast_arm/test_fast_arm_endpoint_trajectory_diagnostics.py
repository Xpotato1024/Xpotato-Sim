from __future__ import annotations

import csv
from pathlib import Path

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import run_fast_arm_endpoint_trajectory_diagnostics
from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import (
    write_fast_arm_endpoint_trajectory_log_csv,
)


def test_endpoint_trajectory_diagnostics_cover_all_axis_commands() -> None:
    diagnostics = run_fast_arm_endpoint_trajectory_diagnostics(
        trajectory_steps=5,
        trajectory_delta_m=0.005,
    )

    assert [result.command_label for result in diagnostics] == ["+x", "-x", "+y", "-y", "+z", "-z"]
    for result in diagnostics:
        assert result.step_count == 5
        assert len(result.initial_tip_position_m) == 3
        assert len(result.initial_qpos) == 4
        assert len(result.records) == 5
        assert result.summary.step_count == 5
        assert result.summary.saturation_step is None or result.summary.saturation_step >= 1
        assert result.summary.first_rejection_step is None or result.summary.first_rejection_step >= 1
        assert result.summary.first_off_plane_step is None or result.summary.first_off_plane_step >= 1
        assert result.summary.safe_hold_step is None or result.summary.safe_hold_step >= 1
        for record in result.records:
            assert len(record.desired_endpoint_m) == 3
            assert len(record.qpos_before) == 4
            assert len(record.qpos_after) == 4
            assert len(record.tip_before_m) == 3
            assert len(record.tip_after_m) == 3
            assert len(record.actual_delta_m) == 3
            assert len(record.cumulative_actual_delta_m) == 3
            assert record.status in {"pass", "limitation", "rejected", "unavailable"}
            assert record.reason


def test_xy_trajectory_records_dof_allocation_limitation_without_rejection() -> None:
    diagnostics = {
        result.command_label: result
        for result in run_fast_arm_endpoint_trajectory_diagnostics(
            trajectory_steps=5,
            trajectory_delta_m=0.005,
        )
    }

    for label in ["+x", "-x", "+y", "-y"]:
        summary = diagnostics[label].summary
        assert summary.final_status == "limitation"
        assert summary.final_reason == "off_plane"
        assert summary.first_off_plane_step == 1
        assert summary.first_rejection_step is None
        assert summary.safe_hold_step is None
        assert summary.decision == "current_solver_dof_allocation_limitation"
        assert all(record.target_rejected is False for record in diagnostics[label].records)


def test_z_trajectory_starts_aligned_then_degrades_under_repeated_commands() -> None:
    diagnostics = {
        result.command_label: result
        for result in run_fast_arm_endpoint_trajectory_diagnostics(
            trajectory_steps=5,
            trajectory_delta_m=0.005,
        )
    }

    plus_z = diagnostics["+z"]
    assert plus_z.records[0].status == "pass"
    assert plus_z.records[0].reason == "aligned"
    assert plus_z.summary.first_rejection_step is None
    assert plus_z.summary.first_opposite_direction_step == 4
    assert plus_z.summary.final_status == "limitation"
    assert plus_z.summary.final_reason == "opposite_direction"

    minus_z = diagnostics["-z"]
    assert minus_z.records[0].status == "limitation"
    assert minus_z.records[0].reason == "opposite_direction"
    assert minus_z.summary.first_rejection_step is None
    assert minus_z.summary.first_opposite_direction_step == 1
    assert minus_z.summary.final_status == "limitation"
    assert minus_z.summary.final_reason == "opposite_direction"
    assert plus_z.summary.decision == minus_z.summary.decision == "z_primary_but_degrades_over_repeated_commands"


def test_trajectory_summary_contains_cumulative_drift_metrics() -> None:
    diagnostics = run_fast_arm_endpoint_trajectory_diagnostics(
        trajectory_steps=5,
        trajectory_delta_m=0.005,
    )

    for result in diagnostics:
        summary = result.summary
        assert len(summary.cumulative_commanded_delta_m) == 3
        assert len(summary.cumulative_actual_delta_m) == 3
        assert summary.mean_direction_dot is not None
        assert summary.orthogonal_drift_m >= 0.0
        assert summary.drift_from_command_axis_m != 0.0


def test_trajectory_csv_export_creates_parent_directories_and_required_fields(tmp_path: Path) -> None:
    diagnostics = run_fast_arm_endpoint_trajectory_diagnostics(
        trajectory_steps=3,
        trajectory_delta_m=0.005,
    )
    output_path = tmp_path / "nested" / "presentation" / "trajectory_log.csv"

    exported_path = write_fast_arm_endpoint_trajectory_log_csv(diagnostics, output_path)

    assert exported_path == output_path
    assert exported_path.exists()
    assert exported_path.parent.exists()

    with exported_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == len(diagnostics) * 3
    first_row = rows[0]
    for column in [
        "step",
        "time_s",
        "dt_s",
        "command_axis",
        "target_x_m",
        "target_y_m",
        "target_z_m",
        "tip_x_m",
        "tip_y_m",
        "tip_z_m",
        "error_x_m",
        "error_y_m",
        "error_z_m",
        "error_norm_m",
        "status",
        "reason",
    ]:
        assert column in first_row
        assert first_row[column] != ""


def test_trajectory_csv_export_records_target_tip_and_error_values(tmp_path: Path) -> None:
    diagnostics = run_fast_arm_endpoint_trajectory_diagnostics(
        trajectory_steps=1,
        trajectory_delta_m=0.005,
    )
    output_path = tmp_path / "trajectory_log.csv"

    write_fast_arm_endpoint_trajectory_log_csv(diagnostics, output_path)

    with output_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    z_rows = [row for row in rows if row["command_axis"] == "z"]
    assert z_rows
    row = z_rows[0]
    error_norm = float(row["error_norm_m"])
    assert error_norm >= 0.0
    assert row["status"]
    assert row["reason"]
    assert row["target_x_m"] != row["tip_x_m"] or row["target_y_m"] != row["tip_y_m"] or row["target_z_m"] != row["tip_z_m"]
