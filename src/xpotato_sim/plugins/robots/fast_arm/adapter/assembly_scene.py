"""腕assemblyと既存の共通base sceneを一度だけ合成する。床をcoreへ入れない。"""
from dataclasses import replace
from types import MappingProxyType
import xml.etree.ElementTree as ET

from fast_arm_core.assembly import FastArmAssembly
from fast_arm_core.assembly_model import (
    FastArmAssemblyModel, build_fast_arm_assembly_model, fast_arm_model_digest,
)
from xpotato_sim.runtime.composition.robot_resource import read_package_resource_bytes
from .resources import FAST_ARM_SCENE_RESOURCE


def build_fast_arm_assembly_scene(assembly: FastArmAssembly) -> FastArmAssemblyModel:
    """単腕・双腕とも同じscene.xmlを使い、include先だけを生成assemblyに結ぶ。"""
    body = build_fast_arm_assembly_model(assembly)
    scene = read_package_resource_bytes(FAST_ARM_SCENE_RESOURCE)
    root = ET.fromstring(scene)
    includes = root.findall(".//include")
    if root.tag != "mujoco" or len(includes) != 1 or includes[0].get("file") != "arm.xml":
        raise ValueError("base scene must include exactly one arm.xml; review resource composition")
    if "arm.xml" in body.assets:
        raise ValueError("assembly assets collide with the base-scene include")
    assets = MappingProxyType({"arm.xml": body.xml, **body.assets})
    return replace(body, xml=scene, assets=assets, model_sha256=fast_arm_model_digest(scene, assets))
