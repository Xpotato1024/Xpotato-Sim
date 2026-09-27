"""fast_arm endpoint wrappers over generic named MuJoCo extraction."""

from __future__ import annotations

from xpotato_sim.mujoco_backend.endpoint_extraction import (
    RuntimeMuJoCoEndpointEvaluation,
    extract_mujoco_reference_endpoint,
    extract_mujoco_reference_endpoint_from_state,
)
from xpotato_sim.mujoco_backend.model_contract import ResolvedModelReference
from xpotato_sim.plugins.robots.fast_arm.adapter.model_contract import (
    fast_arm_model_name_contract,
    resolve_fast_arm_end_effector_reference,
    resolve_fast_arm_tip_reference,
)
from xpotato_sim.schemas import MuJoCoState
from xpotato_sim.schemas.types import Vector3


_FAST_ARM_CONTRACT = fast_arm_model_name_contract()


def _missing_name_message(*, kind: str, name: str, role: str) -> str:
    return f"missing {kind} name {name!r} for expected role '{role}'"


def _resolve_fast_arm_reference_from_state(
    state: MuJoCoState,
    *,
    role: str,
    allow_body_fallback: bool = False,
) -> ResolvedModelReference:
    if any(site.name == _FAST_ARM_CONTRACT.tip_site_name for site in state.sites):
        return ResolvedModelReference(
            role=role,
            kind="site",
            name=_FAST_ARM_CONTRACT.tip_site_name,
        )
    if allow_body_fallback:
        if any(body.name == _FAST_ARM_CONTRACT.tip_body_name for body in state.bodies):
            return ResolvedModelReference(
                role=role,
                kind="body",
                name=_FAST_ARM_CONTRACT.tip_body_name,
            )
        raise ValueError(
            _missing_name_message(
                kind="body",
                name=_FAST_ARM_CONTRACT.tip_body_name,
                role=f"{role} fallback",
            )
        )
    raise ValueError(
        _missing_name_message(
            kind="site",
            name=_FAST_ARM_CONTRACT.tip_site_name,
            role=role,
        )
    )


def extract_fast_arm_tip_site_endpoint(
    model: object,
    data: object,
    *,
    allow_body_fallback: bool = False,
) -> RuntimeMuJoCoEndpointEvaluation:
    return extract_mujoco_reference_endpoint(
        model,
        data,
        reference=resolve_fast_arm_tip_reference(
            model,
            allow_body_fallback=allow_body_fallback,
        ),
    )


def extract_fast_arm_end_effector_site_endpoint(
    model: object,
    data: object,
    *,
    allow_body_fallback: bool = False,
) -> RuntimeMuJoCoEndpointEvaluation:
    return extract_mujoco_reference_endpoint(
        model,
        data,
        reference=resolve_fast_arm_end_effector_reference(
            model,
            allow_body_fallback=allow_body_fallback,
        ),
    )


def extract_fast_arm_tip_site_endpoint_from_state(
    state: MuJoCoState,
    *,
    allow_body_fallback: bool = False,
) -> RuntimeMuJoCoEndpointEvaluation:
    return extract_mujoco_reference_endpoint_from_state(
        state,
        reference=_resolve_fast_arm_reference_from_state(
            state,
            role="tip",
            allow_body_fallback=allow_body_fallback,
        ),
    )


def extract_fast_arm_end_effector_site_endpoint_from_state(
    state: MuJoCoState,
    *,
    allow_body_fallback: bool = False,
) -> RuntimeMuJoCoEndpointEvaluation:
    return extract_mujoco_reference_endpoint_from_state(
        state,
        reference=_resolve_fast_arm_reference_from_state(
            state,
            role="end_effector",
            allow_body_fallback=allow_body_fallback,
        ),
    )


def extract_fast_arm_base_link_position_from_state(state: MuJoCoState) -> Vector3:
    for body in state.bodies:
        if body.name == "base_link":
            return body.position_m
    raise ValueError(
        _missing_name_message(kind="body", name="base_link", role="solver base")
    )


__all__ = [
    "extract_fast_arm_base_link_position_from_state",
    "extract_fast_arm_end_effector_site_endpoint",
    "extract_fast_arm_end_effector_site_endpoint_from_state",
    "extract_fast_arm_tip_site_endpoint",
    "extract_fast_arm_tip_site_endpoint_from_state",
]
