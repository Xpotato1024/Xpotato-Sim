"""Declarative profile for the test-only fixture robot."""

from pathlib import Path

from xpotato_sim.runtime.composition.robot_profile import (
    CoordinateUnitContract,
    EndpointReference,
    RobotProfile,
)
from test_robot_plugins.fixture_bot.viewer import FIXTURE_VIEWER_DECLARATION


_RESOURCE_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROBOT_PROFILE = RobotProfile(
    profile_id="fixture_bot",
    profile_contract_version=2,
    model_contract_version="fixture-bot-model/v1",
    backend_kind="mujoco",
    mujoco_model_asset=(
        _RESOURCE_ROOT / "assets" / "mujoco" / "fixture_bot" / "model.xml"
    ),
    canonical_joint_names=("fixture_joint",),
    qpos_dimension=1,
    qvel_dimension=1,
    initial_keyframe_name="home",
    endpoint=EndpointReference(site_name="tip", body_name="link"),
    joint_limit_config_asset=(
        _RESOURCE_ROOT / "configs" / "fixture_bot" / "limits.toml"
    ),
    coordinate_units=CoordinateUnitContract(
        position_unit="meter",
        angle_unit="rad",
        coordinate_frame="MuJoCo world / scene frame",
        quaternion_order="wxyz",
    ),
    viewer_profile_id="fixture_bot",
    supported_capabilities=frozenset(
        {
            "endpoint_ik",
            "physical_fk",
            "local_endpoint_motion",
            "qpos_feasibility_guard",
            "viewer_qpos_rendering",
        }
    ),
    viewer_declaration=FIXTURE_VIEWER_DECLARATION,
    viewer_declaration_resource_path=(
        "assets/mujoco/fixture_bot/viewer-profile.json"
    ),
    viewer_declaration_url="/mujoco/fixture_bot/viewer-profile.json",
)


__all__ = ["FIXTURE_ROBOT_PROFILE"]
