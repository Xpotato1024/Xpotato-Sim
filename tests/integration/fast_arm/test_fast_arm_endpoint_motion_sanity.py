from __future__ import annotations

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics.endpoint_motion_sanity import FastArmEndpointMotionSanityResult, run_fast_arm_endpoint_motion_sanity
from xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics import endpoint_motion_sanity as endpoint_motion_sanity_module
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE


def _vector_delta(
    end_m: tuple[float, float, float],
    start_m: tuple[float, float, float],
) -> tuple[float, float, float]:
    return tuple(end_m[index] - start_m[index] for index in range(3))


def test_run_fast_arm_endpoint_motion_sanity_returns_axiswise_results() -> None:
    results = run_fast_arm_endpoint_motion_sanity()

    assert isinstance(results, tuple)
    assert len(results) == 6
    assert [result.command_label for result in results] == ["+x", "-x", "+y", "-y", "+z", "-z"]

    for result in results:
        assert isinstance(result, FastArmEndpointMotionSanityResult)
        assert result.axis in {"x", "y", "z"}
        assert result.sign in {-1, 1}
        assert result.status in {"pass", "rejected", "limitation", "unavailable"}
        assert result.reason
        assert len(result.commanded_delta_m) == 3
        assert len(result.command_direction_m) == 3
        assert result.desired_endpoint_m == result.target_position_m
        assert result.base_endpoint_source == "initial_tip"
        assert result.base_endpoint_m is not None
        assert result.initial_tip_position_m is not None
        assert result.base_endpoint_m == pytest.approx(result.initial_tip_position_m, abs=1e-9)
        assert result.desired_endpoint_m is not None
        assert _vector_delta(result.desired_endpoint_m, result.initial_tip_position_m) == pytest.approx(
            result.commanded_delta_m,
            abs=1e-9,
        )
        assert len(result.qpos_before) == 4
        assert len(result.qpos_after) == 4

    x_plus = results[0]
    z_plus = results[4]
    z_minus = results[5]
    assert x_plus.status in {"pass", "limitation", "rejected"}
    assert z_plus.status in {"pass", "limitation", "rejected"}
    assert z_minus.status in {"pass", "limitation", "rejected"}


def test_run_fast_arm_endpoint_motion_sanity_uses_profile_owned_model_resource() -> None:
    results = run_fast_arm_endpoint_motion_sanity(command_delta_m=0.001)

    assert len(results) == 6
    assert all(result.reason != "backend_exception" for result in results)


def test_run_fast_arm_endpoint_motion_sanity_preserves_explicit_base_mode() -> None:
    explicit_base_m = (0.6, 0.0, 0.1)

    results = run_fast_arm_endpoint_motion_sanity(base_desired_endpoint_m=explicit_base_m)

    assert len(results) == 6
    for result in results:
        assert result.base_endpoint_source == "explicit"
        assert result.base_endpoint_m == pytest.approx(explicit_base_m, abs=1e-9)
        assert result.desired_endpoint_m is not None
        assert _vector_delta(result.desired_endpoint_m, explicit_base_m) == pytest.approx(
            result.commanded_delta_m,
            abs=1e-9,
        )


def test_run_fast_arm_endpoint_motion_sanity_converts_pipeline_build_failures_into_unavailable_results(monkeypatch) -> None:
    def _raise_build_failure(*args, **kwargs):  # noqa: ANN001, ANN002
        raise RuntimeError("boom")

    monkeypatch.setattr(endpoint_motion_sanity_module, "build_concrete_mujoco_pipeline", _raise_build_failure)

    results = run_fast_arm_endpoint_motion_sanity()

    assert len(results) == 6
    for result in results:
        assert result.status == "unavailable"
        assert result.reason == "backend_exception"
        assert result.error_message == "boom"
        assert result.base_endpoint_m is None
        assert result.base_endpoint_source == "unavailable"
        assert result.desired_endpoint_m is None
        assert result.target_position_m is None
        assert result.initial_tip_position_m is None
        assert result.final_tip_position_m is None
        assert result.actual_delta_m is None
        assert result.qpos_before == ()
        assert result.qpos_after == ()
