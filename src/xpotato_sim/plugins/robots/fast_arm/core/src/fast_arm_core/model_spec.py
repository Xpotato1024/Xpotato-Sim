"""Pure fast_arm MuJoCo model-name and resource specification."""

from __future__ import annotations

from dataclasses import dataclass


FAST_ARM_CANONICAL_MODEL_NAME = "fast_arm"
FAST_ARM_END_EFFECTOR_SITE_NAME = "tip"
FAST_ARM_END_EFFECTOR_BODY_NAME = "fore_arm_link"
FAST_ARM_WRIST_SITE_NAME: str | None = None
FAST_ARM_WRIST_BODY_NAME = "fore_arm_link"
FAST_ARM_TIP_SITE_NAME = "tip"
FAST_ARM_TIP_BODY_NAME = "fore_arm_link"
FAST_ARM_ARM_BODY_NAMES: tuple[str, ...] = (
    "base_link",
    "sholder_link_1",
    "sholder_link_2",
    "upper_arm_link",
    "fore_arm_link",
)
FAST_ARM_REQUIRED_SITE_NAMES = (FAST_ARM_END_EFFECTOR_SITE_NAME,)
FAST_ARM_REQUIRED_BODY_NAMES = FAST_ARM_ARM_BODY_NAMES


@dataclass(frozen=True, slots=True)
class FastArmModelSpec:
    canonical_model_name: str = FAST_ARM_CANONICAL_MODEL_NAME
    end_effector_site_name: str = FAST_ARM_END_EFFECTOR_SITE_NAME
    end_effector_body_name: str = FAST_ARM_END_EFFECTOR_BODY_NAME
    wrist_site_name: str | None = FAST_ARM_WRIST_SITE_NAME
    wrist_body_name: str = FAST_ARM_WRIST_BODY_NAME
    tip_site_name: str = FAST_ARM_TIP_SITE_NAME
    tip_body_name: str = FAST_ARM_TIP_BODY_NAME
    arm_body_names: tuple[str, ...] = FAST_ARM_ARM_BODY_NAMES
    required_site_names: tuple[str, ...] = FAST_ARM_REQUIRED_SITE_NAMES
    required_body_names: tuple[str, ...] = FAST_ARM_REQUIRED_BODY_NAMES
    position_unit: str = "meter"
    coordinate_frame: str = "MuJoCo world / scene frame"


FAST_ARM_MODEL_SPEC = FastArmModelSpec()


__all__ = [name for name in globals() if name.startswith("FAST_ARM_")] + ["FastArmModelSpec"]
