"""反力の作用方向、対象、品質、時計と旧filterの独立な回帰例。実機は使わない。"""
from copy import deepcopy
from dataclasses import replace
import math

import pytest

from xpotato_sim.runtime.scene.force_signal import SceneForceProcessor, default_force_config


def processor(**options):
    return SceneForceProcessor(model_sha256="1"*64, scene_digest="2"*64,
        settings_digest=options.pop("settings_digest", "3"*64),
        endpoint_ids=("left", "right"), object_ids=("cube", "pedestal"),
        target_object_ids=("cube",), config=options.pop("config", default_force_config(.2)), **options)


def contact(endpoint="left", *, native=(2., 1., 0.), normal=(1.,0.,0.), reverse=False,
            object_id="cube", status="measured"):
    a, b = {"kind":"tool", "id":endpoint}, {"kind":"object", "id":object_id}
    return {"role1": b if reverse else a, "role2": a if reverse else b,
        "point_world_m": [0.,0.,.5], "normal_geom1_to_geom2_world":
            tuple(-x for x in normal) if reverse else normal, "status": status,
        "force_on_geom2_world_n": None if status != "measured" else tuple(-x for x in native) if reverse else native,
        "torque_on_geom2_world_nm": None if status != "measured" else (0.,0.,0.)}


def observation(contacts, *, tick=1, time=None):
    return {"schema_version":"scene-dynamics-observation/v1", "model_sha256":"1"*64,
        "scene_digest":"2"*64, "settings_digest":"3"*64, "frame_index":tick,
        "simulation_time_s":tick*.01 if time is None else time,
        "semantics":"coordinated_actuator_servo_dynamic/v1", "contacts":contacts}


def apply(p, contacts, *, tick=1, epoch="trial-one", now=None, time=None):
    d = None if contacts is None else observation(contacts, tick=tick, time=time)
    return p.process(d, epoch=epoch, tick=tick, frame_index=tick,
        simulation_time_s=tick*.01 if time is None else time, now_s=tick*.01 if now is None else now)


def test_geom_reversal_target_selection_and_bimanual_cancellation():
    a = contact()
    right = contact("right", native=(-2.,-1.,0.), normal=(-1.,0.,0.))
    support = contact(); support["role1"] = {"kind":"support", "id":"floor"}
    inputs = [a, right, contact(object_id="pedestal", native=(500.,0.,0.)), support]
    before = deepcopy(inputs)
    s = apply(processor(), inputs)
    left, right = s.pairs
    assert left.raw_force_world_n == (-2.,-1.,0.)
    assert right.raw_force_world_n == (2.,1.,0.)
    assert left.normal_load_n == right.normal_load_n == 2
    assert tuple(x+y for x,y in zip(left.raw_force_world_n,right.raw_force_world_n)) == (0,0,0)
    assert inputs == before
    reversed_signal = apply(processor(), [contact(reverse=True)])
    assert reversed_signal.pairs[0] == left


def test_normal_load_is_not_norm_of_vector_sum_or_single_plane_projection():
    opposing = contact(native=(-2.,0.,0.), normal=(-1.,0.,0.))
    pair = apply(processor(), [contact(native=(2.,0.,0.)), opposing]).pairs[0]
    assert pair.raw_force_world_n == (0.,0.,0.)
    assert pair.normal_load_n == 4.
    assert pair.status == "active"


@pytest.mark.parametrize("contacts,status", [([], "no_contact"),
    ([contact(status="measurement_unavailable")], "measurement_unavailable"),
    ([contact(), contact(status="measurement_unavailable")], "partial_missing")])
def test_no_contact_is_only_valid_zero_and_partial_evidence_is_not_a_partial_sum(contacts,status):
    pair = apply(processor(), contacts).pairs[0]
    assert pair.status == status
    if status == "no_contact":
        assert pair.raw_force_world_n == pair.force_world_n == (0.,0.,0.) and pair.normal_load_n == 0.
    else:
        assert pair.raw_force_world_n is pair.force_world_n is pair.normal_load_n is None


def test_missing_kinematic_invalid_and_stale_are_distinct():
    assert apply(processor(), None).status == "missing"
    assert apply(processor(settings_digest=None), None).status == "not_evaluated_kinematic"
    bad = contact(native=(float("nan"),0.,0.))
    assert apply(processor(), [bad]).status == "invalid"
    p = processor()
    apply(p,[contact()])
    assert apply(p,[contact()],tick=2,time=.01).status == "stale"
    p = processor(); apply(p,[contact()])
    assert apply(p,[contact()],tick=2,now=.4).status == "stale"


def test_display_repeat_keeps_sample_identity_and_never_refreshes_age():
    p = processor(); first = apply(p,[contact()])
    d = observation([contact()],tick=4,time=.01)
    duplicate = p.process(d,epoch="trial-one",tick=1,frame_index=4,simulation_time_s=.01,now_s=.1)
    assert duplicate is first
    assert duplicate.source_frame_index == 1 and duplicate.observed_monotonic_s == .01
    stale = p.process(d,epoch="trial-one",tick=1,frame_index=4,simulation_time_s=.01,now_s=.3)
    assert stale.status == "stale" and stale.observed_monotonic_s == .01
    assert all(pair.force_world_n is None for pair in stale.pairs)


def test_filter_shared_values_raw_preservation_stop_missing_and_epoch_reset():
    config = replace(default_force_config(.2), smoothing_window_samples=2, magnitude_clamp_n=3.)
    p = processor(config=config)
    first = apply(p,[contact(native=(2.,0.,0.))])
    second = apply(p,[contact(native=(8.,0.,0.))],tick=2)
    pair = second.pairs[0]
    assert pair.raw_force_world_n == (-8.,0.,0.) and pair.normal_load_n == 8.
    assert pair.force_world_n == (-3.,0.,0.) and pair.saturated
    assert first.pairs[0].force_world_n == (-2.,0.,0.)
    apply(p,None,tick=3)
    assert apply(p,[contact(native=(1.,0.,0.))],tick=4).pairs[0].force_world_n == (-1.,0.,0.)
    p.invalidate()
    assert p.latest.status == "unavailable" and all(x.force_world_n is None for x in p.latest.pairs)
    new = apply(p,[contact(native=(2.,0.,0.))],epoch="trial-two")
    assert new.pairs[0].force_world_n == (-2.,0.,0.) and new.epoch == "trial-two"
    assert new.manifest_sha256 == first.manifest_sha256
    assert p.latest is new and len(p._filters) == 2


@pytest.mark.parametrize("field,value", [("settings_digest","4"*64), ("model_sha256","4"*64),
    ("scene_digest","4"*64), ("frame_index",0), ("simulation_time_s",.02)])
def test_foreign_identity_is_rejected(field,value):
    d = observation([contact()]); d[field] = value
    s = processor().process(d,epoch="run",tick=1,frame_index=1,simulation_time_s=.01,now_s=.01)
    assert s.status == "invalid"


def test_native_single_and_bimanual_force_match_independent_solver_oracle():
    import mujoco
    import numpy as np
    from tests.integration.fast_arm.test_scene_dynamics import profile, advance
    for model in ("single_left", "bimanual"):
        p = profile("tasks/dynamic-fixed-contact", model=model)
        instance = p.build_model(); provider = instance.provider
        sample = advance(provider,p,180,(.06,0.,0.))
        targets = tuple(o.instance_id for o in p.scene_plan.manifest.objects)
        f = SceneForceProcessor(model_sha256=sample.robot.model_sha256,
            scene_digest=p.scene_plan.manifest.digest, settings_digest=p.scene_plan.dynamics.digest,
            endpoint_ids=provider.endpoint_ids, object_ids=targets, target_object_ids=targets,
            config=default_force_config(.2))
        signal = f.process(sample.dynamics,epoch="native",tick=180,frame_index=180,
            simulation_time_s=sample.state.time_s,now_s=3.)
        assert signal.status == "available"
        expected = {key:np.zeros(3) for key in f.keys}
        # 独立oracleはsource観測のroleを使わず、Robot/scene bindingからnative geom名を照合する。
        scene = provider._scene_observer.scene
        colliders = {c.geom_name:c.endpoint_id for c in scene.colliders}
        objects = {name:oid for oid,name in scene.object_geoms}
        for i in range(provider._data.ncon):
            c = provider._data.contact[i]
            a = mujoco.mj_id2name(provider.model,mujoco.mjtObj.mjOBJ_GEOM,int(c.geom1))
            b = mujoco.mj_id2name(provider.model,mujoco.mjtObj.mjOBJ_GEOM,int(c.geom2))
            if a in colliders and b in objects: key, sign = (colliders[a],objects[b]), -1.
            elif b in colliders and a in objects: key, sign = (colliders[b],objects[a]), 1.
            else: continue
            if c.efc_address < 0: continue
            wrench = np.zeros(6); mujoco.mj_contactForce(provider.model,provider._data,i,wrench)
            expected[key] += sign * np.asarray(c.frame).reshape(3,3).T @ wrench[:3]
        assert any(math.hypot(*v)>0 for v in expected.values())
        for pair in signal.pairs:
            assert pair.raw_force_world_n == pytest.approx(expected[pair.endpoint_id,pair.object_id])
