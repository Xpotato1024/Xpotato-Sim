"""Immutable declarative robot model contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xpotato_sim.runtime.composition.robot_resource import (
    PackageResource,
    PackageResourceBundle,
)

from xpotato_sim.runtime.composition.viewer_robot_declaration import (
    ViewerRobotDeclaration,
    repository_resource_public_url,
    viewer_robot_declaration_digest,
)


@dataclass(frozen=True, slots=True)
class EndpointReference:
    site_name: str | None
    body_name: str | None

    def __post_init__(self) -> None:
        if self.site_name is None and self.body_name is None:
            raise ValueError("endpoint reference requires a site_name or body_name")
        for field_name, value in (
            ("site_name", self.site_name),
            ("body_name", self.body_name),
        ):
            if value is not None and (not value or value != value.strip()):
                raise ValueError(f"endpoint reference {field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class CoordinateUnitContract:
    position_unit: str
    angle_unit: str
    coordinate_frame: str
    quaternion_order: str


@dataclass(frozen=True, slots=True)
class RobotProfile:
    """Versioned declarations only; executable factories belong to a runtime plugin."""

    profile_id: str
    profile_contract_version: int
    model_contract_version: str
    backend_kind: str
    mujoco_model_asset: Path | PackageResourceBundle
    canonical_joint_names: tuple[str, ...]
    qpos_dimension: int
    qvel_dimension: int
    initial_keyframe_name: str
    endpoint: EndpointReference
    joint_limit_config_asset: Path | PackageResource | None
    coordinate_units: CoordinateUnitContract
    viewer_profile_id: str
    supported_capabilities: frozenset[str]
    viewer_declaration: ViewerRobotDeclaration | None = None
    viewer_declaration_resource_path: str | None = None
    viewer_declaration_url: str | None = None

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id must not be empty")
        if self.profile_contract_version < 1:
            raise ValueError("profile_contract_version must be positive")
        if not self.model_contract_version:
            raise ValueError("model_contract_version must not be empty")
        if not self.backend_kind:
            raise ValueError("backend_kind must not be empty")
        if not self.canonical_joint_names:
            raise ValueError("canonical_joint_names must not be empty")
        if any(not name for name in self.canonical_joint_names):
            raise ValueError("canonical_joint_names must not contain empty names")
        if len(self.canonical_joint_names) != len(set(self.canonical_joint_names)):
            raise ValueError("canonical_joint_names must be unique")
        if self.qpos_dimension < 1 or self.qvel_dimension < 1:
            raise ValueError("qpos_dimension and qvel_dimension must be positive")
        if not self.initial_keyframe_name:
            raise ValueError("initial_keyframe_name must not be empty")
        if not self.viewer_profile_id:
            raise ValueError("viewer_profile_id must not be empty")
        viewer_reference_parts = (
            self.viewer_declaration,
            self.viewer_declaration_resource_path,
            self.viewer_declaration_url,
        )
        if any(item is None for item in viewer_reference_parts) and any(
            item is not None for item in viewer_reference_parts
        ):
            raise ValueError(
                "viewer declaration, resource path, and URL must be declared together"
            )
        if self.viewer_declaration_resource_path is not None:
            expected_url = repository_resource_public_url(
                self.viewer_declaration_resource_path
            )
            if self.viewer_declaration_url != expected_url:
                raise ValueError(
                    "viewer declaration resource path/URL mismatch: "
                    f"expected {expected_url!r}, got {self.viewer_declaration_url!r}"
                )


def robot_profile_runtime_metadata(profile: RobotProfile) -> dict[str, object]:
    metadata: dict[str, object] = {
        "robot_profile_id": profile.profile_id,
        "model_contract_version": profile.model_contract_version,
        "robot_joint_names": profile.canonical_joint_names,
        "robot_qpos_dimension": profile.qpos_dimension,
    }
    if profile.viewer_declaration is not None:
        assert profile.viewer_declaration_resource_path is not None
        assert profile.viewer_declaration_url is not None
        metadata.update(
            {
                "viewer_robot_declaration_resource_path": (
                    profile.viewer_declaration_resource_path
                ),
                "viewer_robot_declaration_url": profile.viewer_declaration_url,
                "viewer_robot_declaration_digest": viewer_robot_declaration_digest(
                    profile.viewer_declaration
                ),
            }
        )
    return metadata


__all__ = [
    "CoordinateUnitContract",
    "EndpointReference",
    "RobotProfile",
    "robot_profile_runtime_metadata",
]
