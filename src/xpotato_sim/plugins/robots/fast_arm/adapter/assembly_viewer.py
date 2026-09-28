"""FastArm assemblyからViewer用resource bundleを決定的に生成する。

coreのarm.xml/STLを唯一のmodel sourceとし、生成物はapplication sessionの一時resourceとして扱う。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

import mujoco

from fast_arm_core.assembly import FastArmAssembly
from fast_arm_core.assembly_model import FastArmAssemblyModel, build_fast_arm_assembly_model
from xpotato_sim.plugins.robots.fast_arm.adapter.viewer import FAST_ARM_VIEWER_DECLARATION
from xpotato_sim.runtime.composition.viewer_robot_declaration import (
    VIEWER_ROBOT_DECLARATION_SCHEMA_VERSION,
    ViewerRobotDeclaration,
    ViewerVfsAsset,
    ViewerVisualStyleSelection,
    repository_resource_public_url,
    viewer_robot_declaration_canonical_bytes,
    viewer_robot_declaration_digest,
)

FAST_ARM_ASSEMBLY_MODEL_CONTRACT_VERSION = "fast_arm-assembly-mujoco-model/v2"
_DYNAMIC_ROOT = "assets/mujoco/fast_arm_assembly"

def _normalized_match(value: str) -> str:
    result = re.sub(r"[^a-z0-9]", "", value.lower())
    if not result:
        raise ValueError("viewer visual match must contain an alphanumeric character")
    return result


def _visual_style_selection(assembly: FastArmAssembly) -> tuple[ViewerVisualStyleSelection, ...]:
    """namespaced body/mesh名へ既存FastArm styleを投影する。"""
    selections: dict[str, str] = {}
    for item in FAST_ARM_VIEWER_DECLARATION.visual_style_selection:
        normalized = _normalized_match(item.match)
        selections.setdefault(normalized, item.style_key)
        for arm_id in assembly.arm_ids:
            namespaced = _normalized_match(f"{arm_id}__{item.match}")
            previous = selections.setdefault(namespaced, item.style_key)
            if previous != item.style_key:
                raise ValueError("ambiguous namespaced viewer visual style")
    return tuple(
        ViewerVisualStyleSelection(match, style_key)
        for match, style_key in sorted(selections.items())
    )


def _home_qpos(built: FastArmAssemblyModel) -> tuple[float, ...]:
    model = mujoco.MjModel.from_xml_string(
        built.xml.decode("utf-8"), dict(built.assets)
    )
    data = mujoco.MjData(model)
    key = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home"))
    if key < 0:
        raise ValueError("assembly viewer requires the home keyframe")
    mujoco.mj_resetDataKeyframe(model, data, key)
    mujoco.mj_forward(model, data)
    qpos = tuple(float(value) for value in data.qpos)
    if len(qpos) != len(built.assembly.joint_names):
        raise ValueError("assembly home qpos dimension differs from named joints")
    return qpos


def _fixture_bytes(*, model_path: str, qpos: tuple[float, ...]) -> bytes:
    document = {
        "schema_version": 1,
        "source": "python-native-mujoco assembly fixture",
        "model_path": model_path,
        "preset": "assembly-home",
        "qpos_length": len(qpos),
        "frames": [
            {
                "frame_index": 0,
                "t_s": 0.0,
                "qpos": list(qpos),
                "metadata": {"source": "assembly-home-keyframe"},
            }
        ],
    }
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class FastArmAssemblyViewerBundle:
    built: FastArmAssemblyModel
    declaration: ViewerRobotDeclaration
    resources: Mapping[str, bytes]

    @property
    def declaration_digest(self) -> str:
        return viewer_robot_declaration_digest(self.declaration)

    @property
    def declaration_resource_path(self) -> str:
        matches = tuple(
            path for path in self.resources if path.endswith("/viewer-profile.json")
        )
        if len(matches) != 1:
            raise RuntimeError("assembly viewer bundle has ambiguous declaration resource")
        return matches[0]

    @property
    def declaration_url(self) -> str:
        return repository_resource_public_url(self.declaration_resource_path)

    @property
    def metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "robot_profile_id": self.declaration.profile_id,
                "model_sha256": self.built.model_sha256,
                "model_contract_version": self.declaration.model_contract_version,
                "robot_joint_names": list(self.declaration.joint_names),
                "robot_qpos_dimension": self.declaration.qpos_dimension,
                "viewer_robot_declaration_resource_path": self.declaration_resource_path,
                "viewer_robot_declaration_url": self.declaration_url,
                "viewer_robot_declaration_digest": self.declaration_digest,
            }
        )

    def write_public_tree(self, root: Path) -> tuple[Path, ...]:
        """assets以下のlogical resourceをVite公開rootへ書く。"""
        root = Path(root)
        if root.exists() and any(root.iterdir()):
            raise ValueError("dynamic viewer resource root must be empty")
        written: list[Path] = []
        for logical_path, payload in sorted(self.resources.items()):
            parts = Path(logical_path).parts
            if not parts or parts[0] != "assets" or ".." in parts:
                raise ValueError("dynamic viewer resources must use assets logical paths")
            target = root.joinpath(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            written.append(target)
        return tuple(written)


def build_fast_arm_assembly_viewer_bundle(
    assembly: FastArmAssembly,
    *, profile_id: str = "fast_arm_assembly",
    built: FastArmAssemblyModel | None = None,
) -> FastArmAssemblyViewerBundle:
    if type(assembly) is not FastArmAssembly:
        raise ValueError("viewer requires an explicit FastArmAssembly")
    if built is None:
        built = build_fast_arm_assembly_model(assembly)
    if built.assembly != assembly:
        raise ValueError("viewer/provider assembly mismatch")
    prefix = f"{_DYNAMIC_ROOT}/{built.model_sha256}"
    model_path = f"{prefix}/model.xml"
    fixture_path = f"{prefix}/fixture.json"
    declaration_path = f"{prefix}/viewer-profile.json"

    vfs_assets = tuple(
        ViewerVfsAsset(
            vfs_path=name,
            resource_path=f"{prefix}/{name}",
            url=repository_resource_public_url(f"{prefix}/{name}"),
        )
        for name in sorted(built.assets)
    )
    declaration = ViewerRobotDeclaration(
        schema_version=VIEWER_ROBOT_DECLARATION_SCHEMA_VERSION,
        profile_id=profile_id,
        profile_contract_version=1,
        model_contract_version=FAST_ARM_ASSEMBLY_MODEL_CONTRACT_VERSION,
        model_url=repository_resource_public_url(model_path),
        model_resource_path=model_path,
        initial_keyframe_name="home",
        initial_pose_source_label="MuJoCo assembly home keyframe",
        fixture_url=repository_resource_public_url(fixture_path),
        fixture_resource_path=fixture_path,
        vfs_assets=vfs_assets,
        visual_style_selection=_visual_style_selection(assembly),
        body_visual_styles=FAST_ARM_VIEWER_DECLARATION.body_visual_styles,
        axis_visual_styles=FAST_ARM_VIEWER_DECLARATION.axis_visual_styles,
        joint_names=assembly.joint_names,
        qpos_dimension=len(assembly.joint_names),
    )
    qpos = _home_qpos(built)
    resources: dict[str, bytes] = {
        model_path: built.xml,
        fixture_path: _fixture_bytes(model_path=model_path, qpos=qpos),
        declaration_path: viewer_robot_declaration_canonical_bytes(declaration),
    }
    resources.update(
        {f"{prefix}/{name}": payload for name, payload in built.assets.items()}
    )
    return FastArmAssemblyViewerBundle(
        built=built,
        declaration=declaration,
        resources=MappingProxyType(resources),
    )


__all__ = [
    "FAST_ARM_ASSEMBLY_MODEL_CONTRACT_VERSION",
    "FastArmAssemblyViewerBundle",
    "build_fast_arm_assembly_viewer_bundle",
]
