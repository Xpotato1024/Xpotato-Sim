"""Manifest-derived binding from stable logical IDs to package resources."""

from __future__ import annotations

import importlib.resources
import json

from xpotato_sim.runtime.composition.robot_resource import PackageResourceBundle
from xpotato_sim.runtime.composition.viewer_package_resource_manifest import (
    CONFIGURATION_ROLE,
    FIXTURE_ROLE,
    MODEL_DEPENDENCY_ROLE,
    MODEL_ENTRYPOINT_ROLE,
    MODEL_INCLUDE_ROLE,
    VIEWER_DECLARATION_ROLE,
    decode_viewer_package_resource_manifest,
)


_MANIFEST_BYTES = (
    importlib.resources.files(__package__)
    .joinpath("viewer-resource-bindings.json")
    .read_bytes()
)
FAST_ARM_RESOURCE_BINDING_MANIFEST = decode_viewer_package_resource_manifest(
    json.loads(_MANIFEST_BYTES.decode("utf-8"))
)

FAST_ARM_SCENE_RESOURCE = FAST_ARM_RESOURCE_BINDING_MANIFEST.require_one(
    MODEL_ENTRYPOINT_ROLE
).resource
FAST_ARM_VIEWER_DECLARATION_RESOURCE = FAST_ARM_RESOURCE_BINDING_MANIFEST.require_one(
    VIEWER_DECLARATION_ROLE
).resource
FAST_ARM_VIEWER_FIXTURE_RESOURCE = FAST_ARM_RESOURCE_BINDING_MANIFEST.require_one(
    FIXTURE_ROLE
).resource
FAST_ARM_JOINT_LIMIT_RESOURCE = FAST_ARM_RESOURCE_BINDING_MANIFEST.require_one(
    CONFIGURATION_ROLE
).resource
FAST_ARM_ARM_XML_RESOURCE = FAST_ARM_RESOURCE_BINDING_MANIFEST.require_one(
    MODEL_INCLUDE_ROLE
).resource
_FAST_ARM_MESH_BINDINGS = FAST_ARM_RESOURCE_BINDING_MANIFEST.for_role(
    MODEL_DEPENDENCY_ROLE
)
if not _FAST_ARM_MESH_BINDINGS:
    raise ValueError("fast_arm resource manifest requires model dependencies")
FAST_ARM_MESH_RESOURCES = tuple(
    item.resource
    for item in _FAST_ARM_MESH_BINDINGS
)
FAST_ARM_MODEL_VFS_RESOURCES = (FAST_ARM_ARM_XML_RESOURCE, *FAST_ARM_MESH_RESOURCES)
FAST_ARM_MODEL_BUNDLE = PackageResourceBundle(
    entrypoint=FAST_ARM_SCENE_RESOURCE,
    resources=FAST_ARM_MODEL_VFS_RESOURCES,
)


def fast_arm_model_resource_bytes() -> tuple[bytes, dict[str, bytes]]:
    return FAST_ARM_MODEL_BUNDLE.model_xml_and_assets()


__all__ = [
    "FAST_ARM_ARM_XML_RESOURCE",
    "FAST_ARM_JOINT_LIMIT_RESOURCE",
    "FAST_ARM_MESH_RESOURCES",
    "FAST_ARM_MODEL_BUNDLE",
    "FAST_ARM_MODEL_VFS_RESOURCES",
    "FAST_ARM_RESOURCE_BINDING_MANIFEST",
    "FAST_ARM_SCENE_RESOURCE",
    "FAST_ARM_VIEWER_DECLARATION_RESOURCE",
    "FAST_ARM_VIEWER_FIXTURE_RESOURCE",
    "fast_arm_model_resource_bytes",
]
