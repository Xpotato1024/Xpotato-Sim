from __future__ import annotations

import copy
import importlib.resources
import json
from dataclasses import replace

import pytest

from xpotato_sim.plugins.robots.fast_arm.adapter.resources import (
    FAST_ARM_ARM_XML_RESOURCE,
    FAST_ARM_JOINT_LIMIT_RESOURCE,
    FAST_ARM_MESH_RESOURCES,
    FAST_ARM_MODEL_BUNDLE,
    FAST_ARM_RESOURCE_BINDING_MANIFEST,
    FAST_ARM_SCENE_RESOURCE,
    FAST_ARM_VIEWER_DECLARATION_RESOURCE,
    FAST_ARM_VIEWER_FIXTURE_RESOURCE,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.viewer import (
    FAST_ARM_VIEWER_DECLARATION,
)
from xpotato_sim.runtime.composition.viewer_package_resource_manifest import (
    MODEL_DEPENDENCY_ROLE,
    decode_viewer_package_resource_manifest,
    validate_viewer_declaration_resource_bindings,
)


def _manifest_document() -> dict[str, object]:
    data = (
        importlib.resources.files(
            "xpotato_sim.plugins.robots.fast_arm.adapter.resources"
        )
        .joinpath("viewer-resource-bindings.json")
        .read_bytes()
    )
    value = json.loads(data.decode("utf-8"))
    assert isinstance(value, dict)
    return value


def test_fast_arm_resource_manifest_decodes_to_its_normalized_document() -> None:
    source = _manifest_document()
    assert FAST_ARM_RESOURCE_BINDING_MANIFEST.to_document() == source
    assert decode_viewer_package_resource_manifest(source).to_document() == source


def test_fast_arm_exports_are_projections_of_manifest_roles() -> None:
    manifest_resources = tuple(
        binding.resource for binding in FAST_ARM_RESOURCE_BINDING_MANIFEST.resources
    )
    assert FAST_ARM_SCENE_RESOURCE in manifest_resources
    assert FAST_ARM_VIEWER_DECLARATION_RESOURCE in manifest_resources
    assert FAST_ARM_VIEWER_FIXTURE_RESOURCE in manifest_resources
    assert FAST_ARM_JOINT_LIMIT_RESOURCE in manifest_resources
    assert FAST_ARM_ARM_XML_RESOURCE in manifest_resources
    assert set(FAST_ARM_MESH_RESOURCES) == {
        binding.resource
        for binding in FAST_ARM_RESOURCE_BINDING_MANIFEST.for_role(
            MODEL_DEPENDENCY_ROLE
        )
    }
    assert FAST_ARM_MODEL_BUNDLE.entrypoint is FAST_ARM_SCENE_RESOURCE
    assert FAST_ARM_MODEL_BUNDLE.resources == (
        FAST_ARM_ARM_XML_RESOURCE,
        *FAST_ARM_MESH_RESOURCES,
    )


@pytest.mark.parametrize("change", ("unknown_root", "missing_root", "unknown_item", "missing_item"))
def test_manifest_rejects_unknown_and_missing_fields(change: str) -> None:
    document = copy.deepcopy(_manifest_document())
    resources = document["resources"]
    assert isinstance(resources, list)
    first = resources[0]
    assert isinstance(first, dict)
    if change == "unknown_root":
        document["unexpected"] = True
    elif change == "missing_root":
        document.pop("schemaVersion")
    elif change == "unknown_item":
        first["unexpected"] = True
    else:
        first.pop("package")
    with pytest.raises(ValueError, match="keys mismatch"):
        decode_viewer_package_resource_manifest(document)


def test_manifest_rejects_duplicate_binding_identity() -> None:
    document = copy.deepcopy(_manifest_document())
    resources = document["resources"]
    assert isinstance(resources, list)
    config = next(
        item
        for item in resources
        if isinstance(item, dict) and item.get("role") == "configuration"
    )
    resources.append(copy.deepcopy(config))
    with pytest.raises(ValueError, match="logical identifiers"):
        decode_viewer_package_resource_manifest(document)

    document = copy.deepcopy(_manifest_document())
    resources = document["resources"]
    assert isinstance(resources, list)
    dependencies = [
        item
        for item in resources
        if isinstance(item, dict) and item.get("role") == "model_dependency"
    ]
    dependencies[1]["logicalIdentifier"] = dependencies[0]["logicalIdentifier"]
    dependencies[1]["url"] = dependencies[0]["url"]
    with pytest.raises(ValueError, match="public URLs"):
        decode_viewer_package_resource_manifest(document)

    document = copy.deepcopy(_manifest_document())
    resources = document["resources"]
    assert isinstance(resources, list)
    first = resources[0]
    assert isinstance(first, dict)
    first["bundlePath"] = "arm.xml"
    with pytest.raises(ValueError, match="bundle paths"):
        decode_viewer_package_resource_manifest(document)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("package", "not-a-package", "importable package"),
        ("package", "fast_é", "ASCII importable package"),
        ("packageResourcePath", "../scene.xml", "relative POSIX path"),
        ("packageResourcePath", "resources//scene.xml", "relative POSIX path"),
        ("logicalIdentifier", "assets/../scene.xml", "relative POSIX path"),
        ("logicalIdentifier", "assets//fast_arm/scene.xml", "relative POSIX path"),
        ("logicalIdentifier", "other/fast_arm/scene.xml", "stable namespace"),
        ("url", "https://example.invalid/scene.xml", "url mismatch"),
        ("bundlePath", "bundle//scene.xml", "relative POSIX path"),
    ),
)
def test_manifest_rejects_invalid_package_path_logical_path_and_url(
    field: str, replacement: object, message: str
) -> None:
    document = copy.deepcopy(_manifest_document())
    resources = document["resources"]
    assert isinstance(resources, list)
    first = resources[0]
    assert isinstance(first, dict)
    first[field] = replacement
    with pytest.raises(ValueError, match=message):
        decode_viewer_package_resource_manifest(document)


def test_manifest_requires_each_singleton_role() -> None:
    document = copy.deepcopy(_manifest_document())
    resources = document["resources"]
    assert isinstance(resources, list)
    resources[:] = [
        item
        for item in resources
        if isinstance(item, dict) and item.get("role") != "viewer_declaration"
    ]
    with pytest.raises(ValueError, match="exactly one 'viewer_declaration'"):
        decode_viewer_package_resource_manifest(document)


def test_viewer_declaration_exactly_matches_manifest_model_fixture_and_vfs() -> None:
    validate_viewer_declaration_resource_bindings(
        FAST_ARM_RESOURCE_BINDING_MANIFEST,
        FAST_ARM_VIEWER_DECLARATION,
    )
    with pytest.raises(ValueError, match="viewer model resource"):
        validate_viewer_declaration_resource_bindings(
            FAST_ARM_RESOURCE_BINDING_MANIFEST,
            replace(
                FAST_ARM_VIEWER_DECLARATION,
                model_resource_path="assets/mujoco/other/scene.xml",
                model_url="/mujoco/other/scene.xml",
            ),
        )
    with pytest.raises(ValueError, match="viewer VFS resources"):
        validate_viewer_declaration_resource_bindings(
            FAST_ARM_RESOURCE_BINDING_MANIFEST,
            replace(
                FAST_ARM_VIEWER_DECLARATION,
                vfs_assets=FAST_ARM_VIEWER_DECLARATION.vfs_assets[:-1],
            ),
        )


def test_adapter_resources_module_contains_no_concrete_binding_registry() -> None:
    source = (
        importlib.resources.files(
            "xpotato_sim.plugins.robots.fast_arm.adapter.resources"
        )
        .joinpath("__init__.py")
        .read_text(encoding="utf-8")
    )
    assert "PackageResource(" not in source
    assert "BaseLink.stl" not in source
    assert "resources/model" not in source
