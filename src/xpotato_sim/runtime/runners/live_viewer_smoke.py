from __future__ import annotations

import argparse
from dataclasses import dataclass

from xpotato_sim.runtime.runners.websocket_publisher import (
    DEFAULT_WEBSOCKET_PUBLISHER_DT_S,
    DEFAULT_WEBSOCKET_PUBLISHER_HOST,
    DEFAULT_WEBSOCKET_PUBLISHER_INTERVAL_S,
    DEFAULT_WEBSOCKET_PUBLISHER_PORT,
    DEFAULT_WEBSOCKET_PUBLISHER_STEPS,
    run_replay_mujoco_websocket_publisher,
)

DEFAULT_LIVE_VIEWER_SMOKE_GRACE_PERIOD_S = 5.0
DEFAULT_LIVE_VIEWER_SMOKE_VIEWER_PATH = "apps/mujoco-viewer/"


@dataclass(frozen=True, slots=True)
class LiveViewerSmokeConfig:
    host: str = DEFAULT_WEBSOCKET_PUBLISHER_HOST
    port: int = DEFAULT_WEBSOCKET_PUBLISHER_PORT
    steps: int = DEFAULT_WEBSOCKET_PUBLISHER_STEPS
    dt_s: float = DEFAULT_WEBSOCKET_PUBLISHER_DT_S
    interval_s: float = DEFAULT_WEBSOCKET_PUBLISHER_INTERVAL_S
    grace_period_s: float = DEFAULT_LIVE_VIEWER_SMOKE_GRACE_PERIOD_S


def build_live_viewer_smoke_endpoint(host: str, port: int) -> str:
    return f"ws://{host}:{port}"


def build_live_viewer_smoke_viewer_url(
    host: str,
    port: int,
    viewer_path: str = DEFAULT_LIVE_VIEWER_SMOKE_VIEWER_PATH,
) -> str:
    return f"{viewer_path}?websocketUrl={build_live_viewer_smoke_endpoint(host, port)}"


def build_live_viewer_smoke_report_lines(
    host: str,
    port: int,
    viewer_path: str = DEFAULT_LIVE_VIEWER_SMOKE_VIEWER_PATH,
) -> tuple[str, str]:
    return (
        f"WebSocket endpoint: {build_live_viewer_smoke_endpoint(host, port)}",
        f"Viewer URL: {build_live_viewer_smoke_viewer_url(host, port, viewer_path=viewer_path)}",
    )


def _host(value: str) -> str:
    if not value:
        raise argparse.ArgumentTypeError("host must not be empty")
    return value


def _port(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 65535:
        raise argparse.ArgumentTypeError("port must be in the range 1..65535")
    return parsed


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


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0.0:
        raise argparse.ArgumentTypeError("interval-s must be non-negative")
    return parsed


def build_live_viewer_smoke_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local smoke path from replay payload v0 to the browser viewer endpoint.",
    )
    parser.add_argument("--host", type=_host, default=DEFAULT_WEBSOCKET_PUBLISHER_HOST, help="loopback host to bind")
    parser.add_argument("--port", type=_port, default=DEFAULT_WEBSOCKET_PUBLISHER_PORT, help="TCP port to bind")
    parser.add_argument("--steps", type=_positive_int, default=3, help="number of replay steps to run")
    parser.add_argument("--dt-s", type=_positive_float, default=DEFAULT_WEBSOCKET_PUBLISHER_DT_S, help="step duration in seconds")
    parser.add_argument(
        "--interval-s",
        type=_non_negative_float,
        default=DEFAULT_WEBSOCKET_PUBLISHER_INTERVAL_S,
        help="delay between steps in seconds",
    )
    parser.add_argument(
        "--grace-period-s",
        type=_non_negative_float,
        default=DEFAULT_LIVE_VIEWER_SMOKE_GRACE_PERIOD_S,
        help="delay after server start before the first payload is published",
    )
    return parser


def run_live_viewer_smoke(
    *,
    host: str = DEFAULT_WEBSOCKET_PUBLISHER_HOST,
    port: int = DEFAULT_WEBSOCKET_PUBLISHER_PORT,
    steps: int = 3,
    dt_s: float = DEFAULT_WEBSOCKET_PUBLISHER_DT_S,
    interval_s: float = DEFAULT_WEBSOCKET_PUBLISHER_INTERVAL_S,
    grace_period_s: float = DEFAULT_LIVE_VIEWER_SMOKE_GRACE_PERIOD_S,
) -> None:
    run_replay_mujoco_websocket_publisher(
        host=host,
        port=port,
        steps=steps,
        dt_s=dt_s,
        interval_s=interval_s,
        grace_period_s=grace_period_s,
    )


__all__ = [
    "DEFAULT_LIVE_VIEWER_SMOKE_GRACE_PERIOD_S",
    "DEFAULT_LIVE_VIEWER_SMOKE_VIEWER_PATH",
    "LiveViewerSmokeConfig",
    "build_live_viewer_smoke_parser",
    "build_live_viewer_smoke_endpoint",
    "build_live_viewer_smoke_report_lines",
    "build_live_viewer_smoke_viewer_url",
    "run_live_viewer_smoke",
]
