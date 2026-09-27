from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import run_fast_arm_endpoint_motion_sanity


def test_initial_tip_workspace_diagnostics_are_structured_for_all_axes() -> None:
    results = run_fast_arm_endpoint_motion_sanity()
    initial_state = build_fast_arm_simulator().snapshot()
    expected_tip = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m

    assert [result.command_label for result in results] == ["+x", "-x", "+y", "-y", "+z", "-z"]
    for result in results:
        assert result.base_endpoint_source == "initial_tip"
        assert result.initial_tip_position_m == pytest.approx(expected_tip, abs=1e-9)
        assert result.solver_base_world_position_m == pytest.approx((-0.069, 0.0, 0.7), abs=1e-9)
        assert result.mujoco_base_link_position_m == pytest.approx((-0.069, 0.0, 0.7), abs=1e-9)
        assert result.tip_relative_to_base_link_m == pytest.approx(
            tuple(expected_tip[index] - result.mujoco_base_link_position_m[index] for index in range(3)),
            abs=1e-9,
        )
        assert result.solver_input_endpoint_m == pytest.approx(result.solver_local_target_m, abs=1e-9)
        assert result.reachable_workspace_summary != "unavailable"
        assert isinstance(result.reachable_workspace_summary, dict)
        assert result.reachable_workspace_summary["solver_base_position_m"] == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)
        assert result.reachable_workspace_summary["max_radius_m"] == pytest.approx(0.73, abs=1e-9)
        assert result.distance_from_solver_base_m != "unavailable"
        assert float(result.distance_from_solver_base_m) <= float(result.reachable_workspace_summary["max_radius_m"])
        assert result.target_constraints_summary != "unavailable"
        assert result.frame_mapping_summary != "unavailable"
        assert isinstance(result.frame_mapping_summary, dict)
        assert result.frame_mapping_summary["mapping_status"] == "world target is transformed to solver local target"
        assert result.frame_transform_status == "world_minus_mujoco_base_link"
        assert result.diagnosis == "world_target_transformed_to_mujoco_base_link_solver_frame"


def test_initial_tip_workspace_diagnostics_keep_qpos_reference_details_without_crashing() -> None:
    results = run_fast_arm_endpoint_motion_sanity()
    initial_qpos = build_fast_arm_simulator().snapshot().qpos[:4]

    for result in results:
        assert result.status in {"pass", "limitation", "rejected", "unavailable"}
        assert result.reason != "target_unreachable"
        assert result.target_rejected is False
        assert result.target_rejection_reason is None
        assert result.target_rejection_message is None
        assert result.rejected_desired_endpoint_m == "unavailable"
        assert result.solver_seed_qpos == pytest.approx(
            (initial_qpos[0], initial_qpos[1] + 1.5707963267948966, initial_qpos[2], initial_qpos[3]),
            abs=1e-9,
        )
        assert result.solver_result_qpos != "unavailable"
        assert isinstance(result.qpos_ref_summary, dict)
        assert result.qpos_ref_summary["mapping_status"] == "q1_ref_adapter_with_q0_q2_q3_hold"
        assert len(result.qpos_before) == 4
        assert len(result.qpos_after) == 4
