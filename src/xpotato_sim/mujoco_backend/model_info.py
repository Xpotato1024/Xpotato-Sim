from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MuJoCoModelInfo:
    joint_names: tuple[str, ...]
    body_names: tuple[str, ...]
    site_names: tuple[str, ...]


def _import_mujoco() -> object:
    import mujoco

    return mujoco


def _collect_names(model: object, obj_type: object, count: int) -> tuple[str, ...]:
    mujoco = _import_mujoco()
    names: list[str] = []

    for index in range(count):
        name = mujoco.mj_id2name(model, obj_type, index)
        if name:
            names.append(name)

    return tuple(names)


def inspect_mujoco_model(model: object) -> MuJoCoModelInfo:
    mujoco = _import_mujoco()
    return MuJoCoModelInfo(
        joint_names=_collect_names(model, mujoco.mjtObj.mjOBJ_JOINT, int(model.njnt)),
        body_names=_collect_names(model, mujoco.mjtObj.mjOBJ_BODY, int(model.nbody)),
        site_names=_collect_names(model, mujoco.mjtObj.mjOBJ_SITE, int(model.nsite)),
    )
