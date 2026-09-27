from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Sequence

from xpotato_sim.plugins.robots.fast_arm.adapter.resources import (
    FAST_ARM_VIEWER_FIXTURE_RESOURCE,
)
from xpotato_sim.runtime.composition.robot_resource import package_resource_traversable
from xpotato_sim.runtime.runners.dry_run import run_replay_mujoco_dry_run

FIXTURE_MODEL_PATH = "assets/mujoco/fast_arm/scene.xml"
FIXTURE_SOURCE = "python-native-mujoco"
_DEFAULT_OUTPUT_RESOURCE = package_resource_traversable(FAST_ARM_VIEWER_FIXTURE_RESOURCE)
if not isinstance(_DEFAULT_OUTPUT_RESOURCE, Path):
    raise RuntimeError("default viewer fixture package is not writable on this installation")
DEFAULT_OUTPUT_PATH = _DEFAULT_OUTPUT_RESOURCE


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("steps must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("dt-s must be positive")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export the product-owned browser qpos fixture from the native MuJoCo dry-run path."
    )
    parser.add_argument("--preset", choices=("sweep_x",), default="sweep_x", help="deterministic replay preset to export")
    parser.add_argument("--steps", type=_positive_int, default=30, help="number of frames to export")
    parser.add_argument("--dt-s", type=_positive_float, default=1.0 / 60.0, help="backend step duration in seconds")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=(
            "output JSON path; defaults to "
            "the adapter-owned fast_arm viewer fixture resource"
        ),
    )
    return parser


def _build_fixture(*, preset: str, steps: int, dt_s: float) -> dict[str, object]:
    payload_lines = run_replay_mujoco_dry_run(steps=steps, dt_s=dt_s, preset=preset)
    if not payload_lines:
        raise RuntimeError("frame sequence is empty: backend dry-run did not return any payload frames")

    frames: list[dict[str, object]] = []
    qpos_length: int | None = None
    previous_frame_index: int | None = None
    previous_time_s: float | None = None
    for sequence_position, line in enumerate(payload_lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"frame sequence position {sequence_position}: invalid JSON payload"
            ) from error
        if not isinstance(payload, dict):
            raise RuntimeError(f"frame sequence position {sequence_position}: payload must be a JSON object")

        frame_index = payload.get("frame_index")
        if isinstance(frame_index, bool) or not isinstance(frame_index, int):
            raise RuntimeError(
                f"frame sequence position {sequence_position}: frame_index must be an integer; "
                f"got {frame_index!r}"
            )
        if previous_frame_index is not None and frame_index != previous_frame_index + 1:
            raise RuntimeError(
                f"frame sequence position {sequence_position}: frame index gap or rollback; "
                f"previous/current frame_index={previous_frame_index}/{frame_index}"
            )

        time_s = payload.get("time_s")
        if isinstance(time_s, bool) or not isinstance(time_s, (int, float)):
            raise RuntimeError(
                f"frame sequence position {sequence_position}: time_s must be numeric; got {time_s!r}"
            )
        time_s = float(time_s)
        if not math.isfinite(time_s):
            raise RuntimeError(
                f"frame sequence position {sequence_position}: time_s must be finite; got {time_s!r}"
            )
        if previous_time_s is not None and time_s <= previous_time_s:
            raise RuntimeError(
                f"frame sequence position {sequence_position}: time rollback or duplicate timestamp; "
                f"previous/current time_s={previous_time_s}/{time_s}"
            )

        qpos = payload.get("qpos")
        if not isinstance(qpos, list) or not qpos:
            raise RuntimeError(
                f"frame sequence position {sequence_position}: qpos must be a non-empty list"
            )

        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in qpos):
            raise RuntimeError(
                f"frame sequence position {sequence_position}: qpos must contain numeric values"
            )
        frame_qpos = [float(value) for value in qpos]
        if not all(math.isfinite(value) for value in frame_qpos):
            raise RuntimeError(
                f"frame sequence position {sequence_position}: qpos must contain only finite numbers"
            )
        if qpos_length is None:
            qpos_length = len(frame_qpos)
        elif len(frame_qpos) != qpos_length:
            raise RuntimeError(
                f"frame sequence position {sequence_position}: qpos dimension changed; "
                f"expected/current length={qpos_length}/{len(frame_qpos)}"
            )

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise RuntimeError(
                f"frame sequence position {sequence_position}: metadata must be a JSON object"
            )

        frames.append(
            {
                "frame_index": frame_index,
                "t_s": time_s,
                "qpos": frame_qpos,
                "metadata": metadata,
            }
        )
        previous_frame_index = frame_index
        previous_time_s = time_s

    assert qpos_length is not None
    return {
        "schema_version": 1,
        "source": FIXTURE_SOURCE,
        "model_path": FIXTURE_MODEL_PATH,
        "preset": preset,
        "qpos_length": qpos_length,
        "frames": frames,
    }


def _serialize_fixture(fixture: dict[str, object]) -> str:
    return json.dumps(fixture, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _write_fixture_atomic(output_path: Path, serialized_fixture: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(serialized_fixture)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    fixture = _build_fixture(preset=args.preset, steps=args.steps, dt_s=args.dt_s)
    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_fixture_atomic(output_path, _serialize_fixture(fixture))
    print(f"Wrote qpos fixture to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
