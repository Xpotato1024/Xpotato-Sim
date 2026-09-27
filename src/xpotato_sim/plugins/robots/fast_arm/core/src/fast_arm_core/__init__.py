"""Python-only shared fast_arm definition, kinematics, and resources."""

from fast_arm_core.definition import (
    FAST_ARM_DEFINITION,
    FAST_ARM_ID,
    FAST_ARM_JOINT_AXES,
    FAST_ARM_JOINT_NAMES,
    FAST_ARM_MODEL_CONTRACT_VERSION,
    FastArmDefinition,
)
from fast_arm_core.joint_limits import (
    FAST_ARM_JOINT_LIMIT_SCHEMA_VERSION,
    FastArmJointLimit,
    FastArmJointLimitConfig,
    FastArmJointLimitViolation,
    parse_fast_arm_joint_limit_bytes,
    parse_fast_arm_joint_limit_file,
)
from fast_arm_core.kinematics import (
    FAST_ARM_ENDPOINT_BASE_POSITION_M,
    FAST_ARM_ENDPOINT_JOINT_COUNT,
    FAST_ARM_ENDPOINT_LINK_LENGTHS_M,
    FastArmEndpointForwardKinematics,
    FastArmEndpointInverseKinematics,
)
from fast_arm_core.model_kinematics import (
    FAST_ARM_MUJOCO_MODEL_JOINT_REFS_RAD,
    FAST_ARM_MUJOCO_MODEL_TIP_SITE_NAME,
    FastArmModelForwardKinematics,
)
from fast_arm_core.model_spec import FAST_ARM_MODEL_SPEC, FastArmModelSpec
from fast_arm_core.reference.initial_state import (
    FAST_ARM_INITIAL_STATE,
    FAST_ARM_INITIAL_STATE_QPOS_RAD,
    FAST_ARM_INITIAL_STATE_TIP_POSITION_M,
    FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ,
    FastArmInitialState,
)

__all__ = [name for name in globals() if name.startswith("FAST_ARM_")] + [
    "FastArmDefinition",
    "FastArmEndpointForwardKinematics",
    "FastArmEndpointInverseKinematics",
    "FastArmInitialState",
    "FastArmJointLimit",
    "FastArmJointLimitConfig",
    "FastArmJointLimitViolation",
    "FastArmModelForwardKinematics",
    "FastArmModelSpec",
    "parse_fast_arm_joint_limit_bytes",
    "parse_fast_arm_joint_limit_file",
]
