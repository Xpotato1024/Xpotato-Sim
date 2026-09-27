"""Typed behavioral boundary for robot-specific runtime composition."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from xpotato_sim.kinematics.base import ForwardKinematicsSolver, InverseKinematicsSolver
from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
from xpotato_sim.motion.base import MotionGenerator
from xpotato_sim.runtime.composition.robot_profile import RobotProfile
from xpotato_sim.runtime.composition.robot_resource import PackageResource
from xpotato_sim.runtime.safety.qpos_feasibility import QposFeasibilityGuard
from xpotato_sim.schemas import MuJoCoState


class RobotRuntimePlugin(Protocol):
    @property
    def profile_id(self) -> str: ...

    @property
    def profile(self) -> RobotProfile: ...

    def validate_model(self, model: object) -> None: ...

    def build_inverse_kinematics(self) -> InverseKinematicsSolver: ...

    def build_forward_kinematics(self) -> ForwardKinematicsSolver: ...

    def build_target_motion_generator(
        self,
        *,
        seed_joint_angles_rad: tuple[float, ...] | None,
        discontinuity_threshold_rad: float | None,
        discontinuity_threshold_label: str,
    ) -> MotionGenerator: ...

    def build_local_endpoint_motion_generator(self) -> MotionGenerator: ...

    def build_qpos_feasibility_guard(
        self,
        *,
        model: object,
        config_path: str | Path | PackageResource | None,
    ) -> QposFeasibilityGuard: ...

    def build_simulator(
        self,
        *,
        model_path: str | Path | None,
        initial_keyframe_name: str | None,
    ) -> HeadlessMuJoCoSimulator: ...

    def endpoint_position_from_state(self, state: MuJoCoState) -> tuple[float, float, float] | None: ...

    def endpoint_orientation_from_state(
        self, state: MuJoCoState
    ) -> tuple[float, float, float, float] | None: ...


def validate_profile_model_dimensions(profile: RobotProfile, model: object) -> None:
    nq = int(getattr(model, "nq"))
    nv = int(getattr(model, "nv"))
    if nq != profile.qpos_dimension:
        raise ValueError(
            f"robot profile/model qpos dimension mismatch for {profile.profile_id!r}: "
            f"expected {profile.qpos_dimension}, got {nq}"
        )
    if nv != profile.qvel_dimension:
        raise ValueError(
            f"robot profile/model qvel dimension mismatch for {profile.profile_id!r}: "
            f"expected {profile.qvel_dimension}, got {nv}"
        )


def state_transform_by_name(
    transforms: Sequence[object], name: str
) -> object | None:
    return next((transform for transform in transforms if getattr(transform, "name", None) == name), None)


__all__ = ["RobotRuntimePlugin", "validate_profile_model_dimensions"]
