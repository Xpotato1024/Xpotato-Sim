from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state

from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

import json
import math
from pathlib import Path

import pytest

from xpotato_sim.mujoco_backend.endpoint_extraction import RuntimeMuJoCoEndpointEvaluation
from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import (
    _build_fast_arm_fk_site_consistency_diagnostic,
    _fast_arm_fk_site_consistency_qpos_fixtures,
    _vector_norm_m,
    build_fast_arm_fk_site_consistency_log_rows,
    run_fast_arm_fk_site_consistency_diagnostics,
    write_fast_arm_fk_site_consistency_log_jsonl,
)


def test_fast_arm_fk_site_consistency_records_include_qpos_fk_endpoint_tip_site_error_vector_and_norm() -> None:
    records = run_fast_arm_fk_site_consistency_diagnostics()

    assert [record.fixture_label for record in records] == [
        "default_qpos",
        "small_positive_perturbation",
        "small_negative_perturbation",
        "representative_endpoint_motion_sanity_qpos",
    ]

    for record in records:
        assert len(record.qpos) == 4
        assert len(record.solver_qpos) == 4
        assert len(record.fk_endpoint_m) == 3
        assert len(record.transformed_solver_fk_world_m) == 3
        assert len(record.mujoco_tip_site_position_m) == 3
        assert len(record.fk_site_error_m) == 3
        assert record.fk_site_error_norm_m == pytest.approx(_vector_norm_m(record.fk_site_error_m), abs=1e-12)
        assert record.status == "pass"
        assert record.reason == "fk_endpoint_matches_tip_site_within_tolerance"
        assert record.site_name == "tip"
        assert len(record.joint_names) == 4
        assert record.solver_qpos[1] == pytest.approx(record.qpos[1] + math.pi / 2.0, abs=1e-12)
        assert record.fk_endpoint_m == pytest.approx(
            record.transformed_solver_fk_world_m,
            abs=1e-12,
        )
        assert record.fk_site_error_m == pytest.approx(
            tuple(
                record.transformed_solver_fk_world_m[index] - record.mujoco_tip_site_position_m[index]
                for index in range(3)
            ),
            abs=1e-12,
        )


def test_fast_arm_fk_site_consistency_default_qpos_fixture_is_deterministic_and_non_default_fixtures_exist() -> None:
    fixtures = _fast_arm_fk_site_consistency_qpos_fixtures()

    assert fixtures[0][0] == "default_qpos"
    simulator = build_fast_arm_simulator()
    assert fixtures[0][1] == pytest.approx(
        tuple(simulator.model.key(FAST_ARM_ROBOT_PROFILE.initial_keyframe_name).qpos),
        abs=1e-12,
    )
    assert any(label != "default_qpos" and qpos != fixtures[0][1] for label, qpos in fixtures)

    diagnostic = _build_fast_arm_fk_site_consistency_diagnostic(
        fixture_label="default_qpos",
        qpos=fixtures[0][1],
    )
    assert diagnostic.qpos == pytest.approx(fixtures[0][1], abs=1e-12)
    assert diagnostic.solver_qpos == pytest.approx(
        (
            fixtures[0][1][0],
            fixtures[0][1][1] + math.pi / 2.0,
            fixtures[0][1][2],
            fixtures[0][1][3],
        ),
        abs=1e-12,
    )
    assert diagnostic.fk_site_error_norm_m <= 1e-9


def test_fast_arm_fk_site_consistency_fixed_fixtures_pass_after_model_aligned_fk_repair() -> None:
    records = run_fast_arm_fk_site_consistency_diagnostics()

    assert max(record.fk_site_error_norm_m for record in records) <= 1e-9
    assert all(record.status == "pass" for record in records)
    assert all(
        record.reason == "fk_endpoint_matches_tip_site_within_tolerance"
        for record in records
    )


def test_fast_arm_fk_site_consistency_tip_site_is_primary_and_body_reference_is_not_treated_as_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    simulator = build_fast_arm_simulator()
    state = simulator.snapshot()

    tip_evaluation = extract_fast_arm_tip_site_endpoint_from_state(state)
    assert tip_evaluation.kind == "site"
    assert tip_evaluation.name == "tip"

    fake_body_reference = RuntimeMuJoCoEndpointEvaluation(
        role="tip",
        kind="body",
        name="fore_arm_link",
        position_m=tip_evaluation.position_m,
    )

    monkeypatch.setattr(
        "xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity.extract_fast_arm_tip_site_endpoint_from_state",
        lambda state: fake_body_reference,
    )

    qpos = tuple(float(value) for value in state.qpos[:4])
    diagnostic = _build_fast_arm_fk_site_consistency_diagnostic(
        fixture_label="default_qpos",
        qpos=qpos,
    )

    assert diagnostic.status == "mismatch"
    assert diagnostic.reason == "tip_site_reference_is_not_primary"


def test_fast_arm_fk_site_consistency_jsonl_export_writes_records(tmp_path: Path) -> None:
    records = run_fast_arm_fk_site_consistency_diagnostics()
    output_path = tmp_path / "fk_site_consistency.jsonl"

    exported_path = write_fast_arm_fk_site_consistency_log_jsonl(records, output_path)

    assert exported_path == output_path
    assert exported_path.exists()

    rows = build_fast_arm_fk_site_consistency_log_rows(records)
    assert len(rows) == len(records)

    lines = exported_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(records)

    first_row = json.loads(lines[0])
    assert first_row["fixture_label"] == "default_qpos"
    assert len(first_row["qpos"]) == 4
    assert len(first_row["solver_qpos"]) == 4
    assert len(first_row["fk_endpoint_m"]) == 3
    assert len(first_row["transformed_solver_fk_world_m"]) == 3
    assert len(first_row["mujoco_tip_site_position_m"]) == 3
    assert len(first_row["fk_site_error_m"]) == 3
    assert first_row["fk_site_error_norm_m"] >= 0.0
