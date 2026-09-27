"""Small robot-specific runtime behavior used only by onboarding tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
from xpotato_sim.runtime.safety.qpos_feasibility import NoOpQposFeasibilityGuard
from xpotato_sim.runtime.composition.robot_plugin import (
    state_transform_by_name,
    validate_profile_model_dimensions,
)
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.schemas import InputIntent, JointCommand, MotionCommand, MuJoCoState
from test_robot_plugins.fixture_bot.profile import FIXTURE_ROBOT_PROFILE


@dataclass(frozen=True, slots=True)
class _FixtureForwardKinematics:
    def forward(self, joint_angles_rad: tuple[float, ...]) -> tuple[float, float, float]:
        return (float(joint_angles_rad[0]), 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class _FixtureInverseKinematics:
    def solve(
        self,
        target_position_m: tuple[float, float, float],
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> JointCommand:
        _ = seed_joint_angles_rad
        return JointCommand(joint_angles_rad=(float(target_position_m[0]),))


@dataclass(frozen=True, slots=True)
class _FixtureMotionGenerator:
    def update(self, intent: InputIntent, dt_s: float) -> MotionCommand:
        _ = dt_s
        return MotionCommand(
            timestamp_s=intent.timestamp_s,
            joint=JointCommand(joint_angles_rad=(float(intent.target_delta_m[0]),)),
        )


@dataclass(frozen=True, slots=True)
class FixtureRobotRuntimePlugin:
    profile: RobotProfile = FIXTURE_ROBOT_PROFILE

    @property
    def profile_id(self) -> str:
        return self.profile.profile_id

    def validate_model(self, model: object) -> None:
        validate_profile_model_dimensions(self.profile, model)
        joint_name = str(getattr(model, "joint")(0).name)
        if joint_name != self.profile.canonical_joint_names[0]:
            raise ValueError("fixture robot joint order mismatch")

    def build_inverse_kinematics(self) -> _FixtureInverseKinematics:
        return _FixtureInverseKinematics()

    def build_forward_kinematics(self) -> _FixtureForwardKinematics:
        return _FixtureForwardKinematics()

    def build_target_motion_generator(
        self,
        *,
        seed_joint_angles_rad: tuple[float, ...] | None,
        discontinuity_threshold_rad: float | None,
        discontinuity_threshold_label: str,
    ) -> _FixtureMotionGenerator:
        _ = (
            seed_joint_angles_rad,
            discontinuity_threshold_rad,
            discontinuity_threshold_label,
        )
        return _FixtureMotionGenerator()

    def build_local_endpoint_motion_generator(self) -> _FixtureMotionGenerator:
        return _FixtureMotionGenerator()

    def build_qpos_feasibility_guard(
        self, *, model: object, config_path: str | Path | None
    ) -> NoOpQposFeasibilityGuard:
        _ = (model, config_path)
        return NoOpQposFeasibilityGuard()

    def build_simulator(
        self,
        *,
        model_path: str | Path | None,
        initial_keyframe_name: str | None,
    ) -> HeadlessMuJoCoSimulator:
        resolved = self.profile.mujoco_model_asset if model_path is None else Path(model_path)
        assert isinstance(resolved, Path)
        return HeadlessMuJoCoSimulator.from_model_path(
            resolved,
            initial_keyframe_name=initial_keyframe_name,
        )

    def endpoint_position_from_state(
        self, state: MuJoCoState
    ) -> tuple[float, float, float] | None:
        transform = state_transform_by_name(state.sites, "tip")
        return None if transform is None else transform.position_m

    def endpoint_orientation_from_state(
        self, state: MuJoCoState
    ) -> tuple[float, float, float, float] | None:
        transform = state_transform_by_name(state.sites, "tip")
        return None if transform is None else transform.quaternion_wxyz


FIXTURE_RUNTIME_PLUGIN = FixtureRobotRuntimePlugin()


__all__ = ["FIXTURE_RUNTIME_PLUGIN", "FixtureRobotRuntimePlugin"]
