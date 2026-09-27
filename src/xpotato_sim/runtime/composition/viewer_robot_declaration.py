"""Versioned composition declaration consumed by the rendering-only viewer."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote


VIEWER_ROBOT_DECLARATION_SCHEMA_VERSION = "viewer-robot-declaration/v1"


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object with string keys")
    return value


def _require_sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _require_exact_keys(
    value: Mapping[str, object], expected: frozenset[str], name: str
) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise ValueError(
            f"{name} keys mismatch: expected {tuple(sorted(expected))}, "
            f"got {tuple(sorted(actual))}"
        )


def _require_local_url(value: str, name: str) -> None:
    _require_string(value, name)
    path = PurePosixPath(value)
    if not value.startswith("/") or value.startswith("//") or ".." in path.parts:
        raise ValueError(f"{name} must be a local absolute-path URL")


def repository_resource_public_url(repository_path: str) -> str:
    """Map a repository asset path to its deterministic viewer URL."""

    _require_string(repository_path, "repository resource path")
    path = PurePosixPath(repository_path)
    if (
        path.is_absolute()
        or "\\" in repository_path
        or ".." in path.parts
        or path.parts[:1] != ("assets",)
        or len(path.parts) < 2
        or any(re.fullmatch(r"[A-Za-z0-9._~-]+", part) is None for part in path.parts)
    ):
        raise ValueError(
            "viewer repository resource path must be below the assets root"
        )
    public_path = PurePosixPath(*path.parts[1:]).as_posix()
    return "/" + quote(public_path, safe="/-._~")


@dataclass(frozen=True, slots=True)
class ViewerVfsAsset:
    vfs_path: str
    resource_path: str
    url: str

    def __post_init__(self) -> None:
        _require_string(self.vfs_path, "viewer VFS path")
        _require_string(self.resource_path, "viewer VFS resource path")
        vfs_path = PurePosixPath(self.vfs_path)
        if vfs_path.is_absolute() or ".." in vfs_path.parts:
            raise ValueError("viewer VFS path must not escape the virtual root")
        _require_local_url(self.url, "viewer VFS URL")
        expected_url = repository_resource_public_url(self.resource_path)
        if self.url != expected_url:
            raise ValueError(
                "viewer VFS resource path/URL mismatch: "
                f"expected {expected_url!r}, got {self.url!r}"
            )

    def to_document(self) -> dict[str, object]:
        return {
            "vfsPath": self.vfs_path,
            "resourcePath": self.resource_path,
            "url": self.url,
        }


@dataclass(frozen=True, slots=True)
class ViewerBodyVisualStyle:
    key: str
    color: str
    label: str
    detail: str

    def __post_init__(self) -> None:
        for name, value in (
            ("viewer body style key", self.key),
            ("viewer body style color", self.color),
            ("viewer body style label", self.label),
            ("viewer body style detail", self.detail),
        ):
            _require_string(value, name)

    def to_document(self) -> dict[str, object]:
        return {
            "key": self.key,
            "color": self.color,
            "label": self.label,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ViewerVisualStyleSelection:
    match: str
    style_key: str

    def __post_init__(self) -> None:
        _require_string(self.match, "viewer visual style match")
        _require_string(self.style_key, "viewer visual style key")

    def to_document(self) -> dict[str, object]:
        return {"match": self.match, "styleKey": self.style_key}


@dataclass(frozen=True, slots=True)
class ViewerAxisVisualStyle:
    color: str
    label: str
    detail: str

    def __post_init__(self) -> None:
        for name, value in (
            ("viewer axis style color", self.color),
            ("viewer axis style label", self.label),
            ("viewer axis style detail", self.detail),
        ):
            _require_string(value, name)

    def to_document(self) -> dict[str, object]:
        return {"color": self.color, "label": self.label, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ViewerRobotDeclaration:
    schema_version: str
    profile_id: str
    profile_contract_version: int
    model_contract_version: str
    model_url: str
    model_resource_path: str
    initial_keyframe_name: str
    initial_pose_source_label: str
    fixture_url: str
    fixture_resource_path: str
    vfs_assets: tuple[ViewerVfsAsset, ...]
    visual_style_selection: tuple[ViewerVisualStyleSelection, ...]
    body_visual_styles: tuple[ViewerBodyVisualStyle, ...]
    axis_visual_styles: tuple[ViewerAxisVisualStyle, ...]
    joint_names: tuple[str, ...]
    qpos_dimension: int

    def __post_init__(self) -> None:
        if self.schema_version != VIEWER_ROBOT_DECLARATION_SCHEMA_VERSION:
            raise ValueError(
                "unsupported viewer robot declaration schema version: "
                f"{self.schema_version!r}"
            )
        for name, value in (
            ("viewer profile ID", self.profile_id),
            ("viewer model contract version", self.model_contract_version),
            ("viewer model resource path", self.model_resource_path),
            ("viewer fixture resource path", self.fixture_resource_path),
            ("viewer initial keyframe name", self.initial_keyframe_name),
            ("viewer initial pose source label", self.initial_pose_source_label),
        ):
            _require_string(value, name)
        _require_local_url(self.model_url, "viewer model URL")
        _require_local_url(self.fixture_url, "viewer fixture URL")
        expected_model_url = repository_resource_public_url(self.model_resource_path)
        if self.model_url != expected_model_url:
            raise ValueError(
                "viewer model resource path/URL mismatch: "
                f"expected {expected_model_url!r}, got {self.model_url!r}"
            )
        expected_fixture_url = repository_resource_public_url(
            self.fixture_resource_path
        )
        if self.fixture_url != expected_fixture_url:
            raise ValueError(
                "viewer fixture resource path/URL mismatch: "
                f"expected {expected_fixture_url!r}, got {self.fixture_url!r}"
            )
        _require_positive_int(
            self.profile_contract_version, "viewer profile contract version"
        )
        _require_positive_int(self.qpos_dimension, "viewer qpos dimension")
        if not self.joint_names or any(not name for name in self.joint_names):
            raise ValueError("viewer joint names must contain non-empty names")
        if len(self.joint_names) != len(set(self.joint_names)):
            raise ValueError("viewer joint names must be unique")
        for name, values in (
            ("viewer VFS paths", tuple(item.vfs_path for item in self.vfs_assets)),
            (
                "viewer VFS resource paths",
                tuple(item.resource_path for item in self.vfs_assets),
            ),
            (
                "viewer visual style matches",
                tuple(item.match for item in self.visual_style_selection),
            ),
            ("viewer body style keys", tuple(item.key for item in self.body_visual_styles)),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        style_keys = frozenset(item.key for item in self.body_visual_styles)
        unknown_style_keys = tuple(
            item.style_key
            for item in self.visual_style_selection
            if item.style_key not in style_keys
        )
        if unknown_style_keys:
            raise ValueError(
                "viewer visual style selection references unknown style keys: "
                f"{unknown_style_keys}"
            )

    def to_document(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "profileId": self.profile_id,
            "profileContractVersion": self.profile_contract_version,
            "modelContractVersion": self.model_contract_version,
            "modelUrl": self.model_url,
            "modelResourcePath": self.model_resource_path,
            "initialKeyframeName": self.initial_keyframe_name,
            "initialPoseSourceLabel": self.initial_pose_source_label,
            "fixtureUrl": self.fixture_url,
            "fixtureResourcePath": self.fixture_resource_path,
            "vfsAssets": [item.to_document() for item in self.vfs_assets],
            "visualStyleSelection": [
                item.to_document() for item in self.visual_style_selection
            ],
            "bodyVisualStyles": [item.to_document() for item in self.body_visual_styles],
            "axisVisualStyles": [item.to_document() for item in self.axis_visual_styles],
            "jointNames": list(self.joint_names),
            "qposDimension": self.qpos_dimension,
        }


_ROOT_KEYS = frozenset(
    {
        "schemaVersion",
        "profileId",
        "profileContractVersion",
        "modelContractVersion",
        "modelUrl",
        "modelResourcePath",
        "initialKeyframeName",
        "initialPoseSourceLabel",
        "fixtureUrl",
        "fixtureResourcePath",
        "vfsAssets",
        "visualStyleSelection",
        "bodyVisualStyles",
        "axisVisualStyles",
        "jointNames",
        "qposDimension",
    }
)


def decode_viewer_robot_declaration(value: object) -> ViewerRobotDeclaration:
    root = _require_mapping(value, "viewer robot declaration")
    _require_exact_keys(root, _ROOT_KEYS, "viewer robot declaration")

    def records(name: str, expected: frozenset[str]) -> tuple[Mapping[str, object], ...]:
        result: list[Mapping[str, object]] = []
        for index, item in enumerate(_require_sequence(root[name], name)):
            record = _require_mapping(item, f"{name}[{index}]")
            _require_exact_keys(record, expected, f"{name}[{index}]")
            result.append(record)
        return tuple(result)

    vfs_assets = records(
        "vfsAssets", frozenset({"vfsPath", "resourcePath", "url"})
    )
    selections = records(
        "visualStyleSelection", frozenset({"match", "styleKey"})
    )
    body_styles = records(
        "bodyVisualStyles", frozenset({"key", "color", "label", "detail"})
    )
    axis_styles = records(
        "axisVisualStyles", frozenset({"color", "label", "detail"})
    )
    joint_names = tuple(
        _require_string(item, f"jointNames[{index}]")
        for index, item in enumerate(_require_sequence(root["jointNames"], "jointNames"))
    )
    return ViewerRobotDeclaration(
        schema_version=_require_string(root["schemaVersion"], "schemaVersion"),
        profile_id=_require_string(root["profileId"], "profileId"),
        profile_contract_version=_require_positive_int(
            root["profileContractVersion"], "profileContractVersion"
        ),
        model_contract_version=_require_string(
            root["modelContractVersion"], "modelContractVersion"
        ),
        model_url=_require_string(root["modelUrl"], "modelUrl"),
        model_resource_path=_require_string(
            root["modelResourcePath"], "modelResourcePath"
        ),
        initial_keyframe_name=_require_string(
            root["initialKeyframeName"], "initialKeyframeName"
        ),
        initial_pose_source_label=_require_string(
            root["initialPoseSourceLabel"], "initialPoseSourceLabel"
        ),
        fixture_url=_require_string(root["fixtureUrl"], "fixtureUrl"),
        fixture_resource_path=_require_string(
            root["fixtureResourcePath"], "fixtureResourcePath"
        ),
        vfs_assets=tuple(
            ViewerVfsAsset(
                vfs_path=_require_string(item["vfsPath"], "vfsPath"),
                resource_path=_require_string(item["resourcePath"], "resourcePath"),
                url=_require_string(item["url"], "url"),
            )
            for item in vfs_assets
        ),
        visual_style_selection=tuple(
            ViewerVisualStyleSelection(
                match=_require_string(item["match"], "match"),
                style_key=_require_string(item["styleKey"], "styleKey"),
            )
            for item in selections
        ),
        body_visual_styles=tuple(
            ViewerBodyVisualStyle(
                key=_require_string(item["key"], "key"),
                color=_require_string(item["color"], "color"),
                label=_require_string(item["label"], "label"),
                detail=_require_string(item["detail"], "detail"),
            )
            for item in body_styles
        ),
        axis_visual_styles=tuple(
            ViewerAxisVisualStyle(
                color=_require_string(item["color"], "color"),
                label=_require_string(item["label"], "label"),
                detail=_require_string(item["detail"], "detail"),
            )
            for item in axis_styles
        ),
        joint_names=joint_names,
        qpos_dimension=_require_positive_int(root["qposDimension"], "qposDimension"),
    )


def viewer_robot_declaration_canonical_bytes(
    declaration: ViewerRobotDeclaration,
) -> bytes:
    return json.dumps(
        declaration.to_document(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def viewer_robot_declaration_digest(declaration: ViewerRobotDeclaration) -> str:
    return "sha256:" + hashlib.sha256(
        viewer_robot_declaration_canonical_bytes(declaration)
    ).hexdigest()


__all__ = [
    "VIEWER_ROBOT_DECLARATION_SCHEMA_VERSION",
    "ViewerAxisVisualStyle",
    "ViewerBodyVisualStyle",
    "ViewerRobotDeclaration",
    "ViewerVfsAsset",
    "ViewerVisualStyleSelection",
    "decode_viewer_robot_declaration",
    "repository_resource_public_url",
    "viewer_robot_declaration_canonical_bytes",
    "viewer_robot_declaration_digest",
]
