from __future__ import annotations

import math

import pytest

from xpotato_sim.mujoco_backend.model_info import inspect_mujoco_model
from xpotato_sim.mujoco_backend.snapshot import snapshot_mujoco_state
from xpotato_sim.plugins.robots.catalog import (
    registered_robot_runtime_plugin_ids,
    resolve_robot_runtime,
)
from xpotato_sim.plugins.robots.catalog import registered_robot_profile_ids
from tests.robots.robot_runtime_plugin_conformance_cases import (
    ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES,
)
from tests.support.robot_runtime_plugin_conformance import (
    assert_qpos_feasible,
    endpoint_position_from_state,
    load_case_model,
    snapshot_at_case_qpos,
    validate_conformance_case_registry,
)


FAIL_CLOSED_CASES = tuple(
    pytest.param(case, failure, id=f"{case.case_id}-{failure.case_id}")
    for case in ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES
    for failure in case.fail_closed_cases
)


def test_explicit_conformance_registry_is_non_empty_and_deterministic() -> None:
    validate_conformance_case_registry(ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES)


@pytest.mark.parametrize(
    "case",
    ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES,
    ids=lambda case: case.case_id,
)
def test_profile_plugin_identity_and_behavioral_boundary(case) -> None:  # noqa: ANN001
    resolved = resolve_robot_runtime(case.expected_profile_id)

    assert resolved.profile is case.profile
    assert resolved.plugin is case.plugin
    assert case.plugin.profile_id == case.expected_profile_id
    assert case.plugin.profile is case.profile
    assert case.plugin.profile.profile_id == case.profile.profile_id
    assert case.expected_profile_id in registered_robot_profile_ids()
    assert case.expected_profile_id in registered_robot_runtime_plugin_ids()
    assert registered_robot_profile_ids() == registered_robot_runtime_plugin_ids()

    for method_name in (
        "validate_model",
        "build_inverse_kinematics",
        "build_forward_kinematics",
        "build_target_motion_generator",
        "build_local_endpoint_motion_generator",
        "build_qpos_feasibility_guard",
        "endpoint_position_from_state",
        "endpoint_orientation_from_state",
    ):
        assert callable(getattr(case.plugin, method_name, None)), method_name


@pytest.mark.parametrize(
    "case",
    ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES,
    ids=lambda case: case.case_id,
)
def test_asset_joint_dimension_home_and_endpoint_contract(case) -> None:  # noqa: ANN001
    bundle = load_case_model(case)
    model = bundle.model
    info = inspect_mujoco_model(model)

    case.plugin.validate_model(model)
    assert int(model.nq) == case.expected_qpos_dimension
    assert int(model.nv) == case.expected_qvel_dimension
    assert info.joint_names == case.expected_joint_names
    assert case.endpoint_site_name in info.site_names
    assert tuple(float(value) for value in bundle.data.qpos) == pytest.approx(
        tuple(float(value) for value in model.key(case.home_keyframe_name).qpos),
        abs=1e-12,
    )
    assert len(tuple(model.key(case.home_keyframe_name).qpos)) == case.expected_qpos_dimension
    assert len(tuple(model.key(case.home_keyframe_name).qvel)) == case.expected_qvel_dimension
    assert all(math.isfinite(float(value)) for value in bundle.data.qpos)
    assert all(math.isfinite(float(value)) for value in bundle.data.qvel)


@pytest.mark.parametrize(
    "case",
    ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES,
    ids=lambda case: case.case_id,
)
def test_home_pose_satisfies_profile_owned_feasibility_contract(case) -> None:  # noqa: ANN001
    bundle = load_case_model(case)
    home_qpos = tuple(float(value) for value in bundle.model.key(case.home_keyframe_name).qpos)
    assert_qpos_feasible(case, bundle.model, home_qpos)
    state = snapshot_mujoco_state(bundle.model, bundle.data, frame_index=0)
    site_position = endpoint_position_from_state(state, site_name=case.endpoint_site_name)
    endpoint = case.plugin.endpoint_position_from_state(state)
    assert endpoint is not None
    assert endpoint == pytest.approx(
        site_position,
        abs=min(consistency.tolerance_m for consistency in case.mujoco_endpoint_cases),
    )
    assert all(math.isfinite(float(value)) for value in site_position)
    assert all(math.isfinite(float(value)) for value in endpoint)


@pytest.mark.parametrize(
    "case",
    ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES,
    ids=lambda case: case.case_id,
)
def test_known_forward_kinematics_cases_use_literal_expected_values(case) -> None:  # noqa: ANN001
    fk = case.plugin.build_forward_kinematics()
    for known in case.known_fk_cases:
        actual = fk.forward(known.qpos)
        assert all(math.isfinite(float(value)) for value in actual)
        assert actual == pytest.approx(known.expected_endpoint_m, abs=known.tolerance_m)


@pytest.mark.parametrize(
    "case",
    ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES,
    ids=lambda case: case.case_id,
)
def test_reachable_ik_fk_round_trips_are_feasible_and_deterministic(case) -> None:  # noqa: ANN001
    bundle = load_case_model(case)
    ik = case.plugin.build_inverse_kinematics()
    fk = case.plugin.build_forward_kinematics()

    for round_trip in case.ik_round_trip_cases:
        result = ik.solve(round_trip.target_position_m, round_trip.seed_qpos)
        solution = tuple(float(value) for value in result.joint_angles_rad)
        assert len(solution) == case.expected_qpos_dimension
        assert all(math.isfinite(value) for value in solution)
        if round_trip.expected_feasible:
            assert_qpos_feasible(case, bundle.model, solution)
        actual_endpoint = fk.forward(solution)
        assert actual_endpoint == pytest.approx(
            round_trip.target_position_m,
            abs=round_trip.tolerance_m,
        )
        if round_trip.require_determinism:
            repeat = ik.solve(round_trip.target_position_m, round_trip.seed_qpos)
            assert tuple(repeat.joint_angles_rad) == pytest.approx(solution, abs=0.0)


@pytest.mark.parametrize(
    "case",
    ROBOT_RUNTIME_PLUGIN_CONFORMANCE_CASES,
    ids=lambda case: case.case_id,
)
def test_mujoco_endpoint_accessor_matches_declared_site_and_model_alignment(case) -> None:  # noqa: ANN001
    for consistency in case.mujoco_endpoint_cases:
        state = snapshot_at_case_qpos(case, consistency)
        site_position = endpoint_position_from_state(state, site_name=case.endpoint_site_name)
        plugin_position = case.plugin.endpoint_position_from_state(state)

        assert plugin_position is not None
        assert all(math.isfinite(float(value)) for value in site_position)
        assert plugin_position == pytest.approx(site_position, abs=consistency.tolerance_m)
        if case.model_aligned_endpoint_evaluator is not None:
            model_aligned = case.model_aligned_endpoint_evaluator(consistency.qpos)
            assert model_aligned == pytest.approx(site_position, abs=consistency.tolerance_m)


@pytest.mark.parametrize(
    ("case", "failure"),
    FAIL_CLOSED_CASES,
)
def test_fail_closed_case_rejects_profile_plugin_model_and_home_mismatches(
    case, failure, tmp_path
) -> None:  # noqa: ANN001
    with pytest.raises(failure.exception_type, match=failure.message_pattern):
        failure.run(tmp_path)
