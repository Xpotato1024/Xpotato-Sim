"""写真由来の取付面方向と肩中心を、実装quaternionのコピーではなく空間量で検証。"""
from math import sqrt
import mujoco
import numpy as np
import pytest
from fast_arm_core.models import (
    resolve_fast_arm_model, torso_shoulder_instance,
    SHOULDER_CENTER_SPACING_M, SHOULDER_CENTER_HEIGHT_M,
)
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
        assert center == pytest.approx((0., side * SHOULDER_CENTER_SPACING_M / 2, SHOULDER_CENTER_HEIGHT_M), abs=1e-12)
        # sourceの取付板はlocal XZ面。鏡映時は極性vectorの法線も鏡映する。
        normal_local = np.array((0., 1. if arm.mirror_y else -1., 0.))
        normal = data.xmat[model.body(arm.name("base_link")).id].reshape(3,3) @ normal_local
        assert normal == pytest.approx((0., side * sqrt(3)/2, .5), abs=1e-12)
        # 法線の上下だけでなく、板の上端が下端より胴体側へ寄ることを独立に固定する。
        plate_up = data.xmat[model.body(arm.name("base_link")).id].reshape(3,3) @ np.array((0.,0.,1.))
        top = center + .1 * plate_up
        bottom = center - .1 * plate_up
        assert top[2] > bottom[2]
        assert side * (top[1] - bottom[1]) < 0  # 上端は内側、下端は外側
        assert abs(top[1] - bottom[1]) / (top[2] - bottom[2]) == pytest.approx(1 / sqrt(3), abs=1e-12)
        # 取付を変えてもjoint homeを見た目に合わせて補正しない。実物の関節姿勢は未校正。
        assert tuple(data.qpos) == tuple(model.key_qpos[0])
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



def test_image_inferred_spacing_is_a_shoulder_datum_not_the_98mm_hole_distance():
    _, model, data = loaded("bimanual")
    left = data.xanchor[model.joint("left__sholder_joint_1").id]
    right = data.xanchor[model.joint("right__sholder_joint_1").id]
    # 採用した画像推定値を固定するacceptance。定数のコピーで旧0.8 mを見逃さない。
    assert left - right == pytest.approx((0., .290, 0.), abs=1e-12)
    assert left[2] == right[2] == pytest.approx(.7)
    plates = []
    for side in ("left", "right"):
        body = model.body(side + "__base_link").id
        # 原本BaseLinkの平板はlocal y=0..0.006 m。その中央面を測る。
        plates.append(data.xpos[body] + data.xmat[body].reshape(3,3) @ np.array((0., .003 if side == "left" else -.003, 0.)))
    separation = plates[0][1] - plates[1][1]
    assert separation == pytest.approx(.290 - 2 * .072 * sqrt(3) / 2, abs=1e-12)
    assert .14 < separation < .18  # CAD投影からの約0.16 mと整合。98 mmの穴間ではない。
    assert plates[0][2] == pytest.approx(plates[1][2], abs=1e-12)



@pytest.mark.parametrize("side", ["left", "right"])
def test_shortening_only_translates_each_arm_without_rescaling_or_reorienting(side):
    from dataclasses import replace
    chosen = resolve_fast_arm_model("single_" + side).assembly
    current = chosen.instances[0]
    sign = 1 if side == "left" else -1
    shift = sign * (.400 - .145)
    old = replace(current, position_m=(current.position_m[0], current.position_m[1] + shift, current.position_m[2]))
    models = []
    for assembly in (chosen, FastArmAssembly((old,))):
        artifact = build_fast_arm_assembly_model(assembly)
        model = mujoco.MjModel.from_xml_string(artifact.xml.decode(), dict(artifact.assets))
        data = mujoco.MjData(model)
        mujoco.mj_resetDataKeyframe(model, data, 0); mujoco.mj_forward(model, data)
        models.append((artifact, model, data))
    (a, ma, da), (b, mb, db) = models
    assert a.source_sha256 == b.source_sha256
    assert a.assets == b.assets
    assert a.model_sha256 != b.model_sha256
    assert da.qpos == pytest.approx(db.qpos)
    assert ma.body_mass == pytest.approx(mb.body_mass)
    assert ma.actuator_forcerange == pytest.approx(mb.actuator_forcerange)
    for suffix in ("base_link", "sholder_link_1", "sholder_link_2", "upper_arm_link", "fore_arm_link"):
        i, j = ma.body(side + "__" + suffix).id, mb.body(side + "__" + suffix).id
        assert db.xpos[j] - da.xpos[i] == pytest.approx((0., shift, 0.), abs=1e-12)
        assert db.xmat[j] == pytest.approx(da.xmat[i], abs=1e-12)
