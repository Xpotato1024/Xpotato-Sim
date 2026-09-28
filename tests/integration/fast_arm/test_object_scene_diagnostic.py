"""単腕/双腕×物体数と、独立なsphere-box oracleでnative contactを検査する。"""
from dataclasses import replace
from types import MappingProxyType
import xml.etree.ElementTree as ET
import mujoco
import numpy as np
import pytest
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.scene.contracts import ObjectSceneBuildRequest, ModelScenePlan, ToolColliderBinding
from xpotato_sim.runtime.scene.composition import compose_object_scene
from xpotato_sim.runtime.scene.measurement import SceneGeometryObserver
from xpotato_sim.plugins.environments.object_scene_environment.implementation import FixedObjectSceneProvider


@pytest.mark.parametrize("suffix",["single","left","right","bimanual"])
@pytest.mark.parametrize("count",[1,2])
def test_shared_model_factory_for_all_arm_and_object_counts(suffix,count):
    p=load_launch_profile("contact-debug-"+suffix)
    manifest=FixedObjectSceneProvider().resolve_parameters({"preset":"two_cubes"})
    manifest=replace(manifest,objects=manifest.objects[:count])
    plan=replace(p.scene_plan,manifest=manifest)
    value=p.model_registration().build(plan);base=p.model_registration().build()
    sample=value.provider.sample(frame_index=3,metadata={})
    assert len(sample.geometry.objects)==count and sample.geometry.contacts==()
    assert sample.state.qpos==base.provider.snapshot().joint_positions_rad
    assert sample.robot.model_sha256==value.viewer.metadata["model_sha256"]
    assert value.viewer.built is value.provider.built
    # 元Robotの慣性・mass・joint/actuator規約を名前で照合。固定cubeの質量は別body。
    for i in range(base.provider.model.nbody):
        name=base.provider.model.body(i).name
        j=value.provider.model.body(name).id
        for attr in ("body_mass","body_inertia","body_ipos","body_iquat"):
            assert getattr(value.provider.model,attr)[j]==pytest.approx(getattr(base.provider.model,attr)[i])
    for attr in ("qpos0","jnt_axis","actuator_forcerange","actuator_ctrlrange"):
        assert np.asarray(getattr(value.provider.model,attr))==pytest.approx(np.asarray(getattr(base.provider.model,attr)))
    assert sample.robot.joint_names==base.provider.snapshot().joint_names
    assert value.provider.model.nq==base.provider.model.nq
    assert value.provider.model.npair==count*len(value.provider.endpoint_ids)
    assert sample.geometry.to_document()["force_n"] is None
    value.provider.reset()
    assert value.provider.sample(frame_index=0,metadata={}).geometry.objects==sample.geometry.objects


def fixture(reverse=False):
    base=b'<mujoco><worldbody><body name="tool"><freejoint/><geom name="tool_geom" type="sphere" size="0.01" mass="0.1" contype="0" conaffinity="0"/></body><geom name="floor" type="plane" size="1 1 .01" pos="0 0 -1"/></worldbody></mujoco>'
    m=FixedObjectSceneProvider().resolve_parameters({"preset":"cube_left"})
    m=replace(m,objects=(replace(m.objects[0],position_m=(.062,0,0)),))
    scene=compose_object_scene(ObjectSceneBuildRequest(base,{},m,(ToolColliderBinding("tool","tool_geom",(.8,0,0)),)))
    if reverse:
        xml=ET.fromstring(scene.xml);pair=xml.find("contact/pair");a,b=pair.get("geom1"),pair.get("geom2");pair.set("geom1",b);pair.set("geom2",a)
        scene=replace(scene,xml=ET.tostring(xml))
    model=mujoco.MjModel.from_xml_string(scene.xml.decode());data=mujoco.MjData(model)
    observer=SceneGeometryObserver(model,scene,"a"*64)
    return model,data,observer


@pytest.mark.parametrize("reverse",[False,True])
@pytest.mark.parametrize("x,distance,relation",[(0.,.002,"near"),(.002,0.,"touching"),(.01,-.008,"penetrating"),(-.1,None,None)])
def test_native_point_normal_distance_without_force_estimation(reverse,x,distance,relation,monkeypatch):
    model,data,observer=fixture(reverse)
    data.qpos[0]=x;mujoco.mj_forward(model,data)
    monkeypatch.setattr(mujoco,"mj_contactForce",lambda *a:pytest.fail("geometry diagnosis must not extract force"))
    obs=observer.observe(data,frame_index=2)
    if distance is None:
        assert obs.contacts==()
    else:
        assert len(obs.contacts)==1
        c=obs.contacts[0]
        assert c.distance_m==pytest.approx(distance,abs=1e-12)
        assert c.normal_world==pytest.approx((1.,0.,0.))
        assert c.point_world_m==pytest.approx(((x+.01+.012)/2,0.,0.),abs=1e-12)
        assert c.penetration_m==pytest.approx(max(0.,-distance),abs=1e-12)
        if abs(distance)>1e-12: assert c.relation==relation
    assert obs.to_document()["force_status"]=="not_evaluated_kinematic"
    assert obs.to_document()["force_n"] is None


def test_initial_penetration_rejected_by_native_geometry():
    model,data,observer=fixture()
    data.qpos[0]=.01;mujoco.mj_forward(model,data)
    with pytest.raises(ValueError,match="initial scene penetration"):observer.validate_initial(data)


def test_distinct_fixed_boxes_cannot_overlap_at_start():
    p=load_launch_profile("contact-debug-bimanual");m=p.scene_plan.manifest
    duplicate=replace(m.objects[1],position_m=m.objects[0].position_m)
    with pytest.raises(ValueError,match="initial scene penetration"):
        p.model_registration().build(replace(p.scene_plan,manifest=replace(m,objects=(m.objects[0],duplicate))))


def test_fixed_box_below_floor_rejected():
    p=load_launch_profile("contact-debug-left");m=p.scene_plan.manifest
    with pytest.raises(ValueError,match="initial scene penetration"):
        p.model_registration().build(replace(p.scene_plan,manifest=replace(m,objects=(replace(m.objects[0],position_m=(.36,.56,0.)),))))


def test_model_asset_roundtrip_matches_viewer_resource():
    value=load_launch_profile("contact-debug-bimanual").build_model();built=value.viewer.built
    model=mujoco.MjModel.from_xml_string(built.xml.decode(),dict(built.assets))
    assert model.ngeom==value.provider.model.ngeom
    assert model.nq==8
    for name in ("object__cube_left__geom","object__cube_right__geom","left__diagnostic_tool_collision","right__diagnostic_tool_collision"):
        assert model.geom(name).size==pytest.approx(value.provider.model.geom(name).size)


@pytest.mark.parametrize("gap,allowed",[(.001,True),(0.,False),(-.01,False)])
def test_fixed_box_pairs_require_positive_native_separation(gap,allowed):
    p=load_launch_profile("contact-debug-bimanual");m=p.scene_plan.manifest
    a=m.objects[0];b=replace(m.objects[1],position_m=(a.position_m[0]+.1+gap,*a.position_m[1:]))
    plan=replace(p.scene_plan,manifest=replace(m,objects=(a,b)))
    if allowed: p.model_registration().build(plan)
    else:
        with pytest.raises(ValueError,match="initial scene penetration"):
            p.model_registration().build(plan)


@pytest.mark.parametrize("bindings",[(("wrong","object__cube_left__geom"),),(("cube_left","tool_geom"),),(("cube_left","a"),("cube_left","b"))])
def test_composed_scene_cannot_change_instance_binding(bindings):
    model,data,observer=fixture()
    with pytest.raises(ValueError): replace(observer.scene,object_geoms=bindings)
