"""写真由来の取付面方向と肩中心を、実装quaternionのコピーではなく空間量で検証。"""
from math import sqrt
import mujoco
import numpy as np
import pytest
from fast_arm_core.models import resolve_fast_arm_model, torso_shoulder_instance
from fast_arm_core.assembly_model import build_fast_arm_assembly_model
from fast_arm_core.assembly import FastArmAssembly
from xpotato_sim.plugins.robots.catalog import ROBOT_CATALOG
from xpotato_sim.runtime.experiment.contracts import PluginSelection


def loaded(name):
    definition = resolve_fast_arm_model(name)
    built = build_fast_arm_assembly_model(definition.assembly)
    model = mujoco.MjModel.from_xml_string(built.xml.decode(), dict(built.assets))
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    mujoco.mj_forward(model, data)
    return definition, model, data


@pytest.mark.parametrize("name", ["single_left", "single_right", "bimanual"])
def test_mount_normal_shoulder_pivot_and_home_direction(name):
    definition, model, data = loaded(name)
    for arm in definition.assembly.instances:
        side = 1 if arm.arm_id == "left" else -1
        center = data.xanchor[model.joint(arm.name("sholder_joint_1")).id]
        assert center == pytest.approx((0., side * .4, .7), abs=1e-12)
        # sourceの取付板はlocal XZ面。鏡映時は極性vectorの法線も鏡映する。
        normal_local = np.array((0., 1. if arm.mirror_y else -1., 0.))
        normal = data.xmat[model.body(arm.name("base_link")).id].reshape(3,3) @ normal_local
        assert normal == pytest.approx((0., side * sqrt(3)/2, -.5), abs=1e-12)
        # 取付板の鉛直に対する30度と、source homeで上腕が下向きになること。
        upper = data.xpos[model.body(arm.name("fore_arm_link")).id] - data.xpos[model.body(arm.name("upper_arm_link")).id]
        assert upper / np.linalg.norm(upper) == pytest.approx((0.,0.,-1.), abs=1e-12)
        tip = data.site_xpos[model.site(arm.name("tip")).id]
        assert tip[0] > center[0]  # 両前腕は胴体前方。片側だけ後ろへ曲げない。


@pytest.mark.parametrize("q", [(0., -.5235987756, 0., -1.04719755), (.1,-.7,.2,-.8)])
def test_bimanual_whole_geometry_is_sagittally_symmetric(q):
    definition, model, data = loaded("bimanual")
    data.qpos[:] = q + q
    mujoco.mj_forward(model,data)
    S=np.diag((1.,-1.,1.))
    for body in ("base_link","sholder_link_1","sholder_link_2","upper_arm_link","fore_arm_link"):
        left=model.body("left__"+body).id; right=model.body("right__"+body).id
        assert data.xpos[right] == pytest.approx(S@data.xpos[left], abs=1e-11)
    assert data.site_xpos[model.site("right__tip").id] == pytest.approx(S@data.site_xpos[model.site("left__tip").id], abs=1e-11)


@pytest.mark.parametrize("side", ["left", "right"])
def test_single_model_and_bimanual_component_have_same_geometry(side):
    _, solo, sd=loaded("single_"+side); _, pair,pd=loaded("bimanual")
    for body in ("base_link","sholder_link_1","sholder_link_2","upper_arm_link","fore_arm_link"):
        assert sd.xpos[solo.body(side+"__"+body).id] == pytest.approx(pd.xpos[pair.body(side+"__"+body).id])
        assert sd.xmat[solo.body(side+"__"+body).id] == pytest.approx(pd.xmat[pair.body(side+"__"+body).id])


@pytest.mark.parametrize("name,count", [("single_original",1),("single_left",1),("single_right",1),("bimanual",2)])
def test_all_models_use_one_registered_build_type(name,count):
    spec = ROBOT_CATALOG.resolve_model(PluginSelection("fast_arm",1),PluginSelection(name,1))
    value = spec.build()
    assert len(spec.endpoint_ids)==count
    assert value.provider.snapshot().joint_names==value.viewer.declaration.joint_names==spec.joint_names
    assert value.viewer.declaration.qpos_dimension==4*count
    assert value.viewer.built is value.provider.built


def test_source_original_keeps_canonical_single_arm_pose():
    _, model, data = loaded("single_original")
    from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator
    expected = build_fast_arm_simulator().snapshot()
    assert tuple(data.qpos)==expected.qpos
    actual=data.site_xpos[model.site("arm__tip").id]
    reference=next(s.position_m for s in expected.sites if s.name=="tip")
    assert actual==pytest.approx(reference,abs=1e-12)


def test_mount_translation_fixes_shoulder_center_at_arbitrary_cant():
    for angle in (0.,15.,30.,60.):
        arm=torso_shoulder_instance(arm_id="custom",side="left",shoulder_center_m=(.3,.2,1.1),cant_degrees=angle)
        built=build_fast_arm_assembly_model(FastArmAssembly((arm,)))
        model=mujoco.MjModel.from_xml_string(built.xml.decode(),dict(built.assets)); data=mujoco.MjData(model)
        mujoco.mj_forward(model,data)
        assert data.xanchor[model.joint("custom__sholder_joint_1").id] == pytest.approx((.3,.2,1.1),abs=1e-12)
