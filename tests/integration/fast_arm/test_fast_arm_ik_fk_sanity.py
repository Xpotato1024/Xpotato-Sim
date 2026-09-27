from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

import json
from pathlib import Path

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import (
    FastArmIkFkSanityDiagnostic,
    _fast_arm_ik_fk_sanity_target_fixtures,
    _ik_fk_error_vector_m,
    _vector_norm_m,
    build_fast_arm_ik_fk_sanity_log_rows,
    run_fast_arm_ik_fk_sanity_diagnostics,
    write_fast_arm_ik_fk_sanity_log_jsonl,
)


def test_fast_arm_ik_fk_sanity_records_include_target_endpoint_ik_input_fk_output_error_and_known_context() -> None:
    records = run_fast_arm_ik_fk_sanity_diagnostics()

    assert [record.fixture_label for record in records] == [
        "default_tip_position",
        "small_positive_x_target",
        "small_positive_z_target",
        "representative_endpoint_motion_sanity_target",
    ]

    for record in records:
        assert len(record.target_endpoint_m) == 3
        assert len(record.ik_input_target_m) == 3
        assert record.known_fk_site_consistency_status == "pass"
        assert (
            record.known_fk_site_consistency_note
            == "fk_site_consistency_repaired_with_mujoco_model_aligned_fk"
        )
        assert "reachability_unverified" in record.reason
        assert record.status in {"pass", "mismatch", "ik_failed", "diagnostic_only"}
        if record.ik_status == "solved":
            assert record.ik_output_qpos is not None
            assert len(record.ik_output_qpos) == 4
            assert record.fk_endpoint_from_ik_qpos_m is not None
            assert len(record.fk_endpoint_from_ik_qpos_m) == 3
            assert record.ik_fk_error_m is not None
            assert len(record.ik_fk_error_m) == 3
            assert record.ik_fk_error_norm_m == pytest.approx(
                _vector_norm_m(record.ik_fk_error_m),
                abs=1e-12,
            )
        else:
            assert record.ik_status == "failed"
            assert record.status == "ik_failed"
            assert record.ik_output_qpos is None
            assert record.fk_endpoint_from_ik_qpos_m is None
            assert record.ik_fk_error_m is None
            assert record.ik_fk_error_norm_m is None


def test_fast_arm_ik_fk_sanity_target_fixture_helper_uses_default_tip_position_and_is_deterministic() -> None:
    simulator = build_fast_arm_simulator()
    default_tip_position_m = extract_fast_arm_tip_site_endpoint_from_state(simulator.snapshot()).position_m

    fixtures = _fast_arm_ik_fk_sanity_target_fixtures()

    assert [label for label, _, _, _ in fixtures] == [
        "default_tip_position",
        "small_positive_x_target",
        "small_positive_z_target",
        "representative_endpoint_motion_sanity_target",
    ]
    assert fixtures[0][1] == pytest.approx(default_tip_position_m, abs=1e-9)
    for _, _, seed_qpos, fixture_note in fixtures:
        assert len(seed_qpos) == 4
        assert "reachability_unverified" in fixture_note


def test_fast_arm_ik_fk_sanity_error_norm_simple_fixture() -> None:
    error_vector_m = _ik_fk_error_vector_m((0.5, 0.4, 0.3), (0.2, 0.0, 0.1))

    assert error_vector_m == pytest.approx((0.3, 0.4, 0.2), abs=1e-12)
    assert _vector_norm_m(error_vector_m) == pytest.approx((0.3**2 + 0.4**2 + 0.2**2) ** 0.5, abs=1e-12)


def test_fast_arm_ik_fk_sanity_row_builder_keeps_success_fields_visible() -> None:
    diagnostic = FastArmIkFkSanityDiagnostic(
        fixture_label="synthetic_pass",
        target_endpoint_m=(0.5, 0.4, 0.3),
        ik_input_target_m=(0.5, 0.4, 0.3),
        ik_output_qpos=(0.1, -0.2, 0.3, -0.4),
        fk_endpoint_from_ik_qpos_m=(0.5, 0.4, 0.3),
        ik_fk_error_m=(0.0, 0.0, 0.0),
        ik_fk_error_norm_m=0.0,
        ik_status="solved",
        status="pass",
        reason="ik_solved_and_target_vs_fk_within_tolerance; reachability_unverified",
        known_fk_site_consistency_status="pass",
        known_fk_site_consistency_note="fk_site_consistency_repaired_with_mujoco_model_aligned_fk",
        seed_qpos=(0.0, 0.0, 0.0, 0.0),
        joint_names=("joint_a", "joint_b", "joint_c", "joint_d"),
        model_path="model.xml",
    )

    row = build_fast_arm_ik_fk_sanity_log_rows((diagnostic,))[0]

    assert row["fixture_label"] == "synthetic_pass"
    assert row["target_endpoint_m"] == (0.5, 0.4, 0.3)
    assert row["ik_input_target_m"] == (0.5, 0.4, 0.3)
    assert row["ik_output_qpos"] == (0.1, -0.2, 0.3, -0.4)
    assert row["fk_endpoint_from_ik_qpos_m"] == (0.5, 0.4, 0.3)
    assert row["ik_fk_error_m"] == (0.0, 0.0, 0.0)
    assert row["ik_fk_error_norm_m"] == 0.0
    assert row["ik_status"] == "solved"
    assert row["status"] == "pass"
    assert row["known_fk_site_consistency_status"] == "pass"
    assert row["seed_qpos"] == (0.0, 0.0, 0.0, 0.0)
    assert row["joint_names"] == ("joint_a", "joint_b", "joint_c", "joint_d")
    assert row["model_path"] == "model.xml"


def test_fast_arm_ik_fk_sanity_jsonl_export_writes_records(tmp_path: Path) -> None:
    records = run_fast_arm_ik_fk_sanity_diagnostics()
    output_path = tmp_path / "ik_fk_sanity.jsonl"

    exported_path = write_fast_arm_ik_fk_sanity_log_jsonl(records, output_path)

    assert exported_path == output_path
    assert exported_path.exists()

    lines = exported_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(records)

    first_row = json.loads(lines[0])
    assert first_row["fixture_label"] == "default_tip_position"
    assert len(first_row["target_endpoint_m"]) == 3
    assert len(first_row["ik_input_target_m"]) == 3
    assert first_row["known_fk_site_consistency_status"] == "pass"
