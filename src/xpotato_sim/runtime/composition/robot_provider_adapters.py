"""Robot-independent providers that delegate to typed profile/plugin contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xpotato_sim.motion.base import MotionGenerator
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from xpotato_sim.runtime.safety.qpos_feasibility import QposFeasibilityGuard
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
    ROBOT_TOOL_ENDPOINT_ROLE,
    SCENE_ROLE_BINDING_V1,
    EndpointPoseObservation,
    InitialStateContract,
    InitialStateReference,
    ProviderAssemblyBinding,
    SemanticRoleBinding,
)
from xpotato_sim.runtime.experiment.contracts import JOINT_POSITION_COMMAND_V1
from xpotato_sim.schemas import JointPositionCommand, RobotCommand
from xpotato_sim.runtime.composition.robot_plugin import RobotRuntimePlugin
from xpotato_sim.schemas import MuJoCoState


@dataclass(frozen=True, slots=True)
class NamedKeyframeInitialStateProvider:
    profile: RobotProfile
    contract: InitialStateContract | None = None
    robot_identity: VersionedIdentity | None = None
    capability_identity = RESET_INITIAL_STATE_V1

    @property
    def assembly_binding(self) -> ProviderAssemblyBinding:
        identity = self.robot_identity or VersionedIdentity(
            self.profile.profile_id,
            self.profile.profile_contract_version,
        )
        return ProviderAssemblyBinding(identity, self.profile)

    def resolve_initial_state(self) -> InitialStateReference:
        return InitialStateReference(
            source_kind="named_keyframe",
            source_id=self.profile.initial_keyframe_name,
        )

    def initial_state_contract(self) -> InitialStateContract:
        if self.contract is None:
            raise ValueError(
                "named-keyframe provider has no canonical initial-state contract"
            )
        return self.contract


@dataclass(frozen=True, slots=True)
class RuntimeEndpointPoseProvider:
    plugin: RobotRuntimePlugin
    robot_identity: VersionedIdentity | None = None
    capability_identity = ENDPOINT_POSE_V1

    @property
    def assembly_binding(self) -> ProviderAssemblyBinding:
        identity = self.robot_identity or VersionedIdentity(
            self.plugin.profile.profile_id,
            self.plugin.profile.profile_contract_version,
        )
        return ProviderAssemblyBinding(identity, self.plugin)

    def observe_endpoint_pose(self, state: MuJoCoState) -> EndpointPoseObservation:
        return EndpointPoseObservation(
            position_m=self.plugin.endpoint_position_from_state(state),
            quaternion_wxyz=self.plugin.endpoint_orientation_from_state(state),
        )


@dataclass(frozen=True, slots=True)
class RuntimeEndpointCommandProvider:
    plugin: RobotRuntimePlugin
    robot_identity: VersionedIdentity | None = None
    capability_identity = ENDPOINT_COMMAND_V1

    @property
    def assembly_binding(self) -> ProviderAssemblyBinding:
        identity = self.robot_identity or VersionedIdentity(
            self.plugin.profile.profile_id,
            self.plugin.profile.profile_contract_version,
        )
        return ProviderAssemblyBinding(identity, self.plugin)

    def build_target_motion_generator(
        self,
        *,
        seed_joint_angles_rad: tuple[float, ...] | None,
        discontinuity_threshold_rad: float | None,
        discontinuity_threshold_label: str,
    ) -> MotionGenerator:
        return self.plugin.build_target_motion_generator(
            seed_joint_angles_rad=seed_joint_angles_rad,
            discontinuity_threshold_rad=discontinuity_threshold_rad,
            discontinuity_threshold_label=discontinuity_threshold_label,
        )

    def build_local_endpoint_motion_generator(self) -> MotionGenerator:
        return self.plugin.build_local_endpoint_motion_generator()


@dataclass(frozen=True, slots=True)
class RuntimeJointPositionCommandProvider:
    plugin: RobotRuntimePlugin
    robot_identity: VersionedIdentity | None = None
    command_semantics_identity = JOINT_POSITION_COMMAND_V1
    command_type = JointPositionCommand

    @property
    def assembly_binding(self) -> ProviderAssemblyBinding:
        identity = self.robot_identity or VersionedIdentity(
            self.plugin.profile.profile_id,
            self.plugin.profile.profile_contract_version,
        )
        return ProviderAssemblyBinding(identity, self.plugin)

    def execute(self, command: RobotCommand, *, backend: object) -> None:
        if not isinstance(command, JointPositionCommand):
            raise TypeError(
                "joint_position_command/v1 provider requires "
                "JointPositionCommand"
            )
        apply_joint_position_command = getattr(
            backend, "apply_joint_position_command", None
        )
        if not callable(apply_joint_position_command):
            raise TypeError(
                "joint_position_command/v1 backend must provide "
                "apply_joint_position_command()"
            )
        apply_joint_position_command(command)


@dataclass(frozen=True, slots=True)
class RuntimeQposFeasibilityProvider:
    plugin: RobotRuntimePlugin
    robot_identity: VersionedIdentity | None = None
    capability_identity = QPOS_FEASIBILITY_V1

    @property
    def assembly_binding(self) -> ProviderAssemblyBinding:
        identity = self.robot_identity or VersionedIdentity(
            self.plugin.profile.profile_id,
            self.plugin.profile.profile_contract_version,
        )
        return ProviderAssemblyBinding(identity, self.plugin)

    def build_guard(
        self, *, model: object, config_path: str | Path | None
    ) -> QposFeasibilityGuard:
        return self.plugin.build_qpos_feasibility_guard(
            model=model,
            config_path=config_path,
        )


@dataclass(frozen=True, slots=True)
class ProfileEndpointSceneRoleProvider:
    profile: RobotProfile
    robot_identity: VersionedIdentity | None = None
    capability_identity = SCENE_ROLE_BINDING_V1

    @property
    def assembly_binding(self) -> ProviderAssemblyBinding:
        identity = self.robot_identity or VersionedIdentity(
            self.profile.profile_id,
            self.profile.profile_contract_version,
        )
        return ProviderAssemblyBinding(identity, self.profile)

    def semantic_role_bindings(self) -> tuple[SemanticRoleBinding, ...]:
        if self.profile.endpoint.site_name is not None:
            target_kind = "site"
            target_id = self.profile.endpoint.site_name
        elif self.profile.endpoint.body_name is not None:
            target_kind = "body"
            target_id = self.profile.endpoint.body_name
        else:  # EndpointReference already rejects this state.
            raise ValueError("robot endpoint role has no backend binding")
        return (
            SemanticRoleBinding(
                role=ROBOT_TOOL_ENDPOINT_ROLE,
                backend_kind=self.profile.backend_kind,
                target_kind=target_kind,
                target_id=target_id,
                object_kind="robot_endpoint",
                frame=self.profile.coordinate_units.coordinate_frame,
                unit=self.profile.coordinate_units.position_unit,
            ),
        )


__all__ = [
    "NamedKeyframeInitialStateProvider",
    "ProfileEndpointSceneRoleProvider",
    "RuntimeEndpointCommandProvider",
    "RuntimeJointPositionCommandProvider",
    "RuntimeEndpointPoseProvider",
    "RuntimeQposFeasibilityProvider",
]
