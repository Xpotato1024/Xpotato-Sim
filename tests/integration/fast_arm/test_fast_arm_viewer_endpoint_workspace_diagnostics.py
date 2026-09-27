from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import sample_fast_arm_viewer_endpoint_workspace


def test_fast_arm_viewer_endpoint_workspace_diagnostics_cover_required_samples() -> None:
    diagnostics = sample_fast_arm_viewer_endpoint_workspace()
    by_label = {diagnostic.sample_label: diagnostic for diagnostic in diagnostics}

    assert {
        "default_qpos",
        "joint_1_positive_small",
        "joint_1_negative_small",
        "joint_2_positive_small",
        "joint_2_negative_small",
        "joint_3_positive_small",
        "joint_3_negative_small",
        "elbow_positive_small",
        "elbow_negative_small",
        "desired_initial_tip_x_positive_small",
        "desired_initial_tip_x_negative_small",
        "desired_initial_tip_y_positive_small",
        "desired_initial_tip_y_negative_small",
        "desired_initial_tip_z_positive_small",
        "desired_initial_tip_z_negative_small",
        "safe_endpoint",
        "initial_mujoco_tip",
    }.issubset(by_label)

    default = by_label["default_qpos"]
    simulator = build_fast_arm_simulator()
    initial_state = simulator.snapshot()
    initial_tip = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m
    assert default.sample_kind == "qpos_sample"
    assert default.qpos_sample == pytest.approx(initial_state.qpos[:4], abs=1e-12)
    assert default.mujoco_tip_site_world_position_m == pytest.approx(initial_tip, abs=1e-9)
    assert len(default.solver_local_fk_endpoint_m) == 3
    assert len(default.model_aligned_fk_endpoint_m) == 3
    assert len(default.solver_local_ik_target_m) == 3


def test_fast_arm_viewer_endpoint_workspace_diagnostics_record_ik_candidate_continuity() -> None:
    diagnostics = sample_fast_arm_viewer_endpoint_workspace()
    by_label = {diagnostic.sample_label: diagnostic for diagnostic in diagnostics}

    first_space_like = by_label["desired_initial_tip_z_positive_small"]
    initial_tip = by_label["initial_mujoco_tip"].mujoco_tip_site_world_position_m

    assert first_space_like.sample_kind == "desired_world_endpoint_sample"
    assert first_space_like.desired_world_endpoint_m == pytest.approx(
        (initial_tip[0], initial_tip[1], initial_tip[2] + 0.01),
        abs=1e-9,
    )
    assert first_space_like.solver_local_ik_target_m == pytest.approx(
        tuple(first_space_like.desired_world_endpoint_m[index] - (-0.069, 0.0, 0.7)[index] for index in range(3)),
        abs=1e-9,
    )
    assert first_space_like.ik_success is True
    assert first_space_like.ik_output_qpos_rad is not None
    assert len(first_space_like.ik_output_qpos_rad) == 4
    assert first_space_like.qpos_delta_norm_from_seed_rad is not None
    assert first_space_like.qpos_delta_norm_from_seed_rad > 0.2
    assert first_space_like.rejection_reason is None

    safe_endpoint = by_label["safe_endpoint"]
    assert safe_endpoint.desired_world_endpoint_m == pytest.approx((0.6, 0.0, 0.1), abs=1e-12)
    assert safe_endpoint.solver_local_ik_target_m == pytest.approx((0.669, 0.0, -0.6), abs=1e-9)
    assert safe_endpoint.ik_success in {True, False}
