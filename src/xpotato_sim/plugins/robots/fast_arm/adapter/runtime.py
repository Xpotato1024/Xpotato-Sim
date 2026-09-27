"""fast_arm production implementation of the Robot Runtime Plugin contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import (
    FAST_ARM_ENDPOINT_LINK_LENGTHS_M,
    FastArmEndpointForwardKinematicsSolver,
    FastArmEndpointInverseKinematicsSolver,
    FastArmMuJoCoModelForwardKinematicsSolver,
)
from xpotato_sim.motion import LocalEndpointMotionGenerator, TargetToJointMotionGenerator
from xpotato_sim.plugins.robots.fast_arm.adapter.model_contract import validate_fast_arm_model_name_contract
from xpotato_sim.mujoco_backend.model_info import inspect_mujoco_model
from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
from xpotato_sim.plugins.robots.fast_arm.adapter.feasibility import (
    FastArmJointLimitGuard,
    load_and_validate_fast_arm_joint_limit_config,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.plugins.robots.fast_arm.adapter.resources import (
    FAST_ARM_SCENE_RESOURCE,
    fast_arm_model_resource_bytes,
)
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.composition.robot_resource import PackageResource
from xpotato_sim.runtime.composition.robot_plugin import state_transform_by_name, validate_profile_model_dimensions
from xpotato_sim.runtime.control.viewer_motion_policy import (
    DEFAULT_VIEWER_LOCAL_ENDPOINT_DAMPING,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_FD_EPSILON_RAD,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_DELTA_PER_TICK_M,
    DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_QPOS_DELTA_NORM_RAD,
)
from xpotato_sim.schemas import MuJoCoState


@dataclass(frozen=True, slots=True)
class FastArmRuntimePlugin:
    profile: RobotProfile = FAST_ARM_ROBOT_PROFILE

    def _endpoint_site_name(self) -> str:
        site_name = self.profile.endpoint.site_name
        if site_name is None:
            raise ValueError("fast_arm Runtime Plugin requires an endpoint site binding")
        return site_name

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    def validate_model(self, model: object) -> None:
        if self.profile.canonical_joint_names != FAST_ARM_ROBOT_PROFILE.canonical_joint_names:
            raise ValueError("fast_arm profile joint order mismatch")
        if (
            self.profile.qpos_dimension
            != FAST_ARM_ROBOT_PROFILE.qpos_dimension
            or self.profile.qvel_dimension
            != FAST_ARM_ROBOT_PROFILE.qvel_dimension
        ):
            raise ValueError("fast_arm profile requires qpos/qvel dimensions 4/4")
        validate_profile_model_dimensions(self.profile, model)
        info = inspect_mujoco_model(model)
        if info.joint_names != self.profile.canonical_joint_names:
            raise ValueError(
                "robot profile/model joint order mismatch for fast_arm: "
                f"expected {self.profile.canonical_joint_names}, got {info.joint_names}"
            )
        validate_fast_arm_model_name_contract(model)

    def build_inverse_kinematics(self) -> FastArmEndpointInverseKinematicsSolver:
        return FastArmEndpointInverseKinematicsSolver(link_lengths_m=FAST_ARM_ENDPOINT_LINK_LENGTHS_M)

    def build_forward_kinematics(self) -> FastArmEndpointForwardKinematicsSolver:
        return FastArmEndpointForwardKinematicsSolver(link_lengths_m=FAST_ARM_ENDPOINT_LINK_LENGTHS_M)

    def build_target_motion_generator(
        self,
        *,
        seed_joint_angles_rad: tuple[float, ...] | None,
        discontinuity_threshold_rad: float | None,
        discontinuity_threshold_label: str,
    ) -> TargetToJointMotionGenerator:
        return TargetToJointMotionGenerator(
            self.build_inverse_kinematics(),
            seed_joint_angles_rad=seed_joint_angles_rad,
            qpos_joint_count=self.profile.qpos_dimension,
            **(
                {}
                if discontinuity_threshold_rad is None
                else {
                    "discontinuity_threshold_rad": discontinuity_threshold_rad,
                    "discontinuity_threshold_label": discontinuity_threshold_label,
                }
            ),
        )

    def build_local_endpoint_motion_generator(self) -> LocalEndpointMotionGenerator:
        return LocalEndpointMotionGenerator(
            endpoint_kinematics=FastArmMuJoCoModelForwardKinematicsSolver(
                tip_site_name=self._endpoint_site_name()
            ),
            endpoint_model="mujoco_model_aligned_tip_site",
            fd_epsilon_rad=DEFAULT_VIEWER_LOCAL_ENDPOINT_FD_EPSILON_RAD,
            damping=DEFAULT_VIEWER_LOCAL_ENDPOINT_DAMPING,
            max_qpos_delta_norm_rad=DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_QPOS_DELTA_NORM_RAD,
            max_endpoint_delta_per_tick_m=DEFAULT_VIEWER_LOCAL_ENDPOINT_MAX_DELTA_PER_TICK_M,
        )

    def build_qpos_feasibility_guard(
        self,
        *,
        model: object,
        config_path: str | Path | PackageResource | None,
    ) -> FastArmJointLimitGuard:
        resolved_path = (
            self.profile.joint_limit_config_asset
            if config_path is None
            else config_path
            if isinstance(config_path, PackageResource)
            else Path(config_path)
        )
        if resolved_path is None:
            raise ValueError("fast_arm profile requires a joint-limit configuration asset")
        return FastArmJointLimitGuard(
            load_and_validate_fast_arm_joint_limit_config(resolved_path, model=model)
        )

    def build_simulator(
        self,
        *,
        model_path: str | Path | None,
        initial_keyframe_name: str | None,
    ) -> HeadlessMuJoCoSimulator:
        if model_path is not None:
            return HeadlessMuJoCoSimulator.from_model_path(
                model_path,
                initial_keyframe_name=initial_keyframe_name,
            )
        model_xml, assets = fast_arm_model_resource_bytes()
        return HeadlessMuJoCoSimulator.from_xml_resources(
            model_xml,
            assets=assets,
            logical_model_path=FAST_ARM_SCENE_RESOURCE.logical_identifier,
            initial_keyframe_name=initial_keyframe_name,
        )

    def endpoint_position_from_state(self, state: MuJoCoState) -> tuple[float, float, float] | None:
        site_name = self.profile.endpoint.site_name
        if site_name is None:
            return None
        transform = state_transform_by_name(state.sites, site_name)
        return None if transform is None else tuple(getattr(transform, "position_m"))

    def endpoint_orientation_from_state(
        self, state: MuJoCoState
    ) -> tuple[float, float, float, float] | None:
        site_name = self.profile.endpoint.site_name
        if site_name is None:
            return None
        transform = state_transform_by_name(state.sites, site_name)
        return None if transform is None else tuple(getattr(transform, "quaternion_wxyz"))


FAST_ARM_RUNTIME_PLUGIN = FastArmRuntimePlugin()


def build_fast_arm_simulator() -> HeadlessMuJoCoSimulator:
    """Build the canonical fast_arm model through Profile-owned resources."""

    return FAST_ARM_RUNTIME_PLUGIN.build_simulator(
        model_path=None,
        initial_keyframe_name=FAST_ARM_ROBOT_PROFILE.initial_keyframe_name,
    )


__all__ = ["FAST_ARM_RUNTIME_PLUGIN", "FastArmRuntimePlugin", "build_fast_arm_simulator"]
