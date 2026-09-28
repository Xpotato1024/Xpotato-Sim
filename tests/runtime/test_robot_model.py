"""Robot model registrationのidentity・factory・描画境界を検査する。"""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from xpotato_sim.plugins.robots.catalog import ROBOT_CATALOG
from xpotato_sim.runtime.composition.robot_model import RobotModelInstance
from xpotato_sim.runtime.experiment.contracts import PluginSelection


def spec(name="bimanual"):
    return ROBOT_CATALOG.resolve_model(PluginSelection("fast_arm",1),PluginSelection(name,1))


def test_catalog_selection_does_not_run_model_factory(monkeypatch):
    from xpotato_sim.plugins.robots.fast_arm.adapter.coordinated import FastArmAssemblyMotionProvider
    monkeypatch.setattr(FastArmAssemblyMotionProvider,"__init__",lambda *a,**k:pytest.fail("model construction during lookup"))
    assert spec().endpoint_ids==("left","right")


@pytest.mark.parametrize("mutation", [
    {"endpoint_ids":("left","left")}, {"joint_names":()},
    {"configuration_json":"{\"x\": 1}"}, {"configuration_json":"[]"},
    {"configuration_json":"{\"x\":NaN}"}, {"build_instance":None},
])
def test_bad_static_declaration_is_rejected(mutation):
    with pytest.raises((ValueError,TypeError)):
        replace(spec(),**mutation)


def test_invalid_factory_type_is_rejected():
    with pytest.raises(TypeError,match="invalid instance"):
        replace(spec(),build_instance=lambda:None).build()


def test_incompatible_single_provider_cannot_back_bimanual_registration():
    single=spec("single_left").build()
    with pytest.raises(ValueError,match="identity/order"):
        replace(spec(),build_instance=lambda:single).build()


def test_viewer_wrong_order_is_rejected_even_at_same_dimension():
    value=spec().build()
    declaration=replace(value.viewer.declaration,joint_names=tuple(reversed(value.viewer.declaration.joint_names)))
    viewer=SimpleNamespace(declaration=declaration,metadata=value.viewer.metadata)
    with pytest.raises(ValueError,match="joint mismatch"):
        replace(spec(),build_instance=lambda:RobotModelInstance(value.provider,viewer)).build()


def test_viewer_different_model_bytes_are_rejected_even_at_same_order():
    value=spec().build()
    metadata={**value.viewer.metadata,"model_sha256":"f"*64}
    viewer=SimpleNamespace(declaration=value.viewer.declaration,metadata=metadata)
    with pytest.raises(ValueError,match="artifact digest"):
        replace(spec(),build_instance=lambda:RobotModelInstance(value.provider,viewer)).build()
