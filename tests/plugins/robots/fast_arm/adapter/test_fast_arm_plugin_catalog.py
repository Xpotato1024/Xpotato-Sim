from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.bundle import FAST_ARM_ROBOT_BUNDLE
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import (
    FAST_ARM_RUNTIME_PLUGIN,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.viewer import FAST_ARM_VIEWER_DECLARATION
from xpotato_sim.plugins.robots.fast_arm.plugin import ROBOT_PLUGIN


def test_fast_arm_adapter_assembles_canonical_bundle() -> None:
    bundle = FAST_ARM_ROBOT_BUNDLE

    assert bundle.profile is FAST_ARM_ROBOT_PROFILE
    assert bundle.runtime_plugin is FAST_ARM_RUNTIME_PLUGIN
    assert ROBOT_PLUGIN.bundle is bundle
    assert ROBOT_PLUGIN.viewer is FAST_ARM_VIEWER_DECLARATION
    assert bundle.profile.viewer_declaration is FAST_ARM_VIEWER_DECLARATION
    for binding in bundle.capability_providers:
        assert binding.provider.assembly_binding.robot_identity == bundle.identity
        assert (
            binding.provider.assembly_binding.owner is bundle.profile
            or binding.provider.assembly_binding.owner is bundle.runtime_plugin
        )


def test_profile_keeps_stable_logical_identifiers_with_package_ownership() -> None:
    assert FAST_ARM_ROBOT_PROFILE.mujoco_model_asset is ROBOT_PLUGIN.resources.model
    assert FAST_ARM_ROBOT_PROFILE.joint_limit_config_asset is (
        ROBOT_PLUGIN.resources.configurations[0]
    )
    assert ROBOT_PLUGIN.resources.model.logical_identifier == (
        "assets/mujoco/fast_arm/scene.xml"
    )
    assert FAST_ARM_VIEWER_DECLARATION.model_resource_path == (
        ROBOT_PLUGIN.resources.model.logical_identifier
    )
    assert ROBOT_PLUGIN.resources.viewer_declaration.logical_identifier == (
        "assets/mujoco/fast_arm/viewer-profile.json"
    )
    assert ROBOT_PLUGIN.resources.viewer_fixture.logical_identifier == (
        FAST_ARM_VIEWER_DECLARATION.fixture_resource_path
    )
    assert tuple(
        item.logical_identifier for item in ROBOT_PLUGIN.resources.viewer_vfs_resources
    ) == tuple(item.resource_path for item in FAST_ARM_VIEWER_DECLARATION.vfs_assets)
