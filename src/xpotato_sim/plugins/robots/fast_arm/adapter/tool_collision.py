"""FastArm所有の診断用tool collider。元のSTL/慣性/関節基準は変更しない。"""
from dataclasses import replace
from types import MappingProxyType
import xml.etree.ElementTree as ET
from fast_arm_core.assembly_model import FastArmAssemblyModel, fast_arm_model_digest
from xpotato_sim.runtime.scene.contracts import ToolColliderBinding

TOOL_COLLISION_PROFILE = "tool_sphere_10mm/v1"
TOOL_COLLISION_RADIUS_M = .01


def add_tool_colliders(built: FastArmAssemblyModel, profile: str):
    """各instanceのtip siteと同じbody/local位置へmassless sphereを一つ追加する。"""
    if profile != TOOL_COLLISION_PROFILE:
        raise ValueError("unsupported FastArm collision profile")
    # 共通base sceneはarm.xmlをincludeする。資源の参照関係を維持し、Robot資源だけを更新する。
    included = "arm.xml" in built.assets
    tree=ET.fromstring(built.assets["arm.xml"] if included else built.xml)
    bindings=[]
    for arm in built.assembly.instances:
        matches=[(body,site) for body in tree.iter("body") for site in body.findall("site") if site.get("name")==arm.name("tip")]
        if len(matches)!=1:
            raise ValueError("ambiguous FastArm tool site")
        body,site=matches[0]
        name=arm.name("diagnostic_tool_collision")
        if any(g.get("name")==name for g in tree.iter("geom")):
            raise ValueError("duplicate diagnostic collider")
        ET.SubElement(body,"geom",{"name":name,"type":"sphere","size":str(TOOL_COLLISION_RADIUS_M),
            "pos":site.get("pos","0 0 0"),"mass":"0","contype":"0","conaffinity":"0","group":"0",
            "rgba":"0.05 0.75 0.85 0.5","friction":"0.8 0 0"})
        bindings.append(ToolColliderBinding(arm.arm_id,name,(.8,0.,0.)))
    xml=ET.tostring(tree,encoding="utf-8")
    assets=MappingProxyType({**built.assets,"arm.xml":xml}) if included else built.assets
    root_xml=built.xml if included else xml
    return replace(built,xml=root_xml,assets=assets,model_sha256=fast_arm_model_digest(root_xml,assets)),tuple(bindings)
