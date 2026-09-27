from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ModelResourceBundle(Protocol):
    @property
    def logical_identifier(self) -> str: ...

    def model_xml_and_assets(self) -> tuple[bytes, dict[str, bytes]]: ...

@dataclass(frozen=True, slots=True)
class MuJoCoModelBundle:
    model: object
    data: object
    model_path: Path


def _import_mujoco() -> object:
    import mujoco

    return mujoco


def reset_mujoco_data_to_initial_state(
    model: object,
    data: object,
    *,
    model_path: str | Path,
    initial_keyframe_name: str | None = None,
) -> None:
    """Reset data, optionally applying an explicitly supplied named keyframe."""

    mujoco = _import_mujoco()
    if initial_keyframe_name is not None:
        keyframe_id = int(
            mujoco.mj_name2id(
                model,
                mujoco.mjtObj.mjOBJ_KEY,
                initial_keyframe_name,
            )
        )
        if keyframe_id < 0:
            raise ValueError(
                "configured initial keyframe is missing: "
                f"{initial_keyframe_name}"
            )
        mujoco.mj_resetDataKeyframe(model, data, keyframe_id)
    else:
        mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)


def load_mujoco_model(
    model_path: str | Path | ModelResourceBundle,
    *,
    initial_keyframe_name: str | None = None,
) -> MuJoCoModelBundle:
    if isinstance(model_path, ModelResourceBundle):
        model_xml, assets = model_path.model_xml_and_assets()
        return load_mujoco_model_from_xml_resources(
            model_xml,
            assets=assets,
            logical_model_path=model_path.logical_identifier,
            initial_keyframe_name=initial_keyframe_name,
        )
    path = Path(model_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    mujoco = _import_mujoco()
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    reset_mujoco_data_to_initial_state(
        model,
        data,
        model_path=path,
        initial_keyframe_name=initial_keyframe_name,
    )
    return MuJoCoModelBundle(model=model, data=data, model_path=path)


def load_mujoco_model_from_xml_resources(
    model_xml: bytes,
    *,
    assets: dict[str, bytes],
    logical_model_path: str | Path,
    initial_keyframe_name: str | None = None,
) -> MuJoCoModelBundle:
    """Load a complete in-memory MuJoCo VFS without a checkout path."""

    try:
        xml_text = model_xml.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("MuJoCo model XML resource must be UTF-8") from exc
    path = Path(logical_model_path)
    mujoco = _import_mujoco()
    model = mujoco.MjModel.from_xml_string(xml_text, assets=assets)
    data = mujoco.MjData(model)
    reset_mujoco_data_to_initial_state(
        model,
        data,
        model_path=path,
        initial_keyframe_name=initial_keyframe_name,
    )
    return MuJoCoModelBundle(model=model, data=data, model_path=path)
