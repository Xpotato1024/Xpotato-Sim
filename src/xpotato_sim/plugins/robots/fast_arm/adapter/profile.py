"""Declarative fast_arm Robot Profile implementation."""

from __future__ import annotations

from fast_arm_core.definition import (
    FAST_ARM_DEFINITION,
    FAST_ARM_ID,
    FAST_ARM_JOINT_NAMES,
    FAST_ARM_MODEL_CONTRACT_VERSION,
)
from fast_arm_core.model_spec import FAST_ARM_MODEL_SPEC
from fast_arm_core.reference.initial_state import FAST_ARM_INITIAL_STATE

from xpotato_sim.plugins.robots.fast_arm.adapter.model_contract import (
    FAST_ARM_END_EFFECTOR_BODY_NAME,
    FAST_ARM_END_EFFECTOR_SITE_NAME,
)
from xpotato_sim.runtime.composition.robot_profile import (
    CoordinateUnitContract,
    EndpointReference,
    RobotProfile,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.viewer import (
    FAST_ARM_VIEWER_DECLARATION,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.resources import (
    FAST_ARM_JOINT_LIMIT_RESOURCE,
    FAST_ARM_MODEL_BUNDLE,
    FAST_ARM_VIEWER_DECLARATION_RESOURCE,
)
from xpotato_sim.runtime.composition.viewer_robot_declaration import (
    repository_resource_public_url,
)

FAST_ARM_PROFILE_ID = FAST_ARM_ID

FAST_ARM_ROBOT_PROFILE = RobotProfile(
    profile_id=FAST_ARM_PROFILE_ID,
    profile_contract_version=1,
    model_contract_version=FAST_ARM_MODEL_CONTRACT_VERSION,
    backend_kind="mujoco",
    mujoco_model_asset=FAST_ARM_MODEL_BUNDLE,
    canonical_joint_names=FAST_ARM_JOINT_NAMES,
    qpos_dimension=FAST_ARM_DEFINITION.qpos_dimension,
    qvel_dimension=FAST_ARM_DEFINITION.qvel_dimension,
    initial_keyframe_name=FAST_ARM_INITIAL_STATE.source_id,
    endpoint=EndpointReference(
        site_name=FAST_ARM_END_EFFECTOR_SITE_NAME,
        body_name=FAST_ARM_END_EFFECTOR_BODY_NAME,
    ),
    joint_limit_config_asset=FAST_ARM_JOINT_LIMIT_RESOURCE,
    coordinate_units=CoordinateUnitContract(
        position_unit=FAST_ARM_MODEL_SPEC.position_unit,
        angle_unit=FAST_ARM_DEFINITION.joint_angle_unit,
        coordinate_frame=FAST_ARM_MODEL_SPEC.coordinate_frame,
        quaternion_order=FAST_ARM_INITIAL_STATE.quaternion_order,
    ),
    viewer_profile_id=FAST_ARM_PROFILE_ID,
    supported_capabilities=frozenset(
        {
            "endpoint_ik",
            "physical_fk",
            "local_endpoint_motion",
            "qpos_feasibility_guard",
            "viewer_qpos_rendering",
        }
    ),
    viewer_declaration=FAST_ARM_VIEWER_DECLARATION,
    viewer_declaration_resource_path=(
        FAST_ARM_VIEWER_DECLARATION_RESOURCE.logical_identifier
    ),
    viewer_declaration_url=repository_resource_public_url(
        FAST_ARM_VIEWER_DECLARATION_RESOURCE.logical_identifier
    ),
)


__all__ = [
    "FAST_ARM_JOINT_NAMES",
    "FAST_ARM_MODEL_CONTRACT_VERSION",
    "FAST_ARM_PROFILE_ID",
    "FAST_ARM_ROBOT_PROFILE",
]
