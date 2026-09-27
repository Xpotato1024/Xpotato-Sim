from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from fast_arm_core.joint_limits import (
    FAST_ARM_JOINT_LIMIT_SCHEMA_VERSION,
    FastArmJointLimit,
    FastArmJointLimitConfig,
    FastArmJointLimitViolation,
    parse_fast_arm_joint_limit_bytes,
    parse_fast_arm_joint_limit_file,
)
from fast_arm_core.definition import FAST_ARM_JOINT_NAMES

from xpotato_sim.plugins.robots.fast_arm.adapter.model_contract import validate_fast_arm_model_name_contract
from xpotato_sim.mujoco_backend.model_info import inspect_mujoco_model
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.runtime.composition.robot_resource import (
    PackageResource,
    read_package_resource_bytes,
)
from xpotato_sim.runtime.safety.qpos_feasibility import QposFeasibilityDiagnostic, QposFeasibilityResult
from xpotato_sim.schemas import JointCommand, MotionCommand

def default_fast_arm_joint_limits_path() -> Path | PackageResource:
    path = FAST_ARM_ROBOT_PROFILE.joint_limit_config_asset
    if path is None:
        raise ValueError("fast_arm profile does not declare a joint-limit config asset")
    return path


@dataclass(frozen=True, slots=True)
class FastArmQposFeasibilityResult:
    motion_command: MotionCommand
    accepted: bool
    action: str
    candidate_qpos_rad: tuple[float, ...] | None
    violations: tuple[FastArmJointLimitViolation, ...]


@dataclass(frozen=True, slots=True)
class FastArmJointLimitGuard:
    """Adapter from the fast_arm limit implementation to the generic guard contract."""

    joint_limits: FastArmJointLimitConfig

    def evaluate(
        self,
        motion_command: MotionCommand,
        *,
        current_qpos_rad: Sequence[float],
    ) -> QposFeasibilityResult:
        result = apply_fast_arm_qpos_feasibility_guard(
            motion_command,
            current_qpos_rad=current_qpos_rad,
            joint_limits=self.joint_limits,
        )
        diagnostics = tuple(
            QposFeasibilityDiagnostic.from_mapping(
                "joint_limit_violation",
                {
                    "joint_name": violation.joint_name,
                    "candidate_value_rad": _diagnostic_number(violation.candidate_value_rad),
                    "lower_rad": violation.lower_rad,
                    "upper_rad": violation.upper_rad,
                },
            )
            for violation in result.violations
        )
        return QposFeasibilityResult(
            motion_command=result.motion_command,
            accepted=result.accepted,
            action=result.action,
            candidate_qpos_rad=result.candidate_qpos_rad,
            diagnostics=diagnostics,
        )


def parse_fast_arm_joint_limit_config(
    path: str | Path | PackageResource,
) -> FastArmJointLimitConfig:
    if isinstance(path, PackageResource):
        return parse_fast_arm_joint_limit_bytes(read_package_resource_bytes(path))
    return parse_fast_arm_joint_limit_file(path)


def validate_fast_arm_joint_limit_config(
    config: FastArmJointLimitConfig,
    model: object,
) -> FastArmJointLimitConfig:
    model_joint_names = inspect_mujoco_model(model).joint_names
    if model_joint_names != config.joint_names:
        missing = tuple(name for name in config.joint_names if name not in model_joint_names)
        unknown = tuple(name for name in model_joint_names if name not in config.joint_names)
        raise ValueError(
            "fast_arm joint-limit config does not match MuJoCo model joint order: "
            f"config={config.joint_names}, model={model_joint_names}, "
            f"missing={missing}, unknown={unknown}"
        )

    validate_fast_arm_model_name_contract(model)

    mujoco = _import_mujoco()
    keyframe_id = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
    )
    if keyframe_id < 0:
        raise ValueError("canonical fast_arm home keyframe is missing: home")
    home_qpos = tuple(float(value) for value in model.key("home").qpos)
    violations = config.violations_for_qpos(home_qpos)
    if violations:
        details = ", ".join(
            f"{violation.joint_name}={violation.candidate_value_rad} not in "
            f"[{violation.lower_rad}, {violation.upper_rad}]"
            for violation in violations
        )
        raise ValueError(f"canonical fast_arm home qpos is outside configured limits: {details}")
    return config


def load_and_validate_fast_arm_joint_limit_config(
    path: str | Path | PackageResource,
    *,
    model: object,
) -> FastArmJointLimitConfig:
    return validate_fast_arm_joint_limit_config(parse_fast_arm_joint_limit_config(path), model)


def _diagnostic_number(value: float) -> float | str:
    return value if isfinite(value) else repr(value)


def apply_fast_arm_qpos_feasibility_guard(
    motion_command: MotionCommand,
    *,
    current_qpos_rad: Sequence[float],
    joint_limits: FastArmJointLimitConfig,
) -> FastArmQposFeasibilityResult:
    if motion_command.joint is None:
        return FastArmQposFeasibilityResult(
            motion_command=motion_command,
            accepted=True,
            action="accept_no_qpos_candidate",
            candidate_qpos_rad=None,
            violations=(),
        )

    candidate_qpos_rad = tuple(float(value) for value in motion_command.joint.joint_angles_rad)
    violations = joint_limits.violations_for_qpos(candidate_qpos_rad)
    if not violations:
        return FastArmQposFeasibilityResult(
            motion_command=motion_command,
            accepted=True,
            action="accept",
            candidate_qpos_rad=candidate_qpos_rad,
            violations=(),
        )

    current_qpos = tuple(float(value) for value in current_qpos_rad)
    if len(current_qpos) != len(joint_limits.joints):
        raise ValueError(
            "current qpos length does not match fast_arm joint-limit contract: "
            f"expected {len(joint_limits.joints)}, got {len(current_qpos)}"
        )
    violation_metadata = tuple(
        {
            "joint_name": violation.joint_name,
            "candidate_value_rad": _diagnostic_number(violation.candidate_value_rad),
            "lower_rad": violation.lower_rad,
            "upper_rad": violation.upper_rad,
        }
        for violation in violations
    )
    metadata = {
        **dict(motion_command.metadata),
        "qpos_feasibility_status": "rejected",
        "qpos_feasibility_action": "hold_current_qpos",
        "qpos_rejection_reason": "joint_limit_violation",
        "qpos_feasibility_rejected": True,
        "qpos_candidate_rad": tuple(_diagnostic_number(value) for value in candidate_qpos_rad),
        "qpos_limit_violations": violation_metadata,
        "qpos_limit_status": joint_limits.status,
    }
    held_command = MotionCommand(
        timestamp_s=motion_command.timestamp_s,
        target=None,
        joint=JointCommand(joint_angles_rad=current_qpos),
        metadata=metadata,
    )
    return FastArmQposFeasibilityResult(
        motion_command=held_command,
        accepted=False,
        action="hold_current_qpos",
        candidate_qpos_rad=candidate_qpos_rad,
        violations=violations,
    )


def _import_mujoco() -> object:
    import mujoco

    return mujoco


__all__ = [
    "FAST_ARM_JOINT_LIMIT_SCHEMA_VERSION",
    "FAST_ARM_JOINT_NAMES",
    "FastArmJointLimitGuard",
    "FastArmJointLimit",
    "FastArmJointLimitConfig",
    "FastArmJointLimitViolation",
    "FastArmQposFeasibilityResult",
    "apply_fast_arm_qpos_feasibility_guard",
    "default_fast_arm_joint_limits_path",
    "load_and_validate_fast_arm_joint_limit_config",
    "parse_fast_arm_joint_limit_config",
    "validate_fast_arm_joint_limit_config",
]
