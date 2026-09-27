"""Fixed entry point for the explicit test-only Robot Plugin."""

from xpotato_sim.plugins.robots.registration import (
    ROBOT_ONBOARDING_CONTRACT_VERSION,
    RepositoryResource,
    RobotPluginRegistration,
    RobotResourceDeclaration,
)
from test_robot_plugins.fixture_bot.bundle import FIXTURE_ROBOT_BUNDLE
from test_robot_plugins.fixture_bot.viewer import FIXTURE_VIEWER_DECLARATION


ROBOT_PLUGIN = RobotPluginRegistration(
    identity=FIXTURE_ROBOT_BUNDLE.identity,
    onboarding_contract_version=ROBOT_ONBOARDING_CONTRACT_VERSION,
    bundle=FIXTURE_ROBOT_BUNDLE,
    viewer=FIXTURE_VIEWER_DECLARATION,
    resources=RobotResourceDeclaration(
        model=RepositoryResource("assets/mujoco/fixture_bot/model.xml"),
        configurations=(
            RepositoryResource("configs/fixture_bot/limits.toml"),
        ),
        viewer_declaration=RepositoryResource(
            "assets/mujoco/fixture_bot/viewer-profile.json"
        ),
        viewer_fixture=RepositoryResource(
            "assets/mujoco/fixture_bot/fixture.json"
        ),
        viewer_vfs_resources=tuple(
            RepositoryResource(item.resource_path)
            for item in FIXTURE_VIEWER_DECLARATION.vfs_assets
        ),
    ),
)


__all__ = ["ROBOT_PLUGIN"]
