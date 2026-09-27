"""既存arm.xmlから決定的に原型・鏡映型を組み立てる。別XML/mesh正本を作らない。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import json
from math import isfinite
from types import MappingProxyType
from typing import Mapping
import xml.etree.ElementTree as ET

from .assembly import FastArmAssembly
from .definition import FAST_ARM_JOINT_NAMES

# world/localのXZ面で鏡映。位置はS、回転軸はdet(S)Sで変換する。
_S = (1.0, -1.0, 1.0)
_AXIAL = (-1.0, 1.0, -1.0)


def _values(text: str, count: int) -> tuple[float, ...]:
    values = tuple(float(value) for value in text.split())
    if len(values) != count or not all(isfinite(v) for v in values):
        raise ValueError("unsupported non-finite or malformed source vector")
    return values


def _text(values: tuple[float, ...]) -> str:
    return " ".join(format(0.0 if value == 0 else value, ".17g") for value in values)


def _reflect(element: ET.Element) -> None:
    """既存resourceの座標量を鏡映する。未対応姿勢表現を黙って残さない。"""
    for node in element.iter():
        if any(key in node.attrib for key in ("axisangle", "xyaxes", "zaxis", "refquat", "refpos")):
            raise ValueError("source uses an unsupported orientation/mesh reference")
        for key in ("pos", "fromto"):
            if key in node.attrib:
                values = _values(node.attrib[key], 6 if key == "fromto" else 3)
                node.set(key, _text(tuple(value * _S[i % 3] for i, value in enumerate(values))))
        if "euler" in node.attrib:
            node.set("euler", _text(tuple(v*s for v, s in zip(_values(node.attrib["euler"], 3), _AXIAL))))
        if "quat" in node.attrib:
            q = _values(node.attrib["quat"], 4)
            node.set("quat", _text((q[0], -q[1], q[2], -q[3])))
        if "axis" in node.attrib:
            if node.tag != "joint" or node.get("type", "hinge") not in {"hinge", "slide"}:
                raise ValueError("unsupported source axis owner")
            signs = _AXIAL if node.get("type", "hinge") == "hinge" else _S
            node.set("axis", _text(tuple(v*s for v, s in zip(_values(node.attrib["axis"], 3), signs))))
        if "fullinertia" in node.attrib:
            inertia = _values(node.attrib["fullinertia"], 6)
            node.set("fullinertia", _text(tuple(v*s for v, s in zip(inertia, (1,1,1,-1,1,-1)))))
        if node.tag == "mesh":
            scale = _values(node.get("scale", "1 1 1"), 3)
            node.set("scale", _text(tuple(v*s for v, s in zip(scale, _S))))


def _namespace(root: ET.Element, prefix: str) -> None:
    for node in root.iter():
        for key in ("name", "mesh", "material", "joint", "body", "site", "tendon", "class", "childclass"):
            if key in node.attrib and node.attrib[key]:
                node.set(key, prefix + node.attrib[key])


@dataclass(frozen=True, slots=True)
class FastArmAssemblyModel:
    assembly: FastArmAssembly
    xml: bytes
    assets: Mapping[str, bytes]
    source_sha256: str
    model_sha256: str


def build_fast_arm_assembly_model(assembly: FastArmAssembly) -> FastArmAssemblyModel:
    """coreの原本だけを読む。ネットワーク、MuJoCo実行、実機I/Oを行わない。"""
    if type(assembly) is not FastArmAssembly:
        raise TypeError("assembly must be FastArmAssembly")
    resources = files("fast_arm_core").joinpath("resources/model")
    source = resources.joinpath("arm.xml").read_bytes()
    root = ET.fromstring(source)
    if {child.tag for child in root} - {"compiler", "asset", "default", "worldbody", "actuator", "keyframe"}:
        raise ValueError("source model has unsupported sections; review assembly support")
    compiler = root.find("compiler")
    if compiler is None or compiler.get("angle") != "degree" or compiler.get("eulerseq", "xyz") != "xyz":
        raise ValueError("source compiler convention changed")
    if any(len(node) or node.attrib for node in root.findall("default")):
        raise ValueError("source default classes require an explicit assembly review")
    source_joints = tuple(node.get("name") for node in root.findall(".//worldbody//joint"))
    if source_joints != FAST_ARM_JOINT_NAMES:
        raise ValueError("source joint order differs from core definition")
    meshes = root.findall("./asset/mesh")
    assets = {}
    for mesh in meshes:
        filename = mesh.get("file", "")
        if not filename or "/" in filename or "\\" in filename or not filename.endswith(".stl"):
            raise ValueError("source mesh must be a package-owned STL basename")
        mesh.set("name", mesh.get("name", filename[:-4]))
        assets["meshes/" + filename] = resources.joinpath("meshes", filename).read_bytes()
    # 無名geomもinstance内の安定した名前を持ち、観測・衝突分類から参照できる。
    for i, geom in enumerate(root.findall(".//worldbody//geom")):
        if "name" not in geom.attrib:
            geom.set("name", f"geom_{i}")
    names = [node.get("name") for node in root.iter() if "name" in node.attrib]
    if len(names) != len(set(names)):
        raise ValueError("source names are ambiguous across assembly namespaces")
    output = ET.Element("mujoco", {"model": "fast_arm_assembly"})
    output.append(deepcopy(compiler))
    output.find("compiler").set("meshdir", "meshes")
    output_asset = ET.SubElement(output, "asset")
    world = ET.SubElement(output, "worldbody")
    actuators = ET.SubElement(output, "actuator")
    keys = ET.SubElement(output, "keyframe")
    for arm in assembly.instances:
        instance = deepcopy(root)
        if arm.mirror_y:
            _reflect(instance)
        _namespace(instance, arm.arm_id + "__")
        output_asset.extend(instance.find("asset"))
        mount = ET.SubElement(world, "body", {"name": arm.name("mount"),
                              "pos": _text(arm.position_m), "quat": _text(arm.quaternion_wxyz)})
        mount.extend(instance.find("worldbody"))
        actuators.extend(instance.find("actuator"))
    for key in root.findall("./keyframe/key"):
        if set(key.attrib) - {"name", "qpos", "ctrl", "qvel", "time"}:
            raise ValueError("source keyframe contains unsupported fields")
        combined = deepcopy(key)
        for field in ("qpos", "qvel", "ctrl"):
            if field in key.attrib:
                values = _values(key.attrib[field], len(FAST_ARM_JOINT_NAMES))
                combined.set(field, _text(values * len(assembly.instances)))
        keys.append(combined)
    ET.indent(output, space="  ")
    xml = (ET.tostring(output, encoding="unicode") + "\n").encode("utf-8")
    asset_hashes = {name: sha256(data).hexdigest() for name, data in sorted(assets.items())}
    def digest(xml_bytes: bytes) -> str:
        value = {"xml": sha256(xml_bytes).hexdigest(), "assets": asset_hashes}
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return FastArmAssemblyModel(assembly, xml, MappingProxyType(assets), digest(source), digest(xml))
