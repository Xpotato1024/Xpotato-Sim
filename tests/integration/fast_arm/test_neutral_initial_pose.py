from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

import pytest

import xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.neutral_initial_pose as neutral_initial_pose
from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.neutral_initial_pose import (
    FAST_ARM_INITIAL_STATE_QPOS_RAD,
    FAST_ARM_INITIAL_STATE_TIP_POSITION_M,
    HISTORICAL_RAISED_BASELINE_QPOS_RAD,
    evaluate_fast_arm_neutral_initial_pose_candidates,
    format_neutral_pose_ranking,
    generate_fast_arm_neutral_pose_candidates,
)


def test_candidate_generation_is_deterministic_and_duplicate_free() -> None:
    first = generate_fast_arm_neutral_pose_candidates(
        build_fast_arm_simulator()
    )
    second = generate_fast_arm_neutral_pose_candidates(
        build_fast_arm_simulator()
    )

    assert first == second
    assert first[0].baseline is True
    assert first[0].qpos_rad == pytest.approx(HISTORICAL_RAISED_BASELINE_QPOS_RAD)
    assert len({candidate.qpos_rad for candidate in first}) == len(first)
    assert {candidate.category for candidate in first} >= {
        "baseline",
        "shoulder_lowered",
        "elbow_bent",
        "shoulder_elbow_combined",
        "symmetric_sign_comparison",
    }


def test_evaluator_selects_only_a_lower_bent_valid_candidate() -> None:
    result = evaluate_fast_arm_neutral_initial_pose_candidates()
    by_id = {candidate.candidate_id: candidate for candidate in result.candidates}
    baseline = by_id["historical_raised_baseline"]
    selected = by_id[result.selected_candidate_id]  # type: ignore[index]

    assert result.candidate_count > 20
    assert result.eligible_count > 1
    assert selected.eligible
    assert selected.qpos_rad == pytest.approx(FAST_ARM_INITIAL_STATE_QPOS_RAD)
    assert selected.tip_world_position_m == pytest.approx(
        FAST_ARM_INITIAL_STATE_TIP_POSITION_M
    )
    assert selected.tip_height_m < baseline.tip_height_m
    assert selected.shoulder_to_tip_extension_m < baseline.shoulder_to_tip_extension_m
    assert selected.contact_count == 0
    assert selected.penetration_count == 0
    assert selected.collision_check_available is False
    assert selected.collision_check_reason == "robot_collision_geoms_disabled"
    assert selected.tip_floor_clearance_m == pytest.approx(selected.tip_height_m)
    assert selected.fk_site_residual_m <= result.selection_contract["fk_site_tolerance_m"]
    assert selected.nearby_sensitivity.evaluated_count == 8
    assert len(selected.directions) == 6
    assert {direction.label for direction in selected.directions} == {
        "+X",
        "-X",
        "+Y",
        "-Y",
        "+Z",
        "-Z",
    }
    assert result.selection_contract["rank_three_required"] is False
    assert "six_direction_progressing_count_desc" in result.selection_contract["ranking_order"]


def test_machine_and_human_outputs_are_deterministic() -> None:
    first = evaluate_fast_arm_neutral_initial_pose_candidates()
    second = evaluate_fast_arm_neutral_initial_pose_candidates()

    assert first.to_json() == second.to_json()
    ranking = format_neutral_pose_ranking(first, limit=3)
    assert f"selected={first.selected_candidate_id}" in ranking
    assert "progressing" in ranking


def test_joint_limit_margin_rejects_out_of_range_candidate() -> None:
    margins, minimum, failures = neutral_initial_pose._joint_margins(
        (1.1, 0.0, 0.0, 0.0),
        (
            {"name": "limited", "limited": True, "range": (-1.0, 1.0)},
            {"name": "u1", "limited": False, "range": (0.0, 0.0)},
            {"name": "u2", "limited": False, "range": (0.0, 0.0)},
            {"name": "u3", "limited": False, "range": (0.0, 0.0)},
        ),
    )

    assert margins[0] == 0.0
    assert minimum == 0.0
    assert failures == ("joint_limit:limited",)


def test_contact_and_penetration_candidates_are_hard_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        neutral_initial_pose,
        "_collision_evidence",
        lambda simulator: neutral_initial_pose.CollisionEvidence(
            collision_check_available=True,
            collision_check_reason="robot_collision_geoms_enabled",
            contact_count=1,
            penetration_count=1,
            minimum_contact_distance_m=-0.001,
        ),
    )

    result = evaluate_fast_arm_neutral_initial_pose_candidates()

    assert result.selected_candidate_id is None
    assert result.eligible_count == 0
    assert result.rejection_counts["startup_contact"] == result.candidate_count
    assert result.rejection_counts["startup_penetration"] == result.candidate_count


def test_unavailable_collision_evidence_does_not_reject_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        neutral_initial_pose,
        "_collision_evidence",
        lambda simulator: neutral_initial_pose.CollisionEvidence(
            collision_check_available=False,
            collision_check_reason="robot_collision_geoms_disabled",
            contact_count=0,
            penetration_count=0,
            minimum_contact_distance_m=None,
        ),
    )

    result = evaluate_fast_arm_neutral_initial_pose_candidates()

    assert result.selected_candidate_id is not None
    assert result.eligible_count > 0
    assert all(not candidate.collision_check_available for candidate in result.candidates)
    assert "startup_contact" not in result.rejection_counts


def test_selected_candidate_is_the_canonical_home_startup_and_reset_qpos() -> None:
    result = evaluate_fast_arm_neutral_initial_pose_candidates()
    selected = next(
        candidate
        for candidate in result.candidates
        if candidate.candidate_id == result.selected_candidate_id
    )
    simulator = build_fast_arm_simulator()
    keyframe = simulator.model.key(FAST_ARM_ROBOT_PROFILE.initial_keyframe_name)
    home_qpos = tuple(float(value) for value in keyframe.qpos)

    assert selected.qpos_rad == pytest.approx(home_qpos, rel=0.0, abs=1e-12)
    assert simulator.snapshot().qpos == pytest.approx(home_qpos, rel=0.0, abs=1e-12)

    simulator.data.qpos[:] = HISTORICAL_RAISED_BASELINE_QPOS_RAD
    simulator.reset()

    assert simulator.snapshot().qpos == pytest.approx(home_qpos, rel=0.0, abs=1e-12)
