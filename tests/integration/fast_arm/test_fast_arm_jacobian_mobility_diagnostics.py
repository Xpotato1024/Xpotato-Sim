from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.jacobian_mobility import _pose_qpos, run_fast_arm_jacobian_mobility_diagnostics


def test_fast_arm_diagnostic_has_explicit_mapping_and_deterministic_pose_sweep() -> None:
    first = run_fast_arm_jacobian_mobility_diagnostics()
    second = run_fast_arm_jacobian_mobility_diagnostics()
    assert first.to_json() == second.to_json()
    assert [pose.label for pose in first.poses] == [
        "default_pose", "sholder_joint_1_positive_nearby", "sholder_joint_1_negative_nearby",
        "sholder_joint_2_positive_nearby", "sholder_joint_2_negative_nearby",
        "sholder_joint_3_positive_nearby", "sholder_joint_3_negative_nearby",
        "elbow_joint_positive_nearby", "elbow_joint_negative_nearby", "representative_combined_fixture",
    ]
    assert [item["joint_name"] for item in first.controlled_dof_mapping] == [
        "sholder_joint_1", "sholder_joint_2", "sholder_joint_3", "elbow_joint"
    ]
    assert all(len(pose.finite_difference.jacobian) == 3 and len(pose.finite_difference.jacobian[0]) == 4 for pose in first.poses)


def test_fast_arm_default_pose_records_native_comparison_and_axis_metrics() -> None:
    result = run_fast_arm_jacobian_mobility_diagnostics()
    default = result.poses[0]
    simulator = build_fast_arm_simulator()
    assert default.qpos_rad == pytest.approx(
        tuple(simulator.model.key(FAST_ARM_ROBOT_PROFILE.initial_keyframe_name).qpos),
        abs=1e-12,
    )
    assert default.jacobian_difference_norm < 1e-3
    assert default.finite_difference.effective_rank == 3
    assert default.native.effective_rank == 3
    assert default.finite_difference.effective_rank_tolerance >= default.jacobian_difference_norm
    assert min(default.native.row_norms) > 0.0
    assert len(default.directions) == 6
    assert default.directions[0].label == "+X"
    assert default.directions[0].delta.requested_delta_m[0] > 0.0


def test_fast_arm_sensitivity_contains_lower_current_upper_points_and_json_is_finite_safe() -> None:
    result = run_fast_arm_jacobian_mobility_diagnostics()
    for points in (result.epsilon_sensitivity, result.damping_sensitivity, result.qpos_cap_sensitivity):
        assert len(points) == 3
        assert points[1].value > points[0].value
        assert points[2].value > points[1].value
    payload = json.loads(result.to_json())
    assert payload["schema_version"] == "r7-e-p9-v2"
    assert payload["model_identity"] == "fast_arm_canonical"
    assert payload["poses"][0]["finite_difference"]["condition_number"] in ("Infinity", None) or math.isfinite(payload["poses"][0]["finite_difference"]["condition_number"])


def test_fast_arm_nearby_poses_record_actual_perturbations_and_preserve_non_target_qpos() -> None:
    result = run_fast_arm_jacobian_mobility_diagnostics()
    default = result.poses[0].qpos_rad
    assert len({pose.qpos_rad for pose in result.poses}) == len(result.poses)
    for pose in result.poses[1:9]:
        assert pose.perturbed_joint_name is not None
        assert pose.actual_perturbation_rad is not None
        assert pose.actual_perturbation_rad == pytest.approx(pose.requested_perturbation_rad, abs=1e-12)
        assert sum(abs(pose.actual_perturbation_vector_rad[index]) > 0.0 for index in range(4)) == 1
        assert sum(pose.qpos_rad[index] != default[index] for index in range(4)) == 1
    representative = result.poses[-1]
    assert representative.perturbed_joint_name == "combined_fixture"
    assert representative.actual_perturbation_vector_rad == pytest.approx((0.02, 0.01, 0.015, 0.005))


def test_pose_generation_clips_only_limited_joints_and_uses_qpos_addresses() -> None:
    names = ("sholder_joint_1", "sholder_joint_2", "sholder_joint_3", "elbow_joint")

    class FakeMujoco:
        class mjtObj:
            mjOBJ_JOINT = 1

        @staticmethod
        def mj_name2id(model: object, kind: int, name: str) -> int:
            return names.index(name)

    class FakeSimulator:
        model = SimpleNamespace(
            nq=4,
            jnt_limited=np.array([0, 1, 0, 0], dtype=np.int32),
            jnt_range=np.array([[0.0, 0.0], [-0.05, 0.05], [0.0, 0.0], [0.0, 0.0]], dtype=np.float64),
            jnt_qposadr=np.array([3, 1, 0, 2], dtype=np.int32),
        )
        data = SimpleNamespace(qpos=np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float64))

        @staticmethod
        def _import_mujoco() -> FakeMujoco:
            return FakeMujoco()

    poses = _pose_qpos(FakeSimulator())  # type: ignore[arg-type]
    positive_limited = next(pose for pose in poses if pose["label"] == "sholder_joint_2_positive_nearby")
    negative_limited = next(pose for pose in poses if pose["label"] == "sholder_joint_2_negative_nearby")
    assert positive_limited["qpos"][1] == pytest.approx(0.05)
    assert negative_limited["qpos"][1] == pytest.approx(-0.05)
    assert positive_limited["actual_perturbation_rad"] == pytest.approx(0.05)
    unlimited = next(pose for pose in poses if pose["label"] == "sholder_joint_1_positive_nearby")
    assert unlimited["qpos"][3] == pytest.approx(0.1)
    assert unlimited["clipped"] is False
