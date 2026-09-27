"""Typed physical ownership for repository and installed-package resources."""

from __future__ import annotations

import importlib.resources
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable


def _validate_relative_posix_path(value: str, *, name: str) -> None:
    if not value or "\\" in value:
        raise ValueError(f"{name} must be a non-empty relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"{name} must not be absolute or escape its resource root")


@runtime_checkable
class LogicalResource(Protocol):
    @property
    def logical_identifier(self) -> str: ...

    def to_document(self) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class PackageResource:
    """A resource whose physical owner is an importable Python package."""

    package: str
    resource_path: str
    logical_identifier: str
    bundle_path: str | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", self.package) is None:
            raise ValueError("package resource owner must be an importable package name")
        _validate_relative_posix_path(self.resource_path, name="package resource path")
        _validate_relative_posix_path(self.logical_identifier, name="logical resource identifier")
        if self.bundle_path is not None:
            _validate_relative_posix_path(self.bundle_path, name="model bundle path")

    def to_document(self) -> dict[str, object]:
        # Preserve the existing canonical identity document: this field is a
        # stable logical identifier, not a claim about physical ownership.
        return {"repositoryPath": self.logical_identifier}


@dataclass(frozen=True, slots=True)
class PackageResourceBundle:
    """A package-owned entry resource plus its relative-layout dependencies."""

    entrypoint: PackageResource
    resources: tuple[PackageResource, ...]

    def __post_init__(self) -> None:
        if self.entrypoint.bundle_path is None:
            raise ValueError("package resource bundle entrypoint requires a bundle path")
        if not self.resources:
            raise ValueError("package resource bundle requires dependency resources")
        paths = (
            self.entrypoint.bundle_path,
            *(resource.bundle_path for resource in self.resources),
        )
        if any(path is None for path in paths):
            raise ValueError("every package resource bundle member requires a bundle path")
        if len(paths) != len(set(paths)):
            raise ValueError("package resource bundle paths must be unique")

    @property
    def logical_identifier(self) -> str:
        return self.entrypoint.logical_identifier

    def to_document(self) -> dict[str, object]:
        return self.entrypoint.to_document()

    def model_xml_and_assets(self) -> tuple[bytes, dict[str, bytes]]:
        assets: dict[str, bytes] = {}
        for resource in self.resources:
            assert resource.bundle_path is not None
            assets[resource.bundle_path] = read_package_resource_bytes(resource)
        return read_package_resource_bytes(self.entrypoint), assets


def package_resource_traversable(resource: PackageResource):
    try:
        root = importlib.resources.files(resource.package)
    except (ModuleNotFoundError, TypeError) as exc:
        raise ValueError(
            f"missing package resource owner {resource.package!r}"
        ) from exc
    candidate = root.joinpath(*PurePosixPath(resource.resource_path).parts)
    if isinstance(root, Path) and isinstance(candidate, Path):
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        if not resolved_candidate.is_relative_to(resolved_root):
            raise ValueError(
                "resolved package resource escapes its owning package: "
                f"{resource.package!r}:{resource.resource_path!r}"
            )
    if not candidate.is_file():
        raise ValueError(
            "missing package resource: "
            f"{resource.package!r}:{resource.resource_path!r}"
        )
    return candidate


def read_package_resource_bytes(resource: PackageResource) -> bytes:
    try:
        return package_resource_traversable(resource).read_bytes()
    except OSError as exc:
        raise ValueError(
            "failed to read package resource: "
            f"{resource.package!r}:{resource.resource_path!r}"
        ) from exc


__all__ = [
    "LogicalResource",
    "PackageResource",
    "PackageResourceBundle",
    "package_resource_traversable",
    "read_package_resource_bytes",
]
