"""最適化がgeneration、物理積分、安全上限と中立の意味を保つことを確認する。"""
from dataclasses import FrozenInstanceError

import mujoco
import pytest

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.schemas.coordinated import EndpointVelocity


def test_snapshot_sharing_is_immutable_and_expires_on_commit_and_reset():
    profile = load_launch_profile("dynamic-cube-drop")
    provider = profile.build_model().provider
    before = provider.snapshot()
    assert provider.snapshot() is before
    with pytest.raises(FrozenInstanceError):
        before.generation = 0
    commands = tuple(EndpointVelocity(a, (0., 0., 0.), "world") for a in provider.endpoint_ids)
    candidate = provider.prepare(commands, profile.dt_s)
    assert candidate.before is before
    assert provider.snapshot() is before
    after = provider.commit(candidate)
    assert after is provider.snapshot() and after is not before
    assert before.simulation_time_s == 0 and after.simulation_time_s > 0
    provider.reset()
    reset = provider.snapshot()
    assert reset is not after and reset.generation > after.generation
    assert reset.simulation_time_s == 0 and reset.joint_positions_rad == before.joint_positions_rad
    provider.invalidate()
    assert provider.snapshot() is not reset and provider.snapshot() == reset


def test_neutral_skips_ik_but_keeps_all_physics_substeps_and_servo_targets(monkeypatch):
    profile = load_launch_profile("dynamic-cube-drop")
    provider = profile.build_model().provider
    targets = provider._data.ctrl.copy()
    original = mujoco.mj_step
    steps = []
    def measured(model, data):
        steps.append(float(data.time))
        return original(model, data)
    monkeypatch.setattr(mujoco, "mj_step", measured)
    def unwanted(qpos):
        raise AssertionError("neutral input must not run finite-difference IK")
    for kinematics in provider._kinematics.values():
        monkeypatch.setattr(kinematics, "forward", unwanted)
    commands = tuple(EndpointVelocity(a, (0., 0., 0.), "world") for a in provider.endpoint_ids)
    provider.commit(provider.prepare(commands, profile.dt_s))
    assert len(steps) == profile.scene_plan.dynamics.substeps(profile.dt_s)
    assert provider._data.ctrl == pytest.approx(targets, abs=0)
    assert provider._data.time == pytest.approx(profile.dt_s)
    # 中立は物体の重力運動を止めるcacheではない。
    assert provider.sample(frame_index=1, metadata={}).geometry.objects[0].position_m[2] < .5


def test_modified_candidate_still_rejected_with_cached_before():
    profile = load_launch_profile("dynamic-cube-drop")
    provider = profile.build_model().provider
    before = provider.snapshot()
    commands = tuple(EndpointVelocity(a, (0., 0., 0.), "world") for a in provider.endpoint_ids)
    candidate = provider.prepare(commands, profile.dt_s)
    provider._pending[1].qpos[0] += .001
    with pytest.raises(ValueError, match="modified"):
        provider.commit(candidate)
    assert provider.snapshot() is before


def test_native_warning_array_keeps_fail_closed_check_in_all_observations():
    provider = load_launch_profile("dynamic-cube-drop").build_model().provider
    provider._data.warning[0].number = 1
    for observe in (lambda: provider._check_data(provider._data),
                    lambda: provider._scene_observer.observe(provider._data, frame_index=0),
                    lambda: provider._dynamics_observation(0)):
        with pytest.raises(ValueError, match="warning"):
            observe()


@pytest.mark.parametrize("field,value,reason", [
    ("qvel", 10.0001, "speed budget"),
    ("ctrl", .7501, "tracking error"),
    ("qacc", float("nan"), "nonfinite"),
    ("actuator_force", float("inf"), "nonfinite"),
])
def test_dynamic_checks_keep_thresholds_and_nonfinite_rejection(field, value, reason):
    provider = load_launch_profile("dynamic-cube-drop").build_model().provider
    data = provider._data
    if field == "ctrl":
        data.ctrl[0] = data.qpos[0] + value
    else:
        getattr(data, field)[0] = value
    with pytest.raises(ValueError, match=reason):
        provider._check_data(data)


def test_live_qpos_mutation_after_prepare_is_rejected_even_with_cached_snapshot():
    profile = load_launch_profile("dynamic-cube-drop")
    provider = profile.build_model().provider
    before = provider.snapshot()
    commands = tuple(EndpointVelocity(a, (0., 0., 0.), "world") for a in provider.endpoint_ids)
    candidate = provider.prepare(commands, profile.dt_s)
    provider._data.qpos[0] += .001
    assert provider.snapshot() is before
    with pytest.raises(ValueError, match="foreign, consumed, or stale"):
        provider.commit(candidate)
    assert provider._pending is None
    assert provider._generation == before.generation


def test_display_sampling_cadence_cannot_change_native_integration_or_contact_state():
    import numpy as np
    profile = load_launch_profile("dynamic-cube-drop")
    providers = [profile.build_model().provider for _ in range(2)]
    commands = tuple(EndpointVelocity(a, (.002, 0., 0.), "world") for a in providers[0].endpoint_ids)
    for tick in range(30):
        for provider in providers:
            provider.commit(provider.prepare(commands, profile.dt_s))
        for _ in range(3):
            providers[1].sample(frame_index=tick, metadata={})
        assert providers[0].trial_state() == providers[1].trial_state()
        for field in ("qacc", "qacc_warmstart", "actuator_force", "efc_force", "site_xpos"):
            np.testing.assert_array_equal(getattr(providers[0]._data, field), getattr(providers[1]._data, field))
        assert providers[0].sample(frame_index=tick, metadata={}).dynamics == providers[1].sample(frame_index=tick, metadata={}).dynamics
