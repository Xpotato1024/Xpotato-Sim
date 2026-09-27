from __future__ import annotations

from dataclasses import replace

import pytest

from xpotato_sim.plugins.robots.catalog import (
    registered_robot_plugin_ids,
    registered_robot_bundle_ids,
    registered_robot_profile_ids,
    registered_robot_runtime_plugin_ids,
    resolve_robot_bundle,
    resolve_robot_plugin_registration,
    resolve_robot_profile,
    resolve_robot_runtime,
    resolve_robot_runtime_plugin,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.bundle import FAST_ARM_ROBOT_BUNDLE
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import (
    FAST_ARM_RUNTIME_PLUGIN,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.viewer import FAST_ARM_VIEWER_DECLARATION
from xpotato_sim.plugins.robots.fast_arm.plugin import ROBOT_PLUGIN
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle as root_resolve_robot_bundle
from xpotato_sim.plugins.robots.catalog import resolve_robot_runtime as root_resolve_robot_runtime
from xpotato_sim.runtime.composition.robot_profile import robot_profile_runtime_metadata
from xpotato_sim.runtime.composition.robot_profile_metadata import merge_runtime_metadata
from xpotato_sim.runtime.composition.viewer_robot_declaration import viewer_robot_declaration_digest
from xpotato_sim.schemas import MuJoCoState
from xpotato_sim.transport.websocket import serialize_mujoco_state_message


def test_catalog_resolvers_project_one_canonical_bundle() -> None:
    bundle = resolve_robot_bundle("fast_arm")
    resolved = resolve_robot_runtime("fast_arm")

    assert bundle is FAST_ARM_ROBOT_BUNDLE
    assert resolve_robot_profile("fast_arm") is bundle.profile
    assert resolve_robot_runtime_plugin("fast_arm") is bundle.runtime_plugin
    assert resolved.profile is bundle.profile
    assert resolved.plugin is bundle.runtime_plugin
    assert bundle.profile is FAST_ARM_ROBOT_PROFILE
    assert bundle.runtime_plugin is FAST_ARM_RUNTIME_PLUGIN
    assert resolve_robot_plugin_registration("fast_arm") is ROBOT_PLUGIN
    with pytest.raises(ValueError, match="Robot Plugin logical version mismatch"):
        resolve_robot_plugin_registration("fast_arm", robot_logical_version=2)
    for resolver in (
        resolve_robot_bundle,
        resolve_robot_profile,
        resolve_robot_runtime_plugin,
        resolve_robot_runtime,
    ):
        with pytest.raises(ValueError, match="version mismatch"):
            resolver("fast_arm", robot_logical_version=2)
    assert ROBOT_PLUGIN.bundle is bundle
    assert ROBOT_PLUGIN.viewer is FAST_ARM_VIEWER_DECLARATION
    assert bundle.profile.viewer_declaration is FAST_ARM_VIEWER_DECLARATION
    assert registered_robot_plugin_ids() == ("fast_arm",)
    assert registered_robot_bundle_ids() == ("fast_arm",)
    assert registered_robot_profile_ids() == registered_robot_bundle_ids()
    assert registered_robot_runtime_plugin_ids() == registered_robot_bundle_ids()
    for binding in bundle.capability_providers:
        assert binding.provider.assembly_binding.robot_identity == bundle.identity
        assert (
            binding.provider.assembly_binding.owner is bundle.profile
            or binding.provider.assembly_binding.owner is bundle.runtime_plugin
        )


def test_runtime_metadata_delivers_a_compact_viewer_declaration_reference() -> None:
    metadata = robot_profile_runtime_metadata(FAST_ARM_ROBOT_PROFILE)

    assert "viewer_robot_declaration" not in metadata
    assert metadata["viewer_robot_declaration_resource_path"] == (
        "assets/mujoco/fast_arm/viewer-profile.json"
    )
    assert metadata["viewer_robot_declaration_url"] == (
        "/mujoco/fast_arm/viewer-profile.json"
    )
    assert metadata["viewer_robot_declaration_digest"] == (
        viewer_robot_declaration_digest(FAST_ARM_VIEWER_DECLARATION)
    )
    assert metadata["viewer_robot_declaration_digest"] == (
        "sha256:0a35ec57fe704fc6bb853e53db9f449a0721c751ece19addf787dfa50cce58e6"
    )


def test_frame_metadata_size_is_independent_of_viewer_declaration_size() -> None:
    compact_metadata = robot_profile_runtime_metadata(FAST_ARM_ROBOT_PROFILE)
    compact_bytes = serialize_mujoco_state_message(
        MuJoCoState(frame_index=1, time_s=0.1, qpos=(0.0,) * 4, metadata=compact_metadata)
    ).encode()
    full_bytes = serialize_mujoco_state_message(
        MuJoCoState(
            frame_index=1,
            time_s=0.1,
            qpos=(0.0,) * 4,
            metadata={
                **compact_metadata,
                "viewer_robot_declaration": FAST_ARM_VIEWER_DECLARATION.to_document(),
            },
        )
    ).encode()
    expanded_declaration = replace(
        FAST_ARM_VIEWER_DECLARATION,
        axis_visual_styles=FAST_ARM_VIEWER_DECLARATION.axis_visual_styles * 100,
    )
    expanded_profile = replace(
        FAST_ARM_ROBOT_PROFILE,
        viewer_declaration=expanded_declaration,
    )
    expanded_compact_bytes = serialize_mujoco_state_message(
        MuJoCoState(
            frame_index=1,
            time_s=0.1,
            qpos=(0.0,) * 4,
            metadata=robot_profile_runtime_metadata(expanded_profile),
        )
    ).encode()

    assert len(compact_bytes) < len(full_bytes)
    assert len(expanded_compact_bytes) == len(compact_bytes)


def test_viewer_reference_metadata_is_atomic_and_generic_profiles_remain_supported() -> None:
    authoritative = robot_profile_runtime_metadata(FAST_ARM_ROBOT_PROFILE)
    merged = merge_runtime_metadata(
        {"viewer_robot_declaration": {"spoofed": True}},
        authoritative_profile_metadata=authoritative,
    )
    assert "viewer_robot_declaration" not in merged
    assert {key: merged[key] for key in authoritative} == authoritative

    partial = dict(authoritative)
    partial.pop("viewer_robot_declaration_digest")
    with pytest.raises(ValueError, match="partial_viewer_reference=True"):
        merge_runtime_metadata(authoritative_profile_metadata=partial)

    generic_profile = replace(
        FAST_ARM_ROBOT_PROFILE,
        viewer_declaration=None,
        viewer_declaration_resource_path=None,
        viewer_declaration_url=None,
    )
    generic_metadata = robot_profile_runtime_metadata(generic_profile)
    assert set(generic_metadata) == {
        "robot_profile_id",
        "model_contract_version",
        "robot_joint_names",
        "robot_qpos_dimension",
    }
    assert merge_runtime_metadata(
        authoritative_profile_metadata=generic_metadata
    ) == generic_metadata


def test_runtime_root_resolvers_share_catalog_objects() -> None:
    bundle = resolve_robot_bundle("fast_arm")

    assert root_resolve_robot_bundle("fast_arm") is bundle
    assert root_resolve_robot_runtime("fast_arm") == resolve_robot_runtime("fast_arm")
