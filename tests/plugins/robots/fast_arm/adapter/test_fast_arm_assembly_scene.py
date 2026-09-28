"""全登録モデルでRobot本体と共通base sceneを同時に構成する。"""
import mujoco
import pytest
from xpotato_sim.plugins.robots.catalog import ROBOT_CATALOG
from xpotato_sim.runtime.experiment.contracts import PluginSelection


@pytest.mark.parametrize("name,count", [("single_original",1),("single_left",1),("single_right",1),("bimanual",2)])
def test_registered_models_include_exactly_one_shared_floor(name,count):
    spec=ROBOT_CATALOG.resolve_model(PluginSelection("fast_arm",1),PluginSelection(name,1))
    value=spec.build()
    model=value.provider.model
    planes=[i for i in range(model.ngeom) if model.geom_type[i] == mujoco.mjtGeom.mjGEOM_PLANE]
    assert len(planes)==1, "registered model must compose the shared base scene once"
    floor=model.geom("floor").id
    assert planes==[floor]
    assert tuple(model.geom_pos[floor]) == (0.,0.,0.)
    assert tuple(model.geom_quat[floor]) == (1.,0.,0.,0.)
    assert model.geom_bodyid[floor]==0
    assert model.nq==4*count and model.nv==4*count
    assert value.provider.built is value.viewer.built
    assert value.provider.snapshot().model_sha256==value.viewer.metadata["model_sha256"]
    # browserが使う公開resourceだけから同じfloorとjoint layoutを復元する。
    declaration=value.viewer.declaration
    xml=value.viewer.resources[declaration.model_resource_path].decode()
    assets={item.vfs_path:value.viewer.resources[item.resource_path] for item in declaration.vfs_assets}
    viewed=mujoco.MjModel.from_xml_string(xml,assets)
    assert viewed.geom("floor").id>=0
    assert viewed.ngeom==model.ngeom
    assert viewed.nq==model.nq


def test_shared_scene_reuses_original_wrapper_without_moving_floor_into_robot_core():
    from fast_arm_core.models import resolve_fast_arm_model
    from fast_arm_core.assembly_model import build_fast_arm_assembly_model, fast_arm_model_digest
    from xpotato_sim.plugins.robots.fast_arm.adapter.assembly_scene import build_fast_arm_assembly_scene
    from xpotato_sim.plugins.robots.fast_arm.adapter.resources import fast_arm_model_resource_bytes
    assembly=resolve_fast_arm_model("bimanual").assembly
    bare=build_fast_arm_assembly_model(assembly)
    composed=build_fast_arm_assembly_scene(assembly)
    expected_scene,_=fast_arm_model_resource_bytes()
    assert composed.xml==expected_scene
    assert composed.assets["arm.xml"]==bare.xml
    assert "floor" not in bare.xml.decode()
    assert composed.source_sha256==bare.source_sha256
    assert composed.model_sha256!=bare.model_sha256
    assert composed.model_sha256==fast_arm_model_digest(composed.xml,composed.assets)
    changed=composed.xml.replace(b'size="3 3 3"',b'size="4 4 4"')
    assert changed!=composed.xml
    assert fast_arm_model_digest(changed,composed.assets)!=composed.model_sha256


def test_scene_floor_parameters_match_legacy_single_arm():
    from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator
    old=build_fast_arm_simulator().model
    new=ROBOT_CATALOG.resolve_model(PluginSelection("fast_arm",1),PluginSelection("bimanual",1)).build().provider.model
    a,b=old.geom("floor").id,new.geom("floor").id
    for name in ("geom_pos","geom_quat","geom_size","geom_friction"):
        assert getattr(old,name)[a] == pytest.approx(getattr(new,name)[b])
    for name in ("geom_type","geom_bodyid","geom_contype","geom_conaffinity","geom_condim"):
        assert getattr(old,name)[a]==getattr(new,name)[b]
    mesh=[i for i in range(new.ngeom) if new.geom_type[i] == mujoco.mjtGeom.mjGEOM_MESH]
    assert len(mesh)==10
    assert all(new.geom_contype[i]==new.geom_conaffinity[i]==0 for i in mesh)


@pytest.mark.parametrize("scene", [b'<mujoco/>',b'<mujoco><include file="wrong.xml"/></mujoco>',b'<mujoco><include file="arm.xml"/><include file="arm.xml"/></mujoco>'])
def test_base_scene_include_drift_is_not_silently_accepted(monkeypatch,scene):
    from fast_arm_core.models import resolve_fast_arm_model
    from xpotato_sim.plugins.robots.fast_arm.adapter import assembly_scene
    monkeypatch.setattr(assembly_scene,"read_package_resource_bytes",lambda _:scene)
    with pytest.raises(ValueError,match="exactly one arm.xml"):
        assembly_scene.build_fast_arm_assembly_scene(resolve_fast_arm_model("bimanual").assembly)


def test_provider_rejects_scene_for_another_assembly():
    from fast_arm_core.models import resolve_fast_arm_model
    from xpotato_sim.plugins.robots.fast_arm.adapter.assembly_scene import build_fast_arm_assembly_scene
    from xpotato_sim.plugins.robots.fast_arm.adapter.coordinated import FastArmAssemblyMotionProvider
    scene=build_fast_arm_assembly_scene(resolve_fast_arm_model("single_left").assembly)
    with pytest.raises(ValueError,match="scene/assembly mismatch"):
        FastArmAssemblyMotionProvider(resolve_fast_arm_model("bimanual").assembly,built=scene)
