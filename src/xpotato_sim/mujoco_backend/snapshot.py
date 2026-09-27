from __future__ import annotations

from collections.abc import Mapping

from xpotato_sim.schemas import BodyTransform, MuJoCoState, SiteTransform
from xpotato_sim.schemas.types import Vector3


def _import_mujoco() -> object:
    import mujoco

    return mujoco


def _vector3(values: object) -> Vector3:
    x, y, z = values  # type: ignore[misc]
    return (float(x), float(y), float(z))


def _quaternion_wxyz(values: object) -> tuple[float, float, float, float]:
    w, x, y, z = values  # type: ignore[misc]
    return (float(w), float(x), float(y), float(z))


def _site_quaternion_wxyz(site_xmat: object) -> tuple[float, float, float, float]:
    mujoco = _import_mujoco()
    import numpy as np

    quaternion = np.zeros(4, dtype=np.float64)
    matrix = np.asarray(site_xmat, dtype=np.float64).reshape(9)
    mujoco.mju_mat2Quat(quaternion, matrix)
    return _quaternion_wxyz(quaternion)


def _collect_body_transforms(model: object, data: object) -> tuple[BodyTransform, ...]:
    mujoco = _import_mujoco()
    bodies: list[BodyTransform] = []

    for body_id in range(int(model.nbody)):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
        if not name:
            continue

        bodies.append(
            BodyTransform(
                name=name,
                position_m=_vector3(data.xpos[body_id]),
                quaternion_wxyz=_quaternion_wxyz(data.xquat[body_id]),
            )
        )

    return tuple(bodies)


def _collect_site_transforms(model: object, data: object) -> tuple[SiteTransform, ...]:
    mujoco = _import_mujoco()
    sites: list[SiteTransform] = []

    for site_id in range(int(model.nsite)):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, site_id)
        if not name:
            continue

        sites.append(
            SiteTransform(
                name=name,
                position_m=_vector3(data.site_xpos[site_id]),
                quaternion_wxyz=_site_quaternion_wxyz(data.site_xmat[site_id]),
            )
        )

    return tuple(sites)


def snapshot_mujoco_state(
    model: object,
    data: object,
    *,
    frame_index: int,
    target_position_m: Vector3 | None = None,
    metadata: Mapping[str, object] | None = None,
) -> MuJoCoState:
    mujoco = _import_mujoco()
    mujoco.mj_forward(model, data)

    return MuJoCoState(
        frame_index=frame_index,
        time_s=float(data.time),
        qpos=tuple(float(value) for value in data.qpos),
        qvel=tuple(float(value) for value in data.qvel),
        bodies=_collect_body_transforms(model, data),
        sites=_collect_site_transforms(model, data),
        target_position_m=target_position_m,
        metadata=dict(metadata) if metadata is not None else {},
    )
