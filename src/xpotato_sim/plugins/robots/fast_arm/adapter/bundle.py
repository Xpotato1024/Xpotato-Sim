"""Production fast_arm Robot Bundle that delegates to existing behavior."""

from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.initial_state import (
    FAST_ARM_INITIAL_STATE_CONTRACT,
    FAST_ARM_INITIAL_STATE_QPOS_RAD,
    FAST_ARM_INITIAL_STATE_TIP_POSITION_M,
    FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import FAST_ARM_RUNTIME_PLUGIN
from xpotato_sim.runtime.composition.robot_provider_adapters import (
    NamedKeyframeInitialStateProvider,
    ProfileEndpointSceneRoleProvider,
    RuntimeEndpointCommandProvider,
    RuntimeEndpointPoseProvider,
    RuntimeJointPositionCommandProvider,
    RuntimeQposFeasibilityProvider,
)
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
    RobotCommandSemanticProviderBinding,
    RobotBundle,
)


FAST_ARM_ROBOT_BUNDLE = RobotBundle(
    identity=VersionedIdentity("fast_arm", 1),
    profile=FAST_ARM_ROBOT_PROFILE,
    runtime_plugin=FAST_ARM_RUNTIME_PLUGIN,
    command_semantic_providers=(
        RobotCommandSemanticProviderBinding(
            JOINT_POSITION_COMMAND_V1,
            RuntimeJointPositionCommandProvider(FAST_ARM_RUNTIME_PLUGIN),
        ),
    ),
    capability_providers=(
        CapabilityProviderBinding(
            RESET_INITIAL_STATE_V1,
            NamedKeyframeInitialStateProvider(
                FAST_ARM_ROBOT_PROFILE,
                FAST_ARM_INITIAL_STATE_CONTRACT,
            ),
        ),
        CapabilityProviderBinding(
            ENDPOINT_POSE_V1,
            RuntimeEndpointPoseProvider(FAST_ARM_RUNTIME_PLUGIN),
        ),
        CapabilityProviderBinding(
            ENDPOINT_COMMAND_V1,
            RuntimeEndpointCommandProvider(FAST_ARM_RUNTIME_PLUGIN),
        ),
        CapabilityProviderBinding(
            QPOS_FEASIBILITY_V1,
            RuntimeQposFeasibilityProvider(FAST_ARM_RUNTIME_PLUGIN),
        ),
        CapabilityProviderBinding(
            SCENE_ROLE_BINDING_V1,
            ProfileEndpointSceneRoleProvider(FAST_ARM_ROBOT_PROFILE),
        ),
    ),
)


__all__ = [
    "FAST_ARM_INITIAL_STATE_CONTRACT",
    "FAST_ARM_INITIAL_STATE_QPOS_RAD",
    "FAST_ARM_INITIAL_STATE_TIP_POSITION_M",
    "FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ",
    "FAST_ARM_ROBOT_BUNDLE",
]
