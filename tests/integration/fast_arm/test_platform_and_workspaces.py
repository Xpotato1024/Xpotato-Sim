"""有限台・Robot据付不変・native作業域再利用の回帰。実時間閾値はCI条件にしない。"""
from dataclasses import replace
import numpy as np
import mujoco
import pytest
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.schemas.coordinated import EndpointVelocity
from xpotato_sim.plugins.robots.fast_arm.adapter.coordinated import _SnapshotKinematics
from tests.integration.fast_arm.test_scene_dynamics import advance, profile


def test_push_floor_and_finite_fixed_pedestal_do_not_change_robot_mount():
    p=profile('dynamic-cube-push');r=p.build_model().provider
    reference=profile('dynamic-cube-drop').build_model().provider
    world=p.scene_plan.manifest.world
    assert len(world.support_planes)==1 and world.support_planes[0].position_m==(0.,0.,0.)
    objects={o.instance_id:o for o in p.scene_plan.manifest.objects}
    pedestal=objects['pedestal'];spec=p.scene_plan.manifest.definition_for(pedestal)
    assert pedestal.motion_type=='fixed' and pedestal.position_m[2]-spec.half_extents_m[2]==pytest.approx(0.)
    assert pedestal.position_m[2]+spec.half_extents_m[2]==pytest.approx(.41)
    assert spec.half_extents_m==(.45,.8,.205)
    assert r.model.body('object__pedestal').id>=0
    bid=r.model.body('object__pedestal').id
    assert r.model.body_jntnum[bid]==0 and r.model.body_parentid[bid]==0
    assert r.model.geom('object__pedestal__geom').type[0]==mujoco.mjtGeom.mjGEOM_BOX
    assert (r.model.nq,r.model.nv)==(15,14) # 固定台に自由度を足さない。
    for name in ('left__base_link','right__base_link'):
        a=r.model.body(name).id;b=reference.model.body(name).id
        assert np.array_equal(r._data.xpos[a],reference._data.xpos[b])
        assert np.array_equal(r._data.xmat[a],reference._data.xmat[b])
    assert r.snapshot().joint_positions_rad==reference.snapshot().joint_positions_rad
    assert np.array_equal(r.model.actuator_gainprm,reference.model.actuator_gainprm)
    assert np.array_equal(r.model.actuator_forcerange,reference.model.actuator_forcerange)


def test_cube_is_supported_by_fixed_box_and_resets_with_pedestal():
    p=profile('dynamic-cube-push');r=p.build_model().provider
    initial=r.sample(frame_index=0,metadata={})
    s=advance(r,p,120)
    cube=next(o for o in s.dynamics['objects'] if o['instance_id']=='cube')
    assert cube['position_m'][2]==pytest.approx(.46,abs=.001)
    contacts=[c for c in s.dynamics['contacts'] if {c['geom1'],c['geom2']}=={'object__pedestal__geom','object__cube__geom'}]
    assert contacts
    force=sum(c['force_on_geom2_world_n'][2]*(1 if c['geom2']=='object__cube__geom' else -1) for c in contacts)
    assert force==pytest.approx(.981,abs=.005)
    assert not any('support__floor' in (c['geom1'],c['geom2']) and 'object__cube__geom' in (c['geom1'],c['geom2']) for c in s.dynamics['contacts'])
    advance(r,p,150,(.06,0.,0.))
    r.reset();reset=r.sample(frame_index=0,metadata={})
    assert reset.state.qpos==initial.state.qpos and reset.state.qvel==initial.state.qvel
    assert reset.geometry.objects==initial.geometry.objects


def test_cube_beyond_platform_edge_falls_to_real_floor():
    p=profile('dynamic-cube-push');m=p.scene_plan.manifest
    objects=tuple(replace(o,position_m=(1.15,.56,.461)) if o.instance_id=='cube' else o for o in m.objects)
    p=replace(p,scene_plan=replace(p.scene_plan,manifest=replace(m,objects=objects)))
    r=p.build_model().provider;s=advance(r,p,120)
    cube=next(o for o in s.geometry.objects if o.instance_id=='cube')
    assert cube.position_m[2]==pytest.approx(.05,abs=.001)


@pytest.mark.parametrize('name',['fast-arm-bimanual-gamepad','contact-debug-bimanual','dynamic-cube-drop','dynamic-fixed-contact','dynamic-cube-push'])
def test_hot_loop_does_not_allocate_new_mjdata(monkeypatch,name):
    p=load_launch_profile(name);r=p.build_model().provider
    commands=tuple(EndpointVelocity(a,(.01,0.,0.),'world') for a in r.endpoint_ids)
    ids={id(r._data),id(r._candidate_data)}
    def forbidden(*a,**k):raise AssertionError('MjData allocation in hot loop')
    monkeypatch.setattr(mujoco,'MjData',forbidden)
    for frame in range(8):
        before=r.snapshot();ticket=r.prepare(commands,p.dt_s)
        assert r.snapshot()==before
        r.commit(ticket);r.sample(frame_index=frame,metadata={})
        assert {id(r._data),id(r._candidate_data)}==ids
        with pytest.raises(ValueError):r.commit(ticket)


def test_pooled_candidates_recover_after_fault_and_never_share_live_data(monkeypatch):
    p=profile();r=p.build_model().provider;clean=p.build_model().provider
    commands=tuple(EndpointVelocity(a,(.01,0.,0.),'world') for a in r.endpoint_ids)
    original=mujoco.mj_step
    def bad(m,d):
        original(m,d);d.qvel[0]=float('nan')
    before=r.sample(frame_index=0,metadata={})
    with monkeypatch.context() as patch:
        patch.setattr(mujoco,'mj_step',bad)
        with pytest.raises(ValueError):r.prepare(commands,p.dt_s)
    assert r.sample(frame_index=0,metadata={}).state==before.state
    r.commit(r.prepare(commands,p.dt_s));clean.commit(clean.prepare(commands,p.dt_s))
    assert r.sample(frame_index=1,metadata={}).state==clean.sample(frame_index=1,metadata={}).state
    ticket=r.prepare(commands,p.dt_s);r.reset()
    with pytest.raises(ValueError):r.commit(ticket)
    clean.reset();r.commit(r.prepare(commands,p.dt_s));clean.commit(clean.prepare(commands,p.dt_s))
    assert r.sample(frame_index=2,metadata={}).state==clean.sample(frame_index=2,metadata={}).state


def test_fk_only_native_stage_matches_full_forward_without_touching_live_world():
    p=profile('dynamic-fixed-contact');r=p.build_model().provider
    ref=mujoco.MjData(r.model);rng=np.random.default_rng(590)
    live=r._data.qpos.copy()
    for arm in r.addresses:
        k=_SnapshotKinematics(r.model,r._data,arm)
        for _ in range(12):
            q=np.asarray(r.snapshot().joint_positions_rad[:4])+rng.uniform(-.1,.1,4)
            mujoco.mj_copyData(ref,r.model,r._data);ref.qpos[list(arm.qpos_addresses)]=q
            mujoco.mj_forward(r.model,ref)
            assert np.array_equal(k.forward(q),ref.site_xpos[arm.tip_site_id])
    assert np.array_equal(r._data.qpos,live)
