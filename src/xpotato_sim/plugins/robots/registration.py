"""first-party Robot Pluginのidentityとresource ownershipを宣言する契約。

resource path、viewer VFS coverage、Bundle/Profile/Runtime identityをimport時に検証するが、
model load、simulator起動、hardware I/Oは行わない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    VersionedIdentity,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
    SCENE_ROLE_BINDING_V1,
    RobotBundle,
)
from xpotato_sim.runtime.composition.viewer_robot_declaration import (
    ViewerRobotDeclaration,
    decode_viewer_robot_declaration,
    repository_resource_public_url,
)
from xpotato_sim.runtime.composition.robot_resource import (
    PackageResource,
    PackageResourceBundle,
    read_package_resource_bytes,
)
from xpotato_sim.runtime.composition.robot_resolution import (
    validate_production_robot_selection_consistency,
)


ROBOT_ONBOARDING_CONTRACT_VERSION = 1
REQUIRED_ROBOT_CAPABILITIES = frozenset(
    {
        RESET_INITIAL_STATE_V1,
        ENDPOINT_POSE_V1,
        ENDPOINT_COMMAND_V1,
        QPOS_FEASIBILITY_V1,
        SCENE_ROLE_BINDING_V1,
    }
)


def _validate_relative_resource_path(value: str, *, name: str) -> None:
    if not value or "\\" in value:
        raise ValueError(f"{name} must be a non-empty repository-relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"{name} must not be absolute or escape its resource root")


@dataclass(frozen=True, slots=True)
class RepositoryResource:
    """repository-relative pathとcontent digestで固定したresource identity。"""
    repository_path: str

    def __post_init__(self) -> None:
        _validate_relative_resource_path(
            self.repository_path, name="repository resource path"
        )

    def to_document(self) -> dict[str, object]:
        return {"repositoryPath": self.repository_path}

    @property
    def logical_identifier(self) -> str:
        return self.repository_path


RobotResource = RepositoryResource | PackageResource | PackageResourceBundle


@dataclass(frozen=True, slots=True)
class RobotResourceDeclaration:
    """1 Robot Pluginが所有するmodel/viewer/config resource集合。"""
    model: RobotResource
    configurations: tuple[RobotResource, ...]
    viewer_declaration: RobotResource
    viewer_fixture: RobotResource
    viewer_vfs_resources: tuple[RobotResource, ...]

    def __post_init__(self) -> None:
        if not self.configurations:
            raise ValueError("robot resource declaration requires a configuration resource")
        paths = (
            self.model.logical_identifier,
            *(item.logical_identifier for item in self.configurations),
            self.viewer_declaration.logical_identifier,
            self.viewer_fixture.logical_identifier,
            *(item.logical_identifier for item in self.viewer_vfs_resources),
        )
        if len(paths) != len(set(paths)):
            raise ValueError("robot resource declaration paths must be unique")

    def to_document(self) -> dict[str, object]:
        return {
            "model": self.model.to_document(),
            "configurations": [item.to_document() for item in self.configurations],
            "viewerDeclaration": self.viewer_declaration.to_document(),
            "viewerFixture": self.viewer_fixture.to_document(),
            "viewerVfsResources": [
                item.to_document() for item in self.viewer_vfs_resources
            ],
        }


def _resolved_resource(
    repository_root: Path,
    resource: RepositoryResource,
    *,
    allowed_roots: tuple[Path, ...],
    ownership_root: Path | None = None,
    ownership_label: str | None = None,
) -> Path:
    candidate = (repository_root / resource.repository_path).resolve()
    roots = tuple(root.resolve() for root in allowed_roots)
    if not any(candidate.is_relative_to(root) for root in roots):
        raise ValueError(
            "robot resource path escapes allowed repository resource roots: "
            f"{resource.repository_path!r}"
        )
    if ownership_root is not None and not candidate.is_relative_to(ownership_root):
        label = ownership_label or "robot"
        raise ValueError(
            f"resolved {label} resource is not owned by the selected robot: "
            f"{resource.repository_path!r}"
        )
    if not candidate.is_file():
        raise ValueError(f"missing robot resource: {resource.repository_path!r}")
    return candidate


def _validate_robot_resource_ownership(
    resource: RobotResource,
    *,
    robot_id: str,
    resource_kind: str,
) -> None:
    path = PurePosixPath(resource.logical_identifier)
    expected_prefix = (
        ("configs", robot_id)
        if resource_kind == "configuration"
        else ("assets", "mujoco", robot_id)
    )
    if path.parts[: len(expected_prefix)] != expected_prefix:
        expected = "/".join(expected_prefix) + "/"
        raise ValueError(
            f"{resource_kind} resource is not owned by robot {robot_id!r}: "
            f"expected path below {expected!r}, got {resource.logical_identifier!r}"
        )


def _resource_bytes(
    repository_root: Path,
    resource: RobotResource,
    *,
    allowed_roots: tuple[Path, ...],
    ownership_root: Path | None = None,
    ownership_label: str | None = None,
) -> bytes:
    if isinstance(resource, PackageResourceBundle):
        return read_package_resource_bytes(resource.entrypoint)
    if isinstance(resource, PackageResource):
        return read_package_resource_bytes(resource)
    return _resolved_resource(
        repository_root,
        resource,
        allowed_roots=allowed_roots,
        ownership_root=ownership_root,
        ownership_label=ownership_label,
    ).read_bytes()


def _validate_viewer_vfs_coverage(
    model_path: Path | bytes,
    viewer: ViewerRobotDeclaration,
    resolved_vfs_resources: tuple[Path | bytes, ...],
) -> None:
    if isinstance(model_path, Path) and model_path.suffix.lower() != ".xml":
        return
    resource_by_vfs_path = {
        declaration.vfs_path: resolved
        for declaration, resolved in zip(
            viewer.vfs_assets, resolved_vfs_resources, strict=True
        )
    }
    visited: set[str] = set()

    def normalize(reference: str, *, parent: PurePosixPath) -> str:
        path = parent / reference
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"viewer VFS reference escapes the virtual root: {reference!r}")
        return path.as_posix().removeprefix("./")

    def read(value: Path | bytes) -> bytes:
        return value if isinstance(value, bytes) else value.read_bytes()

    def visit(xml_path: Path | bytes, logical_path: str) -> None:
        if logical_path in visited:
            return
        visited.add(logical_path)
        try:
            root = ElementTree.fromstring(read(xml_path))
        except ElementTree.ParseError as exc:
            raise ValueError(f"invalid MuJoCo XML resource {logical_path!r}: {exc}") from exc
        parent = PurePosixPath(logical_path).parent
        compiler = root.find("compiler")
        asset_directory = "" if compiler is None else compiler.attrib.get("assetdir", "")
        mesh_directory = (
            asset_directory
            if compiler is None
            else compiler.attrib.get("meshdir", asset_directory)
        )
        texture_directory = (
            asset_directory
            if compiler is None
            else compiler.attrib.get("texturedir", asset_directory)
        )

        for include in root.iter("include"):
            reference = include.attrib.get("file")
            if not reference:
                raise ValueError("MuJoCo include is missing its file reference")
            required = normalize(reference, parent=parent)
            included_path = resource_by_vfs_path.get(required)
            if included_path is None:
                raise ValueError(
                    f"viewer VFS mapping is missing required include {required!r}"
                )
            visit(included_path, required)

        for tag, directory in (
            ("mesh", mesh_directory),
            ("texture", texture_directory),
            ("hfield", asset_directory),
            ("skin", asset_directory),
        ):
            for asset in root.iter(tag):
                reference = asset.attrib.get("file")
                if not reference:
                    continue
                required = normalize(
                    str(PurePosixPath(directory) / reference), parent=parent
                )
                if required not in resource_by_vfs_path:
                    raise ValueError(
                        f"viewer VFS mapping is missing required {tag} asset {required!r}"
                    )

    visit(model_path, ".")


@dataclass(frozen=True, slots=True)
class RobotPluginRegistration:
    """fixed ``plugin.py`` が公開するimmutable Robot onboarding declaration。"""
    identity: VersionedIdentity
    onboarding_contract_version: int
    bundle: RobotBundle
    viewer: ViewerRobotDeclaration
    resources: RobotResourceDeclaration

    def __post_init__(self) -> None:
        robot_id = self.identity.name
        if self.onboarding_contract_version != ROBOT_ONBOARDING_CONTRACT_VERSION:
            raise ValueError(
                "unsupported Robot Plugin onboarding schema version: "
                f"{self.onboarding_contract_version}"
            )
        if self.bundle.identity != self.identity:
            raise ValueError("registration/Robot Bundle identity mismatch")
        profile = self.bundle.profile
        plugin = self.bundle.runtime_plugin
        validate_production_robot_selection_consistency(
            PluginSelection(robot_id, self.identity.version),
            bundle_identity=self.bundle.identity,
            profile=profile,
            plugin=plugin,
        )
        if self.viewer.profile_id != robot_id:
            raise ValueError("registration/viewer declaration identity mismatch")
        if self.viewer.profile_contract_version != self.identity.version:
            raise ValueError(
                "Robot Plugin logical version/viewer profile contract version mismatch"
            )
        if self.viewer.model_contract_version != profile.model_contract_version:
            raise ValueError("Robot Profile/viewer model contract version mismatch")
        if self.viewer.joint_names != profile.canonical_joint_names:
            raise ValueError("Robot Profile/viewer joint name/order mismatch")
        if self.viewer.qpos_dimension != profile.qpos_dimension:
            raise ValueError("Robot Profile/viewer qpos dimension mismatch")
        if self.viewer.initial_keyframe_name != profile.initial_keyframe_name:
            raise ValueError("Robot Profile/viewer initial keyframe mismatch")
        if profile.viewer_profile_id != self.viewer.profile_id:
            raise ValueError("Robot Profile viewer identity mismatch")
        if profile.viewer_declaration is not self.viewer:
            raise ValueError(
                "Robot Profile does not reference the registered viewer declaration object"
            )
        if profile.viewer_declaration_resource_path != (
            self.resources.viewer_declaration.logical_identifier
        ):
            raise ValueError(
                "Robot Profile/viewer declaration resource path mismatch"
            )
        expected_declaration_url = repository_resource_public_url(
            self.resources.viewer_declaration.logical_identifier
        )
        if profile.viewer_declaration_url != expected_declaration_url:
            raise ValueError("Robot Profile/viewer declaration public URL mismatch")
        missing_capabilities = REQUIRED_ROBOT_CAPABILITIES - self.bundle.provided_capabilities
        if missing_capabilities:
            missing = tuple(item.canonical_id for item in sorted(missing_capabilities))
            raise ValueError(f"Robot Plugin missing required capabilities: {missing}")

    def validate_resources(
        self,
        repository_root: Path,
        *,
        asset_roots: tuple[Path, ...],
        configuration_roots: tuple[Path, ...],
    ) -> None:
        robot_id = self.identity.name
        resolved_repository_root = repository_root.resolve()
        asset_ownership_root = (
            resolved_repository_root / "assets" / "mujoco" / robot_id
        )
        configuration_ownership_root = (
            resolved_repository_root / "configs" / robot_id
        )
        asset_resources = (
            self.resources.model,
            self.resources.viewer_declaration,
            self.resources.viewer_fixture,
            *self.resources.viewer_vfs_resources,
        )
        for resource in asset_resources:
            _validate_robot_resource_ownership(
                resource, robot_id=robot_id, resource_kind="asset"
            )
        for resource in self.resources.configurations:
            _validate_robot_resource_ownership(
                resource, robot_id=robot_id, resource_kind="configuration"
            )

        model_data = _resource_bytes(
            repository_root,
            self.resources.model,
            allowed_roots=asset_roots,
            ownership_root=asset_ownership_root,
            ownership_label="asset",
        )
        profile_model = self.bundle.profile.mujoco_model_asset
        if isinstance(self.resources.model, RepositoryResource):
            if not isinstance(profile_model, Path) or (
                _resolved_resource(
                    repository_root,
                    self.resources.model,
                    allowed_roots=asset_roots,
                    ownership_root=asset_ownership_root,
                    ownership_label="asset",
                )
                != profile_model.resolve()
            ):
                raise ValueError("Robot Profile model asset/resource declaration mismatch")
        elif profile_model != self.resources.model:
            raise ValueError("Robot Profile model asset/resource declaration mismatch")
        if self.viewer.model_resource_path != self.resources.model.logical_identifier:
            raise ValueError("viewer/backend model resource declaration mismatch")

        declaration_data = _resource_bytes(
            repository_root,
            self.resources.viewer_declaration,
            allowed_roots=asset_roots,
            ownership_root=asset_ownership_root,
            ownership_label="asset",
        )
        try:
            declaration_document = json.loads(declaration_data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "invalid viewer declaration resource: "
                f"{self.resources.viewer_declaration.logical_identifier!r}"
            ) from exc
        if decode_viewer_robot_declaration(declaration_document) != self.viewer:
            raise ValueError(
                "registered viewer declaration/resource content mismatch"
            )

        _resource_bytes(
            repository_root,
            self.resources.viewer_fixture,
            allowed_roots=asset_roots,
            ownership_root=asset_ownership_root,
            ownership_label="asset",
        )
        if self.viewer.fixture_resource_path != (
            self.resources.viewer_fixture.logical_identifier
        ):
            raise ValueError("viewer fixture/resource declaration mismatch")

        _ = tuple(
            _resource_bytes(
                repository_root,
                resource,
                allowed_roots=configuration_roots,
                ownership_root=configuration_ownership_root,
                ownership_label="configuration",
            )
            for resource in self.resources.configurations
        )
        profile_config = self.bundle.profile.joint_limit_config_asset
        if profile_config is not None:
            if isinstance(profile_config, Path):
                matching = any(
                    isinstance(resource, RepositoryResource)
                    and _resolved_resource(
                        repository_root,
                        resource,
                        allowed_roots=configuration_roots,
                        ownership_root=configuration_ownership_root,
                        ownership_label="configuration",
                    ) == profile_config.resolve()
                    for resource in self.resources.configurations
                )
            else:
                matching = profile_config in self.resources.configurations
            if not matching:
                raise ValueError("Robot Profile configuration/resource declaration mismatch")

        declared_vfs_paths = tuple(
            item.logical_identifier for item in self.resources.viewer_vfs_resources
        )
        viewer_vfs_paths = tuple(item.resource_path for item in self.viewer.vfs_assets)
        if declared_vfs_paths != viewer_vfs_paths:
            raise ValueError("viewer VFS/resource declaration mismatch")
        resolved_vfs_resources = tuple(
            _resource_bytes(
                repository_root,
                resource,
                allowed_roots=asset_roots,
                ownership_root=asset_ownership_root,
                ownership_label="asset",
            )
            for resource in self.resources.viewer_vfs_resources
        )
        _validate_viewer_vfs_coverage(
            model_data, self.viewer, resolved_vfs_resources
        )

    def canonical_identity_bytes(self) -> bytes:
        document = {
            "identity": {
                "name": self.identity.name,
                "version": self.identity.version,
            },
            "onboardingContractVersion": self.onboarding_contract_version,
            "profile": {
                "profileId": self.bundle.profile.profile_id,
                "profileContractVersion": self.bundle.profile.profile_contract_version,
                "modelContractVersion": self.bundle.profile.model_contract_version,
            },
            "viewer": self.viewer.to_document(),
            "resources": self.resources.to_document(),
            "capabilities": [
                {"name": item.name, "version": item.version}
                for item in sorted(self.bundle.provided_capabilities)
            ],
        }
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


__all__ = [
    "PackageResource",
    "PackageResourceBundle",
    "REQUIRED_ROBOT_CAPABILITIES",
    "ROBOT_ONBOARDING_CONTRACT_VERSION",
    "RepositoryResource",
    "RobotPluginRegistration",
    "RobotResourceDeclaration",
]
