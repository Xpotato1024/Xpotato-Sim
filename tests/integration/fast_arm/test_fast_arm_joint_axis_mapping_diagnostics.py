from __future__ import annotations

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import (
    run_fast_arm_endpoint_motion_sanity,
    run_fast_arm_joint_axis_mapping_diagnostics,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE


def _results_by_label():
    return {result.command_label: result for result in run_fast_arm_endpoint_motion_sanity()}


def test_joint_axis_mapping_diagnostics_cover_all_fast_arm_qpos() -> None:
    results = run_fast_arm_joint_axis_mapping_diagnostics()

    assert [result.joint_name for result in results] == [
        "sholder_joint_1",
        "sholder_joint_2",
        "sholder_joint_3",
        "elbow_joint",
    ]
    assert [result.qpos_index for result in results] == [0, 1, 2, 3]
    assert [result.mujoco_joint_axis for result in results] == [
        pytest.approx((0.0, -1.0, 0.0), abs=1e-9),
        pytest.approx((1.0, 0.0, 0.0), abs=1e-9),
        pytest.approx((0.0, -1.0, 0.0), abs=1e-9),
        pytest.approx((0.0, 0.0, 1.0), abs=1e-9),
    ]

    for result in results:
        assert result.perturbation_rad == pytest.approx(0.02, abs=1e-12)
        assert len(result.qpos_before) == 4
        assert len(result.qpos_after) == 4
        assert result.qpos_after[result.qpos_index] == pytest.approx(
            result.qpos_before[result.qpos_index] + 0.02,
            abs=1e-12,
        )
        assert len(result.tip_before) == 3
        assert len(result.tip_after) == 3
        assert len(result.tip_delta_m) == 3
        assert set(result.direction_dot_to_positive_axes) == {"x", "y", "z"}
        assert result.solver_to_mujoco_mapping
        assert result.mujoco_to_solver_mapping
        assert result.mapping_status in {
            "mapped_with_ref_minus_90_adapter",
            "diagnostic_only_held_current",
        }


def test_joint_axis_mapping_diagnostics_use_profile_owned_model_resource() -> None:
    results = run_fast_arm_joint_axis_mapping_diagnostics()

    assert len(results) == 4
    assert [result.qpos_index for result in results] == [0, 1, 2, 3]


def test_joint_axis_mapping_diagnostics_pin_dominant_tip_motion() -> None:
    results = {result.qpos_index: result for result in run_fast_arm_joint_axis_mapping_diagnostics()}

    assert results[0].dominant_axis == "y"
    assert results[0].dominant_sign == 1
    assert results[1].dominant_axis == "x"
    assert results[1].dominant_sign == -1
    assert results[2].dominant_axis == "x"
    assert results[2].dominant_sign == -1
    assert results[3].dominant_axis == "z"
    assert results[3].dominant_sign == -1


def test_endpoint_sanity_carries_mapping_diagnostics_without_changing_q1_adapter() -> None:
    results = _results_by_label()

    plus_z = results["+z"]
    minus_z = results["-z"]
    assert plus_z.status == "pass"
    assert plus_z.reason == "aligned"
    assert minus_z.status == "limitation"
    assert minus_z.reason == "opposite_direction"
    assert plus_z.qpos_ref_summary["mujoco_to_solver"] == "solver_q1 = mujoco_qpos1 + pi/2"
    assert plus_z.qpos_ref_summary["solver_to_mujoco"] == "mujoco_qpos1 = solver_q1 - pi/2"
    assert plus_z.mapping_status == "q1_ref_adapter_with_q0_q2_q3_hold"
    assert len(plus_z.qpos_perturbation_results) == 4
    assert plus_z.joint_axis_mapping_summary["mapping_status"] == "q1_ref_adapter_with_q0_q2_q3_hold"
    assert plus_z.solver_to_mujoco_mapping["q0"].startswith("held at current MuJoCo qpos0")


def test_xy_commands_remain_limitations_with_specific_mapping_reason() -> None:
    results = _results_by_label()

    for label in ["+x", "-x", "+y", "-y"]:
        result = results[label]
        assert result.status == "limitation"
        assert result.reason == "off_plane"
        assert result.target_rejected is False
        assert result.target_rejection_reason is None
        assert result.mapping_status == "q1_ref_adapter_with_q0_q2_q3_hold"
        assert "3D solver DOF allocation" in result.joint_axis_mapping_summary["mapping_decision"]
