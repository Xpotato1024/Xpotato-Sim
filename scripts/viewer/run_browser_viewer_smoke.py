"""旧browser smokeのoption/profileを所有し、起動寿命は正式appへ委譲する。"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-name", "-HostName", default="127.0.0.1")
    parser.add_argument("--publisher-port", "-PublisherPort", type=int, default=8768)
    parser.add_argument("--viewer-port", "-ViewerPort", type=int, default=5176)
    parser.add_argument("--preset", "-Preset", default="sweep_x")
    parser.add_argument("--steps", "-Steps", type=int, default=6)
    parser.add_argument("--interval-s", "-IntervalS", type=float, default=0.033)
    parser.add_argument("--grace-period-s", "-GracePeriodS", type=int, default=90)
    parser.add_argument("--open-browser", "-OpenBrowser", action="store_true")
    parser.add_argument("--no-browser", "-NoBrowser", action="store_true")
    return parser


def build_profile(args: argparse.Namespace) -> dict:
    return {
        "schema_version": "xpotato-sim-launch-profile/v1",
        "name": "browser-smoke",
        "workspace": str(ROOT),
        "mode": "replay",
        "robot": {"name": "fast_arm", "version": 1},
        "input": {"plugin": {"name": "programmed_target", "version": 1},
                  "provider": None, "preset": args.preset},
        "mapping": {"plugin": {"name": "replay_mapping", "version": 1}, "parameters": {}},
        "execution": {"steps": args.steps, "dt_s": 1 / 60, "interval_s": args.interval_s,
                      "grace_period_s": args.grace_period_s},
        "web": {"host": args.host_name, "port": args.viewer_port,
                "websocket_port": args.publisher_port,
                "open_browser": args.open_browser and not args.no_browser},
    }


def run_app(arguments: list[str]) -> int:
    from xpotato_sim.cli import main as cli_main
    return cli_main(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    # profile validation、loopback制限、process cleanup、終了codeはappの既存ownerが所有する。
    with tempfile.TemporaryDirectory(prefix="selfrionette-smoke-") as directory:
        profile = Path(directory) / "profile.json"
        profile.write_text(json.dumps(build_profile(args), ensure_ascii=False), encoding="utf-8")
        forwarded = ["app", "--profile", str(profile)]
        if args.no_browser:
            forwarded.append("--startup-check")
        return run_app(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
