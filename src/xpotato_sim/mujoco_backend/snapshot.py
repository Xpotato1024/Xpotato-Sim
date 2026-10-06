from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from xpotato_sim.schemas import BodyTransform, MuJoCoState, SiteTransform
from xpotato_sim.schemas.types import Vector3


def _copy_metadata_value(value: object) -> object:
    """JSON用containerを切り離し、tupleの既存schema形状も維持する。"""
    if isinstance(value, Mapping):
        return {key: _copy_metadata_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_metadata_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_copy_metadata_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class _SnapshotLayout:
    """model lifetimeの名前bindingのみ保持し、pose/timeは保持しない。"""
    model: object
    bodies: tuple[tuple[int, str], ...]
    sites: tuple[tuple[int, str], ...]

    @classmethod
    def prepare(cls, model: object) -> _SnapshotLayout:
        mujoco = _import_mujoco()
        def named(kind, count):
            return tuple((i, name) for i in range(count)
                         if (name := mujoco.mj_id2name(model, kind, i)))
        return cls(model, named(mujoco.mjtObj.mjOBJ_BODY, int(model.nbody)),
                   named(mujoco.mjtObj.mjOBJ_SITE, int(model.nsite)))


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

    return _read_synchronized_mujoco_state(model, data, frame_index=frame_index,
        target_position_m=target_position_m, metadata=metadata)


def _read_synchronized_mujoco_state(
    model: object, data: object, *, frame_index: int,
    target_position_m: Vector3 | None = None,
    metadata: Mapping[str, object] | None = None,
    layout: _SnapshotLayout | None = None,
) -> MuJoCoState:
    """reset/commitでforward済みのprovider専用読取り。native stateを変更しない。"""
    if layout is not None and layout.model is not model:
        raise ValueError("snapshot layout/model mismatch")
    bodies = (_collect_body_transforms(model, data) if layout is None else tuple(
        BodyTransform(name, _vector3(data.xpos[i]), _quaternion_wxyz(data.xquat[i]))
        for i, name in layout.bodies))
    sites = (_collect_site_transforms(model, data) if layout is None else tuple(
        SiteTransform(name, _vector3(data.site_xpos[i]), _site_quaternion_wxyz(data.site_xmat[i]))
        for i, name in layout.sites))
    return MuJoCoState(
        frame_index=frame_index,
        time_s=float(data.time),
        qpos=tuple(float(value) for value in data.qpos),
        qvel=tuple(float(value) for value in data.qvel),
        bodies=bodies,
        sites=sites,
        target_position_m=target_position_m,
        metadata={key: _copy_metadata_value(value) for key, value in metadata.items()} if metadata is not None else {},
    )
