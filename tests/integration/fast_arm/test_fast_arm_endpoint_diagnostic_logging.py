from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import run_fast_arm_endpoint_motion_sanity
from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import (
    _endpoint_error_vector_m,
    _vector_norm_m,
    build_fast_arm_endpoint_diagnostic_log_rows,
    write_fast_arm_endpoint_diagnostic_log_jsonl,
)


def test_endpoint_diagnostic_log_rows_include_desired_endpoint_actual_tip_error_norm_and_step_index() -> None:
    results = run_fast_arm_endpoint_motion_sanity()
    rows = build_fast_arm_endpoint_diagnostic_log_rows(results)

    assert [row["step_index"] for row in rows] == [1, 2, 3, 4, 5, 6]
    assert len(rows) == 6

    for result, row in zip(results, rows, strict=True):
        assert len(row["desired_endpoint_m"]) == 3
        assert len(row["actual_tip_position_m"]) == 3
        assert len(row["endpoint_error_m"]) == 3
        assert len(row["qpos_before"]) == 4
        assert len(row["qpos_after"]) == 4
        assert row["desired_endpoint_m"] == result.desired_endpoint_m
        assert row["actual_tip_position_m"] == result.final_tip_position_m
        assert row["base_endpoint_source"] in {"initial_tip", "explicit", "unavailable"}
        assert row["desired_endpoint_source"] != ""
        assert row["status"] in {"pass", "rejected", "limitation", "unavailable"}
        assert row["reason"]
        assert row["endpoint_error_norm_m"] == pytest.approx(
            _vector_norm_m(row["endpoint_error_m"]),
            abs=1e-12,
        )


def test_endpoint_diagnostic_error_norm_uses_desired_endpoint_even_if_target_position_differs() -> None:
    results = run_fast_arm_endpoint_motion_sanity()
    mutated_result = replace(
        results[0],
        target_position_m=(9.0, 8.0, 7.0),
    )

    row = build_fast_arm_endpoint_diagnostic_log_rows((mutated_result,))[0]

    assert row["desired_endpoint_m"] == mutated_result.desired_endpoint_m
    assert row["desired_endpoint_m"] != mutated_result.target_position_m
    assert row["endpoint_error_m"] == _endpoint_error_vector_m(
        mutated_result.desired_endpoint_m,
        mutated_result.final_tip_position_m,
    )


def test_endpoint_diagnostic_error_norm_simple_fixture() -> None:
    error_vector_m = _endpoint_error_vector_m((0.5, 0.4, 0.3), (0.2, 0.0, 0.1))

    assert error_vector_m == pytest.approx((0.3, 0.4, 0.2), abs=1e-12)
    assert _vector_norm_m(error_vector_m) == pytest.approx(math.sqrt(0.3**2 + 0.4**2 + 0.2**2), abs=1e-12)


def test_endpoint_diagnostic_jsonl_export_writes_records(tmp_path: Path) -> None:
    results = run_fast_arm_endpoint_motion_sanity()
    output_path = tmp_path / "endpoint_diagnostics.jsonl"

    exported_path = write_fast_arm_endpoint_diagnostic_log_jsonl(results, output_path)

    assert exported_path == output_path
    assert exported_path.exists()

    lines = exported_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6

    first_row = json.loads(lines[0])
    assert first_row["step_index"] == 1
    assert len(first_row["desired_endpoint_m"]) == 3
    assert len(first_row["actual_tip_position_m"]) == 3
    assert first_row["endpoint_error_norm_m"] >= 0.0
