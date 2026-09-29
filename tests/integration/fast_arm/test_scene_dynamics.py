"""world/数値条件/freejoint/servoのnative検証。実機を使わない。"""
from dataclasses import replace
import json
import numpy as np
import mujoco
import pytest
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.schemas.coordinated import EndpointVelocity
from xpotato_sim.runtime.scene.objects import decode_object_scene, canonical
from xpotato_sim.runtime.execution.physics import DynamicsSettings


def profile(name="dynamic-cube-drop", *, model="bimanual", gravity=None, planes=None):
    p=load_launch_profile(name)
    from xpotato_sim.runtime.experiment.contracts import PluginSelection
    if model!="bimanual":
        ids={"single_original":{"left":"arm"},"single_left":{"left":"left"},"single_right":{"right":"right"}}[model]
        coord=json.loads(p.coordination_json);coord["side_to_endpoint"]=ids
        p=replace(p,model=PluginSelection(model,1),coordination_json=json.dumps(coord))
    if gravity is not None or planes is not None:
        w=p.scene_plan.manifest.world
        w=replace(w,gravity_m_s2=w.gravity_m_s2 if gravity is None else gravity,
                  support_planes=w.support_planes if planes is None else planes)
        p=replace(p,scene_plan=replace(p.scene_plan,manifest=replace(p.scene_plan.manifest,world=w)))
    return p


def advance(r,p,n,velocity=(0.,0.,0.)):
    commands=tuple(EndpointVelocity(a,velocity if a!="right" else (0.,0.,0.),"world") for a in r.endpoint_ids)
    for _ in range(n):
        r.commit(r.prepare(commands,p.dt_s))
    return r.sample(frame_index=n,metadata={})


@pytest.mark.parametrize("model",["single_original","single_left","single_right","bimanual"])
def test_dynamic_cube_gravity_floor_reset_and_named_robot_subset(model):
    p=profile(model=model);i=p.build_model();r=i.provider
    n=4*len(r.endpoint_ids)
    assert r.model.nq==n+7 and r.model.nv==n+6
    assert i.viewer.declaration.qpos_dimension==n
    assert i.viewer.declaration.scene_state_layout.qpos_dimension==n+7
    start=r.sample(frame_index=0,metadata={})
    sample=advance(r,p,120)
    pose=sample.geometry.objects[0]
    assert pose.position_m[2]==pytest.approx(.05,abs=3e-4)
    support=[c for c in sample.dynamics["contacts"] if c["role1"]["kind"]=="support" and c["role2"]["kind"]=="object"]
    assert support
    assert sum(c["force_on_geom2_world_n"][2] for c in support)==pytest.approx(.1*9.81,abs=.003)
    assert tuple(sample.state.qpos[a] for a in sample.robot_qpos_addresses)==sample.robot.joint_positions_rad
    r.reset();reset=r.sample(frame_index=0,metadata={})
    assert reset.state.qpos==start.state.qpos and reset.state.qvel==start.state.qvel
    assert reset.state.time_s==0 and reset.dynamics["objects"]==start.dynamics["objects"]


def test_freefall_matches_independent_analytic_solution_and_not_commanded_height():
    p=profile(planes=());r=p.build_model().provider
    sample=advance(r,p,6)
    t=sample.state.time_s;z=sample.geometry.objects[0].position_m[2]
    # semi-implicit/implicitfast: bounded first-order integration error versus continuous solution.
    assert abs(z-(.5-.5*9.81*t*t))<=9.81*t*p.scene_plan.dynamics.physics_dt_s
    assert sample.dynamics["objects"][0]["linear_velocity_world_m_s"][2]==pytest.approx(-9.81*t,abs=1e-8)
    assert sample.dynamics["joints"][0]["target_rad"]!=sample.dynamics["joints"][0]["position_rad"]


def test_zero_gravity_nonzero_velocity_and_body_angular_frame():
    p=profile(gravity=(0.,0.,0.),planes=())
    obj=p.scene_plan.manifest.objects[0]
    obj=replace(obj,orientation_wxyz=(float(np.sqrt(.5)),0.,0.,float(np.sqrt(.5))),
                initial_linear_velocity_m_s=(.1,.2,.3),initial_angular_velocity_rad_s=(1.,0.,0.))
    p=replace(p,scene_plan=replace(p.scene_plan,manifest=replace(p.scene_plan.manifest,objects=(obj,))))
    r=p.build_model().provider
    initial=r.sample(frame_index=0,metadata={})
    assert initial.dynamics["objects"][0]["angular_velocity_world_rad_s"]==pytest.approx((1.,0.,0.),abs=1e-12)
    sample=advance(r,p,6)
    assert sample.geometry.objects[0].position_m==pytest.approx(np.array(obj.position_m)+.1*np.array((.1,.2,.3)),abs=1e-9)
    assert sample.dynamics["objects"][0]["linear_velocity_world_m_s"]==pytest.approx((.1,.2,.3),abs=1e-9)


def test_fixed_object_does_not_fall_and_arm_produces_contact_force():
    p=profile("dynamic-fixed-contact");r=p.build_model().provider
    sample=advance(r,p,120,(.06,0.,0.))
    assert sample.geometry.objects[0].position_m==pytest.approx((.36,.56,.46),abs=1e-12)
    contacts=[c for c in sample.dynamics["contacts"] if c["role1"]["kind"]=="tool" or c["role2"]["kind"]=="tool"]
    assert contacts and any(np.linalg.norm(c["force_on_geom2_world_n"])>.05 for c in contacts)
    assert max(c["penetration_m"] for c in contacts)<.005
    assert any(abs(j["target_rad"]-j["position_rad"])>.005 for j in sample.dynamics["joints"])


def test_dynamic_cube_push_translates_object_with_finite_native_force():
    p=profile("dynamic-cube-push");r=p.build_model().provider
    sample=advance(r,p,180,(.06,0.,0.))
    obj=sample.dynamics["objects"][0]
    assert obj["position_m"][0]>.38
    assert obj["position_m"][2]>.455
    assert np.all(np.isfinite(sample.state.qpos))


def test_scene_and_execution_conditions_have_independent_digests():
    p=profile();d=p.scene_plan.manifest.to_document()
    assert decode_object_scene(canonical(d))==p.scene_plan.manifest
    assert replace(p.scene_plan.manifest,world=replace(p.scene_plan.manifest.world,gravity_m_s2=(0.,0.,0.))).digest!=p.scene_plan.manifest.digest
    assert replace(p.scene_plan.dynamics,physics_dt_s=1/1200).digest!=p.scene_plan.dynamics.digest
    with pytest.raises(ValueError,match="integer multiple"):
        p.scene_plan.dynamics.substeps(.017)


def test_prepared_dynamic_step_does_not_mutate_live_world_and_is_single_use():
    p=profile();r=p.build_model().provider
    commands=tuple(EndpointVelocity(a,(0.,0.,0.),"world") for a in r.endpoint_ids)
    before=r.sample(frame_index=0,metadata={});candidate=r.prepare(commands,p.dt_s)
    assert r.sample(frame_index=0,metadata={}).state.qpos==before.state.qpos
    r.commit(candidate)
    with pytest.raises(ValueError):r.commit(candidate)
    candidate=r.prepare(commands,p.dt_s);r.reset()
    with pytest.raises(ValueError):r.commit(candidate)


@pytest.mark.parametrize("bad", [
    {"physics_dt_s":0}, {"physics_dt_s":True}, {"integrator":"fallback"},
    {"solver":"unknown"}, {"iterations":False}, {"iterations":1001},
    {"tolerance":float("nan")}, {"max_joint_speed_rad_s":0}, {"max_tracking_error_rad":-1},
])
def test_invalid_numerical_settings_are_not_repaired(bad):
    p=profile()
    with pytest.raises((TypeError,ValueError)):
        replace(p.scene_plan.dynamics,**bad)


def test_old_fixed_scene_document_bytes_remain_v1_and_reject_dynamic():
    p=load_launch_profile("contact-debug-bimanual")
    doc=p.scene_plan.manifest.to_document()
    assert doc["schema_version"]=="object-scene/v1" and "world" not in doc
    assert all("initial_velocity" not in o for o in doc["objects"])
    doc["objects"][0]["motion_type"]="dynamic"
    with pytest.raises(ValueError):decode_object_scene(canonical(doc))


def test_empty_support_set_does_not_inherit_robot_floor():
    p=profile(planes=());r=p.build_model().provider
    assert not any(int(t)==int(mujoco.mjtGeom.mjGEOM_PLANE) for t in r.model.geom_type)
    assert r.model.opt.gravity==pytest.approx((0.,0.,-9.81))


def test_dynamic_freejoint_layout_roundtrip_and_false_addresses_fail():
    from xpotato_sim.schemas.scene_state import SceneStateLayout
    from xpotato_sim.runtime.composition.viewer_robot_declaration import decode_viewer_robot_declaration
    i=profile().build_model();d=i.viewer.declaration
    assert decode_viewer_robot_declaration(d.to_document())==d
    layout=d.scene_state_layout
    assert SceneStateLayout.from_document(layout.to_document())==layout
    with pytest.raises(ValueError):
        replace(layout,joints=layout.joints[:-1]+(replace(layout.joints[-1],qpos_address=0),))
    with pytest.raises(ValueError):replace(layout,qvel_dimension=layout.qvel_dimension+1)


def test_dynamic_failure_does_not_publish_half_step_or_object_motion(monkeypatch):
    p=profile();r=p.build_model().provider
    before=r.sample(frame_index=0,metadata={});original=mujoco.mj_step
    def invalid(model,data):
        original(model,data)
        data.qvel[0]=float("nan")
    monkeypatch.setattr(mujoco,"mj_step",invalid)
    commands=tuple(EndpointVelocity(a,(0.,0.,0.),"world") for a in r.endpoint_ids)
    with pytest.raises(ValueError,match="nonfinite"):
        r.prepare(commands,p.dt_s)
    after=r.sample(frame_index=0,metadata={})
    assert after.state.qpos==before.state.qpos and after.state.qvel==before.state.qvel and after.state.time_s==0


def test_stale_input_freezes_complete_dynamic_world_and_never_auto_restarts():
    from xpotato_sim.runtime.execution.coordinated import CoordinatedRuntime
    from xpotato_sim.schemas.coordinated import CoordinatedInput
    p=profile();r=p.build_model().provider
    runtime=CoordinatedRuntime(r,epoch="run-1",dt_s=p.dt_s,max_input_age_s=.2)
    endpoints=tuple(EndpointVelocity(a,(0.,0.,0.),"world") for a in r.endpoint_ids)
    value=CoordinatedInput(endpoints,"source-1",0,0.,0.,True,True,"0"*64)
    assert runtime.tick(value,epoch="run-1",now_s=0.).state=="running"
    runtime.tick(value,epoch="run-1",now_s=.01)
    before=r.sample(frame_index=2,metadata={})
    assert runtime.tick(value,epoch="run-1",now_s=.3).state=="faulted"
    fresh=replace(value,source_sequence=1,received_at_s=.3,source_timestamp_s=.3)
    assert runtime.tick(fresh,epoch="run-1",now_s=.3).state=="faulted"
    after=r.sample(frame_index=3,metadata={})
    assert after.state.qpos==before.state.qpos and after.state.qvel==before.state.qvel and after.state.time_s==before.state.time_s
    runtime.restart(epoch="run-2",now_s=.4)
    assert r.sample(frame_index=0,metadata={}).geometry.objects[0].position_m[2]==.5


def test_tool_frame_is_resolved_from_observed_not_desired_orientation(monkeypatch):
    p=profile("dynamic-fixed-contact");r=p.build_model().provider
    advance(r,p,90,(.06,0.,0.))
    current=r._data
    planning=r._planning_state(current)
    assert not np.allclose(current.site_xmat,planning.site_xmat)
    tool_commands=tuple(EndpointVelocity(a,(.01,0.,0.),"tool") for a in r.endpoint_ids)
    world_commands=tuple(EndpointVelocity(a.arm_id,tuple(float(v) for v in current.site_xmat[a.tip_site_id].reshape(3,3)@np.array((.01,0.,0.))),"world") for a in r.addresses)
    tool=r.prepare(tool_commands,p.dt_s);world=r.prepare(world_commands,p.dt_s)
    assert tool.command.joint_angles_rad==pytest.approx(world.command.joint_angles_rad,abs=1e-12)
    assert tool.predicted.joint_positions_rad==pytest.approx(world.predicted.joint_positions_rad,abs=1e-12)


def test_multiple_dynamic_objects_and_fixed_object_share_one_layout_and_reset():
    p=profile();obj=p.scene_plan.manifest.objects[0]
    objects=(replace(obj,instance_id="drop_a",position_m=(.4,-.2,.5)),
             replace(obj,instance_id="drop_b",position_m=(.4,.2,.5)),
             replace(obj,instance_id="fixture",motion_type="fixed",position_m=(.6,0.,.4)))
    manifest=replace(p.scene_plan.manifest,objects=objects)
    assert replace(manifest,objects=tuple(reversed(objects))).digest==manifest.digest
    p=replace(p,scene_plan=replace(p.scene_plan,manifest=manifest))
    r=p.build_model().provider
    assert (r.model.nq,r.model.nv)==(22,20)
    initial=r.sample(frame_index=0,metadata={})
    state=advance(r,p,100)
    measured={o["instance_id"]:o for o in state.dynamics["objects"]}
    assert measured["fixture"]["position_m"]==pytest.approx((.6,0.,.4))
    assert measured["drop_a"]["position_m"][2]==pytest.approx(.05,abs=.001)
    assert measured["drop_b"]["position_m"][2]==pytest.approx(.05,abs=.001)
    r.reset();assert r.sample(frame_index=0,metadata={}).state.qpos==initial.state.qpos


def test_world_definition_is_reusable_without_dynamic_execution_for_fixed_objects():
    from xpotato_sim.runtime.composition.launch_profile import decode_launch_profile
    p=profile("dynamic-fixed-contact")
    raw=json.loads(p.document_json);raw["schema_version"]="xpotato-sim-launch-profile/v3";del raw["execution"]["dynamics"]
    k=decode_launch_profile(canonical(raw),source_path=p.source_path)
    assert k.scene_plan.manifest.digest==p.scene_plan.manifest.digest
    assert k.scene_plan.dynamics is None
    instance=k.build_model();r=instance.provider
    assert r.snapshot().execution_semantics=="coordinated_joint_position_kinematic/v1"
    assert r.model.opt.gravity==pytest.approx((0,0,-9.81))
    sample=r.sample(frame_index=0,metadata={})
    assert sample.dynamics is None and not sample.geometry.dynamic
    assert sample.geometry.to_document()["force_status"]=="not_evaluated_kinematic"
    with pytest.raises(ValueError,match="silently frozen"):
        replace(p.scene_plan,dynamics=None,manifest=profile().scene_plan.manifest)
