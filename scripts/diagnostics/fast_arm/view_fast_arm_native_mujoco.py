from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import mujoco.viewer

from xpotato_sim.mujoco_backend import load_mujoco_model
from xpotato_sim.plugins.robots.fast_arm.profile import FAST_ARM_ROBOT_PROFILE


def _positive_index(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("key-index must be a non-negative integer")
    return parsed


def _model_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path:
        raise argparse.ArgumentTypeError("model path must not be empty")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the MuJoCo native viewer for the canonical fast_arm scene.")
    parser.add_argument(
        "--model",
        type=_model_path,
        default=FAST_ARM_ROBOT_PROFILE.mujoco_model_asset,
        help="path to the MuJoCo XML scene to load",
    )
    parser.add_argument("--key-index", type=_positive_index, default=None, help="optional keyframe index to apply")
    parser.add_argument("--key-name", type=str, default=None, help="optional keyframe name to apply")
    parser.add_argument(
        "--no-viewer",
        action="store_true",
        help="print the model information and exit without launching the GUI viewer",
    )
    return parser


def _select_keyframe(model: mujoco.MjModel, key_index: int | None, key_name: str | None) -> int | None:
    if key_index is not None and key_name is not None:
        raise ValueError("key-index and key-name are mutually exclusive")

    if key_name is not None:
        for index in range(model.nkey):
            if mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_KEY, index) == key_name:
                return index
        raise ValueError(f"unknown keyframe name: {key_name}")

    if key_index is not None:
        if key_index >= model.nkey:
            raise ValueError(f"invalid keyframe index: {key_index}")
        return key_index

    return None


def _print_model_info(model_path: Path, model: mujoco.MjModel, data: mujoco.MjData) -> None:
    print(f"model={model_path.resolve()}")
    print(
        "counts "
        f"nq={model.nq} nv={model.nv} nbody={model.nbody} "
        f"ngeom={model.ngeom} nmesh={model.nmesh} nsite={model.nsite} nkey={model.nkey}"
    )
    print(f"default_qpos={data.qpos.tolist()}")

    if model.nkey == 0:
        print("keyframes=[]")
        return

    print("keyframes=[")
    for index in range(model.nkey):
        key_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_KEY, index)
        print(f"  {{index={index}, name={key_name!r}, qpos={model.key_qpos[index].tolist()}}},")
    print("]")


def _print_transforms(model: mujoco.MjModel, data: mujoco.MjData) -> None:
    print("bodies=[")
    for index in range(model.nbody):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, index)
        print(f"  {{index={index}, name={name!r}, xpos={data.xpos[index].tolist()}, xquat={data.xquat[index].tolist()}}},")
    print("]")

    print("sites=[")
    for index in range(model.nsite):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, index)
        print(f"  {{index={index}, name={name!r}, xpos={data.site_xpos[index].tolist()}, xmat={data.site_xmat[index].tolist()}}},")
    print("]")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    bundle = load_mujoco_model(args.model)
    model = bundle.model
    data = bundle.data

    _print_model_info(bundle.model_path, model, data)

    key_index = _select_keyframe(model, args.key_index, args.key_name)
    if key_index is not None:
        data.qpos[:] = model.key_qpos[key_index]
        key_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_KEY, key_index)
        print(f"applied_keyframe=index:{key_index} name={key_name!r}")
        print(f"key_qpos={data.qpos.tolist()}")

    mujoco.mj_forward(model, data)
    _print_transforms(model, data)

    if args.no_viewer:
        return 0

    with mujoco.viewer.launch_passive(model, data) as viewer:
        print("native viewer launched. Close the viewer window to exit.")
        while viewer.is_running():
            viewer.sync()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
