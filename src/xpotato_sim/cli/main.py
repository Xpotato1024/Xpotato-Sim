"""Plugin-aware command-line entry for existing runtime operations."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.plugins.input_sources.catalog import (
    INPUT_SOURCE_CATALOG,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
)
from xpotato_sim.runtime.runners.dry_run import run_replay_mujoco_dry_run
from xpotato_sim.runtime.runners.websocket_publisher import (
    SUPPORTED_WEBSOCKET_PUBLISHER_PRESETS,
    run_input_source_websocket_publisher,
    run_replay_mujoco_websocket_publisher,
)
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.experiment.input_source import InputSourceMode

_RUNTIME_CAPABILITIES = (
    RESET_INITIAL_STATE_V1,
    ENDPOINT_POSE_V1,
    ENDPOINT_COMMAND_V1,
    QPOS_FEASIBILITY_V1,
)
CLI_INPUT_SOURCE_NAMES = (
    "programmed_target",
    "replay",
    "noop",
    "viewer",
)
if any(source_name not in INPUT_SOURCE_CATALOG.aliases for source_name in CLI_INPUT_SOURCE_NAMES):
    raise RuntimeError("CLI input source policy references an unknown source")
REPLAY_INPUT_SOURCE_NAMES = tuple(
    source_name
    for source_name in CLI_INPUT_SOURCE_NAMES
    if INPUT_SOURCE_CATALOG.resolve(source_name).plugin.mode
    in {InputSourceMode.OFFLINE, InputSourceMode.REPLAY}
)


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


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0.0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _port(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be in the range 1..65535")
    return parsed


def _add_robot_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--robot",
        required=True,
        help="Robot Catalog ID; no robot is selected implicitly",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xpotato-sim")
    commands = parser.add_subparsers(dest="command", required=True)

    replay = commands.add_parser("replay", help="run the deterministic MuJoCo replay")
    _add_robot_argument(replay)
    replay.add_argument("--steps", type=_positive_int, default=1)
    replay.add_argument("--dt-s", type=_positive_float, default=None)
    replay.add_argument("--preset", choices=("sweep_x",), default=None)
    replay.add_argument("--output", type=Path, default=None)
    replay.add_argument(
        "--input-source",
        choices=REPLAY_INPUT_SOURCE_NAMES,
        default=None,
    )

    viewer = commands.add_parser(
        "viewer",
        help="publish replay payloads to an existing WebSocket viewer client",
    )
    _add_robot_argument(viewer)
    viewer.add_argument("--host", default="127.0.0.1")
    viewer.add_argument("--port", type=_port, default=8766)
    viewer.add_argument("--steps", type=_positive_int, default=1)
    viewer.add_argument("--dt-s", type=_positive_float, default=1.0 / 60.0)
    viewer.add_argument("--interval-s", type=_non_negative_float, default=0.0)
    viewer.add_argument("--grace-period-s", type=_non_negative_float, default=0.05)
    viewer.add_argument(
        "--preset",
        choices=SUPPORTED_WEBSOCKET_PUBLISHER_PRESETS,
        default=None,
    )
    viewer.add_argument(
        "--input-source",
        choices=CLI_INPUT_SOURCE_NAMES,
        default=None,
    )
    profile = commands.add_parser("profile", help="起動プロファイルを検証・表示する（実行しない）")
    profile.add_argument("selector", nargs="?", help="profile名またはJSON path。省略時は一覧")
    app = commands.add_parser("app", help="profileからWebとbackendを一括起動する")
    app.add_argument("--profile", required=True)
    app.add_argument("--web-port", type=_port, default=None)
    app.add_argument("--backend-port", type=_port, default=None)
    app.add_argument("--no-browser", action="store_true")
    mode = app.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="設定と依存だけを検査（起動しない）")
    mode.add_argument("--startup-check", action="store_true", help="両serverの起動と終了だけを検証")
    trial = commands.add_parser("trial", help="明示Gamepad記録で有限試行を実行・保存する")
    trial.add_argument("--profile", required=True)
    trial.add_argument("--fixture", type=Path, required=True)
    trial.add_argument("--result-root", type=Path, required=True)
    trial.add_argument("--software-revision", required=True, help="実行sourceのrevision（未commit変更も明記）")
    trial.add_argument("--ticks", type=_positive_int, required=True)
    trial.add_argument("--input-wait-s", type=_positive_float, default=5.0)
    trial.add_argument("--wall-s", type=_positive_float, default=60.0)
    trial.add_argument("--prepare-s", type=_positive_float, default=30.0)
    workbench = commands.add_parser("workbench", help="未選択で待機する有限試行Workbench")
    workbench.add_argument("--profile", help="初期選択する登録ID（開始は別操作）")
    workbench.add_argument("--condition", type=Path, help="GUI exportと共通の展開済み条件JSON（profile/ticksと排他）")
    workbench.add_argument("--result-root", type=Path, required=True)
    workbench.add_argument("--temporary-root", type=Path, required=True)
    workbench.add_argument("--software-revision", required=True)
    workbench.add_argument("--web-port", type=_port, default=5173)
    workbench.add_argument("--backend-port", type=_port, default=8766)
    workbench.add_argument("--web-dist", type=Path, help="検証済みVite buildのroot。省略時はsource dev server")
    workbench.add_argument("--open-browser", action="store_true")
    workbench.add_argument("--startup-check", action="store_true")
    workbench.add_argument("--diagnostic-memory", action="store_true", help="明示診断時だけPython allocation追跡を有効化")
    workbench.add_argument("--run-once", action="store_true", help="明示profile/fixtureで同じserviceを有限実行する（headlessの明示Start）")
    workbench.add_argument("--control-stdin", action="store_true", help="自動検証用の一時制御資格をstdinの1行から読む（保存しない）")
    workbench.add_argument("--fixture", type=Path, help="明示software検証fixture。通常のGamepad入力とは排他")
    workbench.add_argument("--ticks", type=_positive_int, help="明示した有限検証予算。省略時は選択profileのsteps")
    workbench.add_argument("--input-wait-s", type=_positive_float, default=None, help="入力待機上限秒（既定5、conditionと排他）")
    workbench.add_argument("--wall-s", type=_positive_float, default=None, help="実時間上限秒（既定360、conditionと排他）")
    workbench.add_argument("--prepare-s", type=_positive_float, default=None, help="準備上限秒（既定30、conditionと排他）")
    return parser


def _resolve_runtime_capabilities(robot_id: str) -> None:
    bundle = resolve_robot_bundle(robot_id)
    for capability in _RUNTIME_CAPABILITIES:
        bundle.provider(capability)


def _run(args: argparse.Namespace) -> int:
    if args.command == "workbench":
        from xpotato_sim.runtime.runners.workbench import run_workbench
        return run_workbench(args)
    if args.command == "trial":
        from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
        from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
        from xpotato_sim.runtime.experiment.trial_fixture import load_trial_fixture
        from xpotato_sim.runtime.runners.finite_trial import run_finite_trial
        limits = TrialLimits(args.ticks, args.input_wait_s, args.wall_s, args.prepare_s)
        fixture = load_trial_fixture(args.fixture)
        result = run_finite_trial(load_launch_profile(args.profile), fixture=fixture, limits=limits,
            result_root=args.result_root, software_revision=args.software_revision).to_document()
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if (result["recording"] == "complete"
            and result["runner_stop_reason"] in {"simulation_budget", "task_success"}) else 1
    if args.command == "profile":
        from xpotato_sim.runtime.runners.application import (
            list_application_profiles,
            load_application_profile,
        )
        value = (
            list_application_profiles()
            if args.selector is None
            else load_application_profile(args.selector).to_dict()
        )
        print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    if args.command == "app":
        from xpotato_sim.runtime.runners.application import (
            application_url,
            load_application_profile,
            override_application_profile,
            preflight_application,
            run_application,
        )
        profile = override_application_profile(load_application_profile(args.profile), web_port=args.web_port,
            backend_port=args.backend_port, open_browser=False if args.no_browser else None)
        preflight_application(profile)
        if args.check:
            print(json.dumps({**profile.to_dict(), "viewer_url": application_url(profile)}, ensure_ascii=False, indent=2))
            return 0
        return run_application(profile, startup_check=args.startup_check)
    _resolve_runtime_capabilities(args.robot)
    if args.command == "replay":
        output = args.output if args.output is not None else sys.stdout
        if args.input_source is None:
            run_replay_mujoco_dry_run(
                steps=args.steps,
                dt_s=args.dt_s,
                output=output,
                preset=args.preset,
                robot_profile_id=args.robot,
            )
        else:
            selection = select_runtime_input_source(
                args.input_source,
                steps=args.steps,
                preset=args.preset,
            )
            run_kwargs = {
                "steps": args.steps,
                "dt_s": args.dt_s,
                "output": output,
                "robot_profile_id": args.robot,
            }
            adapter = selection.execution_adapter
            if (
                adapter is not None
                and adapter.annotates_target_position
                and not adapter.uses_viewer_endpoint_compatibility
            ):
                run_kwargs["preset"] = "sweep_x"
            else:
                run_kwargs["frames"] = selection.frames
            run_replay_mujoco_dry_run(**run_kwargs)
        return 0
    if args.command == "viewer":
        runner = (
            run_replay_mujoco_websocket_publisher
            if args.input_source is None
            else run_input_source_websocket_publisher
        )
        run_kwargs = {
            "host": args.host,
            "port": args.port,
            "steps": args.steps,
            "dt_s": args.dt_s,
            "interval_s": args.interval_s,
            "grace_period_s": args.grace_period_s,
            "preset": args.preset,
            "robot_profile_id": args.robot,
        }
        if args.input_source is not None:
            run_kwargs["input_source"] = args.input_source
        runner(**run_kwargs)
        return 0
    raise AssertionError(f"unhandled command {args.command!r}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    previous_break = None
    if args.command in {"app", "workbench"} and hasattr(signal, "SIGBREAK"):
        def interrupt(signum, frame):
            raise KeyboardInterrupt
        previous_break = signal.signal(signal.SIGBREAK, interrupt)
    try:
        return _run(args)
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"xpotato-sim: error: {exc}", file=sys.stderr)
        return 1
    finally:
        if previous_break is not None:
            signal.signal(signal.SIGBREAK, previous_break)


__all__ = ["build_parser", "main"]
