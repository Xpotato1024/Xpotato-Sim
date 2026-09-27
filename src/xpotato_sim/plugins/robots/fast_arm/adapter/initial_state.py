"""Selfrionette projection of the core-owned fast_arm initial-state reference."""

from __future__ import annotations

from fast_arm_core.reference.initial_state import (
    FAST_ARM_INITIAL_STATE,
    FAST_ARM_INITIAL_STATE_QPOS_RAD,
    FAST_ARM_INITIAL_STATE_TIP_POSITION_M,
    FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ,
)
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from xpotato_sim.runtime.composition.robot_bundle import InitialStateContract

FAST_ARM_INITIAL_STATE_CONTRACT = InitialStateContract(
    identity=VersionedIdentity("fast_arm_initial_state", 1),
    source_kind="named_keyframe",
    source_id=FAST_ARM_INITIAL_STATE.source_id,
    qpos_rad=FAST_ARM_INITIAL_STATE_QPOS_RAD,
    tip_position_m=FAST_ARM_INITIAL_STATE_TIP_POSITION_M,
    tool_orientation_wxyz=FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ,
    frame=FAST_ARM_INITIAL_STATE.frame,
    position_unit=FAST_ARM_INITIAL_STATE.tip_position_unit,
    orientation_unit="unit_quaternion",
    quaternion_order=FAST_ARM_INITIAL_STATE.quaternion_order,
)


__all__ = [
    "FAST_ARM_INITIAL_STATE_CONTRACT",
    "FAST_ARM_INITIAL_STATE_QPOS_RAD",
    "FAST_ARM_INITIAL_STATE_TIP_POSITION_M",
    "FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ",
]
