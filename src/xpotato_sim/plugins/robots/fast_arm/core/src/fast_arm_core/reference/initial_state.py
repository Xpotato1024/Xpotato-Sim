"""Core-owned fast_arm initial-state reference shared by adapters."""

from __future__ import annotations

from dataclasses import dataclass

from fast_arm_core.definition import FAST_ARM_JOINT_NAMES


FAST_ARM_INITIAL_STATE_QPOS_RAD = (
    0.0,
    -0.5235987755982989,
    0.0,
    -1.0471975511965976,
)
FAST_ARM_INITIAL_STATE_TIP_POSITION_M = (0.240000, -0.245951, 0.284308)
FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ = (
    0.8365163037378079,
    0.1294095225512604,
    0.4829629131445341,
    0.22414386804201347,
)


@dataclass(frozen=True, slots=True)
class FastArmInitialState:
    source_id: str = "home"
    joint_names: tuple[str, ...] = FAST_ARM_JOINT_NAMES
    qpos_rad: tuple[float, ...] = FAST_ARM_INITIAL_STATE_QPOS_RAD
    tip_position_m: tuple[float, float, float] = FAST_ARM_INITIAL_STATE_TIP_POSITION_M
    tool_orientation_wxyz: tuple[float, float, float, float] = (
        FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ
    )
    qpos_unit: str = "rad"
    tip_position_unit: str = "meter"
    frame: str = "MuJoCo world / scene frame"
    quaternion_order: str = "wxyz"


FAST_ARM_INITIAL_STATE = FastArmInitialState()


__all__ = [
    "FAST_ARM_INITIAL_STATE",
    "FAST_ARM_INITIAL_STATE_QPOS_RAD",
    "FAST_ARM_INITIAL_STATE_TIP_POSITION_M",
    "FAST_ARM_INITIAL_STATE_TOOL_ORIENTATION_WXYZ",
    "FastArmInitialState",
]
