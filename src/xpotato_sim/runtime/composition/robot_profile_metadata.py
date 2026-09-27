"""Authoritative robot compatibility metadata merge boundary."""

from __future__ import annotations

from collections.abc import Mapping

REQUIRED_ROBOT_PROFILE_METADATA_KEYS = frozenset(
    {
        "robot_profile_id",
        "model_contract_version",
        "robot_joint_names",
        "robot_qpos_dimension",
    }
)
VIEWER_DECLARATION_REFERENCE_METADATA_KEYS = frozenset(
    {
        "viewer_robot_declaration_resource_path",
        "viewer_robot_declaration_url",
        "viewer_robot_declaration_digest",
    }
)
ROBOT_PROFILE_METADATA_KEYS = (
    REQUIRED_ROBOT_PROFILE_METADATA_KEYS
    | VIEWER_DECLARATION_REFERENCE_METADATA_KEYS
)
_RESERVED_ROBOT_PROFILE_METADATA_KEYS = ROBOT_PROFILE_METADATA_KEYS | {
    "viewer_robot_declaration"
}


def merge_runtime_metadata(
    *metadata_sources: Mapping[str, object] | None,
    authoritative_profile_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Merge ordinary metadata, then overwrite reserved keys authoritatively.

    Production composition supplies the four compatibility keys and, for a
    discovered Robot Plugin, its compact viewer declaration reference. Generic
    pipelines pass no authoritative metadata and retain their previous merge
    behavior.
    """

    merged: dict[str, object] = {}
    for source in metadata_sources:
        if source is not None:
            merged.update(source)

    if authoritative_profile_metadata is None:
        return merged

    actual_keys = frozenset(authoritative_profile_metadata)
    missing = tuple(sorted(REQUIRED_ROBOT_PROFILE_METADATA_KEYS - actual_keys))
    unexpected = tuple(sorted(actual_keys - ROBOT_PROFILE_METADATA_KEYS))
    viewer_reference_keys = actual_keys & VIEWER_DECLARATION_REFERENCE_METADATA_KEYS
    partial_viewer_reference = bool(viewer_reference_keys) and (
        viewer_reference_keys != VIEWER_DECLARATION_REFERENCE_METADATA_KEYS
    )
    if missing or unexpected or partial_viewer_reference:
        raise ValueError(
            "authoritative robot profile metadata keys mismatch: "
            f"missing={missing}, unexpected={unexpected}, "
            f"partial_viewer_reference={partial_viewer_reference}"
        )
    for key in _RESERVED_ROBOT_PROFILE_METADATA_KEYS:
        merged.pop(key, None)
    merged.update(authoritative_profile_metadata)
    return merged


__all__ = [
    "REQUIRED_ROBOT_PROFILE_METADATA_KEYS",
    "ROBOT_PROFILE_METADATA_KEYS",
    "VIEWER_DECLARATION_REFERENCE_METADATA_KEYS",
    "merge_runtime_metadata",
]
