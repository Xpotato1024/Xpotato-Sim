"""左右を名前・配置・校正と混同しないcore宣言の検証。"""
from dataclasses import replace
import math

import pytest

from fast_arm_core.assembly import FastArmAssembly, FastArmInstance
from fast_arm_core.assembly_model import build_fast_arm_assembly_model


def arm(name="right", mirror=False):
    return FastArmInstance(name, mirror, (0,0,0), (1,0,0,0))


@pytest.mark.parametrize("kwargs", [
    {"arm_id":""}, {"arm_id":"../left"}, {"arm_id":"Left"},
    {"mirror_y":1}, {"position_m":(0,math.nan,0)}, {"position_m":(True,0,0)},
    {"position_m":(0,0)}, {"quaternion_wxyz":(2,0,0,0)},
    {"quaternion_wxyz":(0,0,0,0)}, {"quaternion_wxyz":(math.inf,0,0,0)},
])
def test_instance_rejects_ambiguous_or_invalid_fields(kwargs):
    with pytest.raises((ValueError, TypeError)):
        replace(arm(), **kwargs)


def test_instance_copies_mutable_vectors_and_canonicalizes_quaternion_sign():
    pos, quat = [1,2,3], [-1,0,0,0]
    instance = FastArmInstance("operator_left", False, pos, quat)
    pos[0] = 42; quat[0] = 0
    assert instance.position_m == (1,2,3)
    assert instance.quaternion_wxyz == (1,0,0,0)
    # 名称は鏡映やOSC targetを意味しない。
    assert instance.mirror_y is False
    assert FastArmAssembly((instance,)).configuration_sha256 == FastArmAssembly((
        replace(instance, quaternion_wxyz=(1,0,0,0)),)).configuration_sha256


@pytest.mark.parametrize("instances", [(), [], (arm(), arm()), (arm(),arm("left"),arm("third")), ("right",)])
def test_assembly_rejects_empty_duplicate_or_invalid_instances(instances):
    with pytest.raises((ValueError, TypeError)):
        FastArmAssembly(instances)


def test_assembly_preserves_declared_order_and_identifies_model_configuration():
    left, right = arm("left", True), arm()
    assembly = FastArmAssembly((left,right))
    assert assembly.arm_ids == ("left","right")
    assert assembly.joint_names[0] == "left__sholder_joint_1"
    assert assembly.configuration_sha256 != FastArmAssembly((right,left)).configuration_sha256
    assert assembly.configuration_sha256 != FastArmAssembly((replace(left, mirror_y=False),right)).configuration_sha256


def test_generated_model_is_deterministic_and_does_not_modify_source_resources():
    from importlib.resources import files
    source = files("fast_arm_core").joinpath("resources/model/arm.xml")
    original = source.read_bytes()
    assembly = FastArmAssembly((arm(),arm("left", True)))
    first, second = (build_fast_arm_assembly_model(assembly) for _ in range(2))
    assert first.xml == second.xml
    assert first.model_sha256 == second.model_sha256
    assert first.assets == second.assets and len(first.assets) == 5
    assert source.read_bytes() == original
    with pytest.raises(TypeError):
        first.assets["new.stl"] = b"invalid"
    single = build_fast_arm_assembly_model(FastArmAssembly((arm(),)))
    assert single.source_sha256 == first.source_sha256
    assert single.model_sha256 != first.model_sha256
