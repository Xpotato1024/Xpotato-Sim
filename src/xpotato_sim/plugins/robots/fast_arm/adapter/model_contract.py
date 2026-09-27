"""fast_arm-specific MuJoCo name contract and fail-closed validators."""

from __future__ import annotations

from dataclasses import dataclass

from fast_arm_core.model_spec import (
    FAST_ARM_ARM_BODY_NAMES,
    FAST_ARM_CANONICAL_MODEL_NAME,
    FAST_ARM_END_EFFECTOR_BODY_NAME,
    FAST_ARM_END_EFFECTOR_SITE_NAME,
    FAST_ARM_REQUIRED_BODY_NAMES,
    FAST_ARM_REQUIRED_SITE_NAMES,
    FAST_ARM_MODEL_SPEC,
    FAST_ARM_TIP_BODY_NAME,
    FAST_ARM_TIP_SITE_NAME,
    FAST_ARM_WRIST_BODY_NAME,
    FAST_ARM_WRIST_SITE_NAME,
)

from xpotato_sim.mujoco_backend.model_contract import ResolvedModelReference
from xpotato_sim.mujoco_backend.model_info import MuJoCoModelInfo, inspect_mujoco_model

@dataclass(frozen=True, slots=True)
class FastArmModelNameContract:
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
    position_unit: str = FAST_ARM_MODEL_SPEC.position_unit
    coordinate_frame: str = FAST_ARM_MODEL_SPEC.coordinate_frame


def _missing_name_message(*, kind: str, names: tuple[str, ...], role: str) -> str:
    formatted_names = ", ".join(repr(name) for name in names)
    return f"missing {kind} name {formatted_names} for expected role '{role}'"


def _assert_names_present(
    *,
    info: MuJoCoModelInfo,
    required_names: tuple[str, ...],
    kind: str,
    role: str,
) -> None:
    available_names = info.site_names if kind == "site" else info.body_names
    missing_names = [name for name in required_names if name not in available_names]
    if missing_names:
        raise ValueError(
            _missing_name_message(
                kind=kind,
                names=tuple(missing_names),
                role=role,
            )
        )


def fast_arm_model_name_contract() -> FastArmModelNameContract:
    return FastArmModelNameContract()


def validate_fast_arm_model_name_contract(model: object) -> FastArmModelNameContract:
    info = inspect_mujoco_model(model)
    _assert_names_present(
        info=info,
        required_names=FAST_ARM_REQUIRED_SITE_NAMES,
        kind="site",
        role="end_effector / tip",
    )
    _assert_names_present(
        info=info,
        required_names=FAST_ARM_REQUIRED_BODY_NAMES,
        kind="body",
        role="arm / wrist / tip",
    )
    return fast_arm_model_name_contract()


def resolve_fast_arm_end_effector_reference(
    model: object,
    *,
    allow_body_fallback: bool = False,
) -> ResolvedModelReference:
    info = inspect_mujoco_model(model)
    if FAST_ARM_END_EFFECTOR_SITE_NAME in info.site_names:
        return ResolvedModelReference(
            role="end_effector",
            kind="site",
            name=FAST_ARM_END_EFFECTOR_SITE_NAME,
        )

    if allow_body_fallback:
        if FAST_ARM_END_EFFECTOR_BODY_NAME not in info.body_names:
            raise ValueError(
                _missing_name_message(
                    kind="body",
                    names=(FAST_ARM_END_EFFECTOR_BODY_NAME,),
                    role="end_effector fallback",
                )
            )
        return ResolvedModelReference(
            role="end_effector",
            kind="body",
            name=FAST_ARM_END_EFFECTOR_BODY_NAME,
        )

    raise ValueError(
        _missing_name_message(
            kind="site",
            names=(FAST_ARM_END_EFFECTOR_SITE_NAME,),
            role="end_effector",
        )
    )


def resolve_fast_arm_tip_reference(
    model: object,
    *,
    allow_body_fallback: bool = False,
) -> ResolvedModelReference:
    info = inspect_mujoco_model(model)
    if FAST_ARM_TIP_SITE_NAME in info.site_names:
        return ResolvedModelReference(
            role="tip",
            kind="site",
            name=FAST_ARM_TIP_SITE_NAME,
        )

    if allow_body_fallback:
        if FAST_ARM_TIP_BODY_NAME not in info.body_names:
            raise ValueError(
                _missing_name_message(
                    kind="body",
                    names=(FAST_ARM_TIP_BODY_NAME,),
                    role="tip fallback",
                )
            )
        return ResolvedModelReference(
            role="tip",
            kind="body",
            name=FAST_ARM_TIP_BODY_NAME,
        )

    raise ValueError(
        _missing_name_message(
            kind="site",
            names=(FAST_ARM_TIP_SITE_NAME,),
            role="tip",
        )
    )


def resolve_fast_arm_wrist_reference(model: object) -> ResolvedModelReference:
    info = inspect_mujoco_model(model)
    if FAST_ARM_WRIST_BODY_NAME not in info.body_names:
        raise ValueError(
            _missing_name_message(
                kind="body",
                names=(FAST_ARM_WRIST_BODY_NAME,),
                role="wrist",
            )
        )

    return ResolvedModelReference(role="wrist", kind="body", name=FAST_ARM_WRIST_BODY_NAME)


__all__ = [
    "FAST_ARM_ARM_BODY_NAMES",
    "FAST_ARM_CANONICAL_MODEL_NAME",
    "FAST_ARM_END_EFFECTOR_BODY_NAME",
    "FAST_ARM_END_EFFECTOR_SITE_NAME",
    "FAST_ARM_REQUIRED_BODY_NAMES",
    "FAST_ARM_REQUIRED_SITE_NAMES",
    "FAST_ARM_TIP_BODY_NAME",
    "FAST_ARM_TIP_SITE_NAME",
    "FAST_ARM_WRIST_BODY_NAME",
    "FAST_ARM_WRIST_SITE_NAME",
    "FastArmModelNameContract",
    "fast_arm_model_name_contract",
    "resolve_fast_arm_end_effector_reference",
    "resolve_fast_arm_tip_reference",
    "resolve_fast_arm_wrist_reference",
    "validate_fast_arm_model_name_contract",
]
