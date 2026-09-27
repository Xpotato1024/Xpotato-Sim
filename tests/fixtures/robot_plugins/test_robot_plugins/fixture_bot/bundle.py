"""Typed provider assembly for the test-only fixture robot."""

from xpotato_sim.runtime.experiment.contracts import (
    JOINT_POSITION_COMMAND_V1,
    VersionedIdentity,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
    SCENE_ROLE_BINDING_V1,
    CapabilityProviderBinding,
    InitialStateContract,
    RobotCommandSemanticProviderBinding,
    RobotBundle,
)
from xpotato_sim.runtime.composition.robot_provider_adapters import (
    NamedKeyframeInitialStateProvider,
    ProfileEndpointSceneRoleProvider,
    RuntimeEndpointCommandProvider,
    RuntimeEndpointPoseProvider,
    RuntimeJointPositionCommandProvider,
    RuntimeQposFeasibilityProvider,
)
from test_robot_plugins.fixture_bot.profile import FIXTURE_ROBOT_PROFILE
from test_robot_plugins.fixture_bot.runtime import FIXTURE_RUNTIME_PLUGIN


FIXTURE_INITIAL_STATE_CONTRACT = InitialStateContract(
    identity=VersionedIdentity("fixture_bot_initial_state", 2),
    source_kind="named_keyframe",
    source_id="home",
    qpos_rad=(0.25,),
    tip_position_m=(0.0, 0.0, 0.0),
    tool_orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
    frame="MuJoCo world / scene frame",
    position_unit="meter",
    orientation_unit="unit_quaternion",
    quaternion_order="wxyz",
)

FIXTURE_ROBOT_BUNDLE = RobotBundle(
    identity=VersionedIdentity("fixture_bot", 2),
    profile=FIXTURE_ROBOT_PROFILE,
    runtime_plugin=FIXTURE_RUNTIME_PLUGIN,
    command_semantic_providers=(
        RobotCommandSemanticProviderBinding(
            JOINT_POSITION_COMMAND_V1,
            RuntimeJointPositionCommandProvider(FIXTURE_RUNTIME_PLUGIN),
        ),
    ),
    capability_providers=(
        CapabilityProviderBinding(
            RESET_INITIAL_STATE_V1,
            NamedKeyframeInitialStateProvider(
                FIXTURE_ROBOT_PROFILE, FIXTURE_INITIAL_STATE_CONTRACT
            ),
        ),
        CapabilityProviderBinding(
            ENDPOINT_POSE_V1, RuntimeEndpointPoseProvider(FIXTURE_RUNTIME_PLUGIN)
        ),
        CapabilityProviderBinding(
            ENDPOINT_COMMAND_V1,
            RuntimeEndpointCommandProvider(FIXTURE_RUNTIME_PLUGIN),
        ),
        CapabilityProviderBinding(
            QPOS_FEASIBILITY_V1,
            RuntimeQposFeasibilityProvider(FIXTURE_RUNTIME_PLUGIN),
        ),
        CapabilityProviderBinding(
            SCENE_ROLE_BINDING_V1,
            ProfileEndpointSceneRoleProvider(FIXTURE_ROBOT_PROFILE),
        ),
    ),
)


__all__ = ["FIXTURE_INITIAL_STATE_CONTRACT", "FIXTURE_ROBOT_BUNDLE"]
