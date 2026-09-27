from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from xpotato_sim.runtime.runners.live_selfrionette import (
    DEFAULT_LIVE_SELFRIONETTE_BAUD_RATE,
    DEFAULT_LIVE_SELFRIONETTE_CURRENT_TIP_POSITION_M,
    DEFAULT_LIVE_SELFRIONETTE_MAX_FRAMES,
    DEFAULT_LIVE_SELFRIONETTE_STEPS_PER_FRAME,
    LiveSelfrionetteRuntimeRunnerConfig,
    run_live_selfrionette_runtime_runner,
)


def _non_empty(value: str) -> str:
    if not value:
        raise argparse.ArgumentTypeError("port must not be empty")
    return value


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _vector3_csv(value: str) -> tuple[float, float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3 or any(not part for part in parts):
        raise argparse.ArgumentTypeError("current-tip-position-m must be x,y,z")

    try:
        vector = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("current-tip-position-m must contain numeric values") from exc

    return vector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the manual-gated live Selfrionette runtime runner.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port", type=_non_empty, default=None, help="serial port to open for live mode")
    mode.add_argument("--fixture", type=Path, default=None, help="fixture file with serial frame lines")
    parser.add_argument(
        "--baud-rate",
        type=_positive_int,
        default=DEFAULT_LIVE_SELFRIONETTE_BAUD_RATE,
        help="serial baud rate used in live mode",
    )
    parser.add_argument(
        "--max-frames",
        type=_positive_int,
        default=DEFAULT_LIVE_SELFRIONETTE_MAX_FRAMES,
        help="maximum number of payload frames to emit",
    )
    parser.add_argument(
        "--steps-per-frame",
        type=_positive_int,
        default=DEFAULT_LIVE_SELFRIONETTE_STEPS_PER_FRAME,
        help="number of offline runtime steps to run for each input frame",
    )
    parser.add_argument(
        "--current-tip-position-m",
        type=_vector3_csv,
        default=DEFAULT_LIVE_SELFRIONETTE_CURRENT_TIP_POSITION_M,
        help="comma-separated current tip position in meters",
    )
    return parser


def _print_startup_banner(*, port: str | None, baud_rate: int, max_frames: int) -> None:
    if port is None:
        print("manual gated Selfrionette fixture mode: serial is not opened")
        return

    print("manual gated live Selfrionette serial mode")
    print(f"port={port} baud_rate={baud_rate} max_frames={max_frames}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    line_source = None
    port: str | None
    if args.fixture is not None:
        port = None
        line_source = args.fixture.read_text(encoding="utf-8").splitlines()
    else:
        port = args.port

    _print_startup_banner(port=port, baud_rate=args.baud_rate, max_frames=args.max_frames)

    payloads = run_live_selfrionette_runtime_runner(
        LiveSelfrionetteRuntimeRunnerConfig(
            port=port,
            baud_rate=args.baud_rate,
            max_frames=args.max_frames,
            current_tip_position_m=args.current_tip_position_m,
            steps_per_frame=args.steps_per_frame,
        ),
        line_source=line_source,
    )

    for payload in payloads:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    print(f"frames_emitted={len(payloads)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
