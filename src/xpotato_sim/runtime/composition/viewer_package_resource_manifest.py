"""Typed decoder for plugin-owned viewer package-resource manifests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Final

from xpotato_sim.runtime.composition.robot_resource import PackageResource
from xpotato_sim.runtime.composition.viewer_robot_declaration import (
    ViewerRobotDeclaration,
    repository_resource_public_url,
)


VIEWER_PACKAGE_RESOURCE_BINDINGS_SCHEMA_VERSION: Final = (
    "viewer-package-resource-bindings/v1"
)

MODEL_ENTRYPOINT_ROLE: Final = "model_entrypoint"
MODEL_INCLUDE_ROLE: Final = "model_include"
MODEL_DEPENDENCY_ROLE: Final = "model_dependency"
VIEWER_DECLARATION_ROLE: Final = "viewer_declaration"
FIXTURE_ROLE: Final = "fixture"
CONFIGURATION_ROLE: Final = "configuration"

_ROLES = frozenset(
    {
        MODEL_ENTRYPOINT_ROLE,
        MODEL_INCLUDE_ROLE,
        MODEL_DEPENDENCY_ROLE,
        VIEWER_DECLARATION_ROLE,
        FIXTURE_ROLE,
        CONFIGURATION_ROLE,
    }
)
_PUBLIC_ROLES = _ROLES - {CONFIGURATION_ROLE}
_BUNDLE_ROLES = frozenset(
    {MODEL_ENTRYPOINT_ROLE, MODEL_INCLUDE_ROLE, MODEL_DEPENDENCY_ROLE}
)
_ROOT_KEYS = frozenset({"schemaVersion", "resources"})
_RESOURCE_KEYS = frozenset(
    {
        "role",
        "logicalIdentifier",
        "url",
        "package",
        "packageResourcePath",
        "bundlePath",
    }
)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object with string keys")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _relative_posix_string(value: object, name: str) -> str:
    path = _string(value, name)
    parts = path.split("/")
    if (
        path.startswith("/")
        or "\\" in path
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError(f"{name} must be a non-empty relative POSIX path")
    return path


def _package_name(value: object, name: str) -> str:
    package = _string(value, name)
    if re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", package
    ) is None:
        raise ValueError(f"{name} must be an ASCII importable package name")
    return package


def _exact_keys(
    value: Mapping[str, object], expected: frozenset[str], name: str
) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise ValueError(
            f"{name} keys mismatch: expected {tuple(sorted(expected))}, "
            f"got {tuple(sorted(actual))}"
        )


def _validate_logical_namespace(logical_identifier: str) -> None:
    parts = PurePosixPath(logical_identifier).parts
    if parts[:1] not in (("assets",), ("configs",)) or len(parts) < 3:
        raise ValueError(
            "viewer package resource logical identifier must use the assets/ or "
            "configs/ stable namespace"
        )


@dataclass(frozen=True, slots=True)
class ViewerPackageResourceBinding:
    role: str
    resource: PackageResource
    url: str | None

    def to_document(self) -> dict[str, object]:
        return {
            "role": self.role,
            "logicalIdentifier": self.resource.logical_identifier,
            "url": self.url,
            "package": self.resource.package,
            "packageResourcePath": self.resource.resource_path,
            "bundlePath": self.resource.bundle_path,
        }


@dataclass(frozen=True, slots=True)
class ViewerPackageResourceManifest:
    schema_version: str
    resources: tuple[ViewerPackageResourceBinding, ...]

    def __post_init__(self) -> None:
        if self.schema_version != VIEWER_PACKAGE_RESOURCE_BINDINGS_SCHEMA_VERSION:
            raise ValueError(
                "unsupported viewer package resource bindings schema version: "
                f"{self.schema_version!r}"
            )
        if not self.resources:
            raise ValueError("viewer package resource manifest requires resources")

        for name, values in (
            ("public URLs", tuple(item.url for item in self.resources if item.url)),
            (
                "logical identifiers",
                tuple(item.resource.logical_identifier for item in self.resources),
            ),
            (
                "bundle paths",
                tuple(
                    item.resource.bundle_path
                    for item in self.resources
                    if item.resource.bundle_path
                ),
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"viewer package resource {name} must be unique")

        for role in (MODEL_ENTRYPOINT_ROLE, VIEWER_DECLARATION_ROLE, FIXTURE_ROLE):
            self.require_one(role)

    def for_role(self, role: str) -> tuple[ViewerPackageResourceBinding, ...]:
        return tuple(item for item in self.resources if item.role == role)

    def require_one(self, role: str) -> ViewerPackageResourceBinding:
        matches = self.for_role(role)
        if len(matches) != 1:
            raise ValueError(
                f"viewer package resource manifest requires exactly one {role!r} "
                f"resource, got {len(matches)}"
            )
        return matches[0]

    def to_document(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "resources": [item.to_document() for item in self.resources],
        }


def decode_viewer_package_resource_manifest(
    value: object,
) -> ViewerPackageResourceManifest:
    root = _mapping(value, "viewer package resource manifest")
    _exact_keys(root, _ROOT_KEYS, "viewer package resource manifest")
    schema_version = _string(root["schemaVersion"], "schemaVersion")

    bindings: list[ViewerPackageResourceBinding] = []
    for index, raw_item in enumerate(_sequence(root["resources"], "resources")):
        name = f"resources[{index}]"
        item = _mapping(raw_item, name)
        _exact_keys(item, _RESOURCE_KEYS, name)
        role = _string(item["role"], f"{name}.role")
        if role not in _ROLES:
            raise ValueError(f"unsupported viewer package resource role: {role!r}")

        bundle_value = item["bundlePath"]
        if bundle_value is not None:
            bundle_value = _relative_posix_string(
                bundle_value, f"{name}.bundlePath"
            )
        if role in _BUNDLE_ROLES and bundle_value is None:
            raise ValueError(f"{role!r} resource requires bundlePath")
        if role not in _BUNDLE_ROLES and bundle_value is not None:
            raise ValueError(f"{role!r} resource must not define bundlePath")

        logical_identifier = _relative_posix_string(
            item["logicalIdentifier"], f"{name}.logicalIdentifier"
        )
        resource = PackageResource(
            package=_package_name(item["package"], f"{name}.package"),
            resource_path=_relative_posix_string(
                item["packageResourcePath"], f"{name}.packageResourcePath"
            ),
            logical_identifier=logical_identifier,
            bundle_path=bundle_value,
        )
        _validate_logical_namespace(logical_identifier)

        url_value = item["url"]
        if role in _PUBLIC_ROLES:
            url = _string(url_value, f"{name}.url")
            expected_url = repository_resource_public_url(logical_identifier)
            if url != expected_url:
                raise ValueError(
                    f"{name}.url mismatch: expected {expected_url!r}, got {url!r}"
                )
        else:
            if url_value is not None:
                raise ValueError(f"{role!r} resource URL must be null")
            url = None
        bindings.append(ViewerPackageResourceBinding(role, resource, url))

    return ViewerPackageResourceManifest(schema_version, tuple(bindings))


def validate_viewer_declaration_resource_bindings(
    manifest: ViewerPackageResourceManifest,
    declaration: ViewerRobotDeclaration,
) -> None:
    entrypoint = manifest.require_one(MODEL_ENTRYPOINT_ROLE)
    fixture = manifest.require_one(FIXTURE_ROLE)
    if (
        declaration.model_resource_path,
        declaration.model_url,
    ) != (entrypoint.resource.logical_identifier, entrypoint.url):
        raise ValueError("viewer model resource does not match its package manifest")
    if (
        declaration.fixture_resource_path,
        declaration.fixture_url,
    ) != (fixture.resource.logical_identifier, fixture.url):
        raise ValueError("viewer fixture resource does not match its package manifest")

    manifest_vfs = {
        (item.resource.bundle_path, item.resource.logical_identifier, item.url)
        for item in manifest.resources
        if item.role in (MODEL_INCLUDE_ROLE, MODEL_DEPENDENCY_ROLE)
    }
    declaration_vfs = {
        (item.vfs_path, item.resource_path, item.url)
        for item in declaration.vfs_assets
    }
    if manifest_vfs != declaration_vfs:
        raise ValueError(
            "viewer VFS resources do not exactly match their package manifest"
        )


__all__ = [
    "CONFIGURATION_ROLE",
    "FIXTURE_ROLE",
    "MODEL_DEPENDENCY_ROLE",
    "MODEL_ENTRYPOINT_ROLE",
    "MODEL_INCLUDE_ROLE",
    "VIEWER_DECLARATION_ROLE",
    "VIEWER_PACKAGE_RESOURCE_BINDINGS_SCHEMA_VERSION",
    "ViewerPackageResourceBinding",
    "ViewerPackageResourceManifest",
    "decode_viewer_package_resource_manifest",
    "validate_viewer_declaration_resource_bindings",
]
