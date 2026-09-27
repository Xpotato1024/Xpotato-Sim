from __future__ import annotations

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import run_fast_arm_endpoint_motion_sanity


def _result_by_label():
    return {result.command_label: result for result in run_fast_arm_endpoint_motion_sanity()}


def test_solver_mujoco_frame_alignment_uses_base_link_local_target() -> None:
    results = _result_by_label()

    plus_z = results["+z"]
    assert plus_z.world_target_m == pytest.approx(
        (
            plus_z.initial_tip_position_m[0],
            plus_z.initial_tip_position_m[1],
            plus_z.initial_tip_position_m[2] + 0.02,
        ),
        abs=1e-9,
    )
    assert plus_z.solver_base_world_position_m == pytest.approx((-0.069, 0.0, 0.7), abs=1e-9)
    assert plus_z.solver_local_target_m == pytest.approx(
        tuple(plus_z.world_target_m[index] - plus_z.solver_base_world_position_m[index] for index in range(3)),
        abs=1e-9,
    )
    assert plus_z.transformed_solver_fk_world_m == pytest.approx(plus_z.world_target_m, abs=1e-5)
    assert plus_z.distance_from_solver_base_m == pytest.approx(
        sum(value * value for value in plus_z.solver_local_target_m) ** 0.5,
        abs=1e-9,
    )


def test_solver_mujoco_qpos_reference_adapter_keeps_z_commands_aligned() -> None:
    results = _result_by_label()

    plus_z = results["+z"]
    minus_z = results["-z"]
    assert plus_z.status == "pass"
    assert plus_z.reason == "aligned"
    assert plus_z.actual_delta_m[2] > 0.0
    assert plus_z.direction_dot > 0.85
    assert minus_z.status == "limitation"
    assert minus_z.reason == "opposite_direction"
    assert minus_z.actual_delta_m[2] > 0.0
    assert minus_z.direction_dot < -0.85
    assert plus_z.qpos_ref_summary["mujoco_to_solver"] == "solver_q1 = mujoco_qpos1 + pi/2"
    assert plus_z.qpos_ref_summary["solver_to_mujoco"] == "mujoco_qpos1 = solver_q1 - pi/2"


def test_solver_mujoco_frame_alignment_turns_x_commands_into_limitations_not_unreachable() -> None:
    results = _result_by_label()

    assert results["+x"].status == "limitation"
    assert results["+x"].reason == "off_plane"
    assert results["-x"].status == "limitation"
    assert results["-x"].reason == "off_plane"
    assert results["+x"].target_rejected is False
    assert results["-x"].target_rejected is False
