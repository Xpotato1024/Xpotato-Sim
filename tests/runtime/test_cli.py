from __future__ import annotations

import importlib
import sys

import pytest

cli = importlib.import_module("xpotato_sim.cli.main")


class _BundleWithCapabilities:
    def provider(self, identity):
        return object()


class _BundleWithoutCapabilities:
    def provider(self, identity):
        raise ValueError(
            f"unsupported Robot Bundle capability {identity.canonical_id!r}; provided=()"
        )


def _input_source_choices(command: str) -> tuple[str, ...]:
    parser = cli.build_parser()
    subparsers = parser._subparsers
    assert subparsers is not None
    command_parser = subparsers._group_actions[0].choices[command]
    action = next(
        action
        for action in command_parser._actions
        if "--input-source" in action.option_strings
    )
    assert action.choices is not None
    return tuple(action.choices)


@pytest.mark.parametrize("source_name", ("programmed_target", "replay", "noop"))
def test_replay_parser_accepts_offline_and_replay_input_sources(
    source_name: str,
) -> None:
    args = cli.build_parser().parse_args(
        ["replay", "--robot", "fast_arm", "--input-source", source_name]
    )
    assert args.input_source == source_name


def test_replay_parser_rejects_viewer_with_argparse_exit_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            ["replay", "--robot", "fast_arm", "--input-source", "viewer"]
        )

    assert exc_info.value.code == 2
    error = capsys.readouterr().err
    assert "invalid choice: 'viewer'" in error
    assert "{programmed_target,replay,noop}" in error


def test_viewer_parser_accepts_all_generic_input_source_aliases() -> None:
    args = cli.build_parser().parse_args(
        ["viewer", "--robot", "fast_arm", "--input-source", "viewer"]
    )
    assert args.input_source == "viewer"
    assert _input_source_choices("viewer") == (
        "programmed_target",
        "replay",
        "noop",
        "viewer",
    )


def test_command_help_agrees_with_command_specific_parser_choices() -> None:
    parser = cli.build_parser()
    subparsers = parser._subparsers
    assert subparsers is not None
    commands = subparsers._group_actions[0].choices

    assert _input_source_choices("replay") == (
        "programmed_target",
        "replay",
        "noop",
    )
    assert "{programmed_target,replay,noop}" in commands["replay"].format_help()
    assert (
        "{programmed_target,replay,noop,viewer}"
        in commands["viewer"].format_help()
    )


def test_help_is_deterministic(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    assert "{replay,viewer,profile,app}" in capsys.readouterr().out


def test_invalid_argument_uses_argparse_exit_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["replay", "--robot", "fast_arm", "--steps", "0"])

    assert exc_info.value.code == 2
    assert "value must be a positive integer" in capsys.readouterr().err


def test_unknown_robot_returns_one_without_loading_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    diagnostic_module = "xpotato_sim.plugins.robots.fast_arm.adapter.diagnostics"
    sys.modules.pop(diagnostic_module, None)

    def reject_unknown(robot_id: str):
        raise ValueError(f"unknown Robot Plugin ID {robot_id!r}; available: ('fast_arm',)")

    monkeypatch.setattr(cli, "resolve_robot_bundle", reject_unknown)

    assert cli.main(["replay", "--robot", "unknown"]) == 1
    assert "unknown Robot Plugin ID 'unknown'" in capsys.readouterr().err
    assert diagnostic_module not in sys.modules


def test_missing_capability_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "resolve_robot_bundle", lambda robot_id: _BundleWithoutCapabilities())

    assert cli.main(["replay", "--robot", "limited"]) == 1
    assert "unsupported Robot Bundle capability" in capsys.readouterr().err


def test_replay_resolves_bundle_and_forwards_robot_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: list[str] = []
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "resolve_robot_bundle",
        lambda robot_id: (resolved.append(robot_id), _BundleWithCapabilities())[1],
    )
    monkeypatch.setattr(cli, "run_replay_mujoco_dry_run", lambda **kwargs: calls.append(kwargs))

    assert cli.main(["replay", "--robot", "selected", "--steps", "2"]) == 0
    assert resolved == ["selected"]
    assert calls[0]["robot_profile_id"] == "selected"
    assert calls[0]["steps"] == 2


def test_viewer_resolves_bundle_and_forwards_robot_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "resolve_robot_bundle",
        lambda robot_id: _BundleWithCapabilities(),
    )
    monkeypatch.setattr(
        cli,
        "run_replay_mujoco_websocket_publisher",
        lambda **kwargs: calls.append(kwargs),
    )

    assert cli.main(["viewer", "--robot", "selected", "--port", "9000"]) == 0
    assert calls[0]["robot_profile_id"] == "selected"
    assert calls[0]["port"] == 9000


def test_viewer_source_selection_uses_canonical_viewer_ingress_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "resolve_robot_bundle", lambda robot_id: _BundleWithCapabilities())
    monkeypatch.setattr(
        cli,
        "run_input_source_websocket_publisher",
        lambda **kwargs: calls.append(kwargs),
    )

    assert (
        cli.main(
            [
                "viewer",
                "--robot",
                "fast_arm",
                "--input-source",
                "viewer",
            ]
        )
        == 0
    )
    assert calls == [
        {
            "host": "127.0.0.1",
            "port": 8766,
            "steps": 1,
            "dt_s": 1.0 / 60.0,
            "interval_s": 0.0,
            "grace_period_s": 0.05,
            "preset": None,
            "robot_profile_id": "fast_arm",
            "input_source": "viewer",
        }
    ]


def test_runtime_failure_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "resolve_robot_bundle", lambda robot_id: _BundleWithCapabilities())

    def fail(**kwargs):
        raise RuntimeError("runtime failed")

    monkeypatch.setattr(cli, "run_replay_mujoco_dry_run", fail)

    assert cli.main(["replay", "--robot", "selected"]) == 1
    assert capsys.readouterr().err == "xpotato-sim: error: runtime failed\n"
