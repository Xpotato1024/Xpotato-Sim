"""Robot-independent named MuJoCo endpoint extraction."""

from __future__ import annotations

from dataclasses import dataclass

from xpotato_sim.mujoco_backend.model_contract import ResolvedModelReference
from xpotato_sim.schemas import BodyTransform, MuJoCoState, SiteTransform
from xpotato_sim.schemas.types import Vector3


def _import_mujoco() -> object:
    import mujoco

    return mujoco


def _vector3(values: object) -> Vector3:
    x, y, z = values  # type: ignore[misc]
    return (float(x), float(y), float(z))


def _missing_name_message(*, kind: str, name: str, role: str) -> str:
    return f"missing {kind} name {name!r} for expected role '{role}'"


@dataclass(frozen=True, slots=True)
class RuntimeMuJoCoEndpointEvaluation:
    """Robot-independent evaluation of a named MuJoCo site or body endpoint."""

    role: str
    kind: str
    name: str
    position_m: Vector3
    unit: str = "meter"
    coordinate_frame: str = "MuJoCo world / scene frame"


# Compatibility alias for the pre-existing public import. New code should use
# RuntimeMuJoCoEndpointEvaluation because ``kind`` supports both site and body.
RuntimeMuJoCoSiteEndpointEvaluation = RuntimeMuJoCoEndpointEvaluation


def extract_mujoco_reference_endpoint(
    model: object,
    data: object,
    *,
    reference: ResolvedModelReference,
) -> RuntimeMuJoCoEndpointEvaluation:
    mujoco = _import_mujoco()
    mujoco.mj_forward(model, data)
    if reference.kind == "site":
        object_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, reference.name)
        positions = data.site_xpos
    elif reference.kind == "body":
        object_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, reference.name)
        positions = data.xpos
    else:
        raise ValueError(f"unsupported MuJoCo endpoint reference kind: {reference.kind!r}")
    if object_id < 0:
        raise ValueError(
            _missing_name_message(kind=reference.kind, name=reference.name, role=reference.role)
        )
    return RuntimeMuJoCoEndpointEvaluation(
        role=reference.role,
        kind=reference.kind,
        name=reference.name,
        position_m=_vector3(positions[object_id]),
    )


def extract_mujoco_reference_endpoint_from_state(
    state: MuJoCoState,
    *,
    reference: ResolvedModelReference,
) -> RuntimeMuJoCoEndpointEvaluation:
    transforms: tuple[SiteTransform | BodyTransform, ...]
    if reference.kind == "site":
        transforms = state.sites
    elif reference.kind == "body":
        transforms = state.bodies
    else:
        raise ValueError(f"unsupported MuJoCo endpoint reference kind: {reference.kind!r}")
    for transform in transforms:
        if transform.name == reference.name:
            return RuntimeMuJoCoEndpointEvaluation(
                role=reference.role,
                kind=reference.kind,
                name=reference.name,
                position_m=transform.position_m,
            )
    raise ValueError(
        _missing_name_message(kind=reference.kind, name=reference.name, role=reference.role)
    )


def extract_mujoco_site_endpoint(
    model: object,
    data: object,
    *,
    site_name: str,
    role: str = "endpoint",
) -> RuntimeMuJoCoEndpointEvaluation:
    return extract_mujoco_reference_endpoint(
        model,
        data,
        reference=ResolvedModelReference(role=role, kind="site", name=site_name),
    )


def extract_mujoco_site_endpoint_from_state(
    state: MuJoCoState,
    *,
    site_name: str,
    role: str = "endpoint",
) -> RuntimeMuJoCoEndpointEvaluation:
    return extract_mujoco_reference_endpoint_from_state(
        state,
        reference=ResolvedModelReference(role=role, kind="site", name=site_name),
    )


__all__ = [
    "RuntimeMuJoCoEndpointEvaluation",
    "RuntimeMuJoCoSiteEndpointEvaluation",
    "extract_mujoco_reference_endpoint",
    "extract_mujoco_reference_endpoint_from_state",
    "extract_mujoco_site_endpoint",
    "extract_mujoco_site_endpoint_from_state",
]
