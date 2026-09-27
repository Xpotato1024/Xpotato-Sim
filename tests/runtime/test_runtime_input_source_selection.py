from __future__ import annotations

import inspect
import importlib
import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import xpotato_sim.runtime.runners.websocket_publisher as WEBSOCKET_RUNNER
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source


CLI = importlib.import_module("xpotato_sim.cli.main")


def test_select_runtime_input_source_reports_initial_metadata_contract() -> None:
    programmed_target = select_runtime_input_source("programmed_target", steps=2)
    replay = select_runtime_input_source("replay", steps=1)
    noop = select_runtime_input_source("noop", steps=1)
    viewer = select_runtime_input_source("viewer", steps=1)

    assert programmed_target.source_name == "programmed_target"
    assert programmed_target.loop is False
    assert programmed_target.initial_metadata["source_kind"] == "programmed_target"
    assert programmed_target.initial_metadata["trajectory_name"] == "sweep_x"

    assert replay.source_name == "replay"
    assert replay.loop is True
    assert replay.initial_metadata["preset"] == "r6-h-p5-default"

    assert noop.source_name == "noop"
    assert noop.loop is True
    assert noop.initial_metadata["source_kind"] == "noop"

    assert viewer.source_name == "viewer"
    assert viewer.loop is True
    assert viewer.frames[0].source == "viewer"
    assert viewer.initial_metadata["source_kind"] == "viewer"
    assert viewer.initial_metadata["source_active"] is False
    assert viewer.initial_metadata["stale_reason"] == "no_control_message_received"
    assert viewer.initial_metadata["desired_endpoint_m"] == (0.6, 0.0, 0.1)


def test_select_runtime_input_source_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="unsupported input source"):
        select_runtime_input_source("unknown", steps=1)


def test_dry_run_cli_default_source_remains_backward_compatible() -> None:
    stdout = io.StringIO()
    with patch.object(CLI, "run_replay_mujoco_dry_run") as run_dry_run:
        with patch.object(CLI.sys, "stdout", stdout):
            exit_code = CLI.main(["replay", "--robot", "fast_arm", "--steps", "1"])

    assert exit_code == 0
    run_dry_run.assert_called_once()
    _, kwargs = run_dry_run.call_args
    assert kwargs["preset"] is None
    assert "frames" not in kwargs


def test_dry_run_cli_programmed_target_selection_preserves_existing_path() -> None:
    with patch.object(CLI, "run_replay_mujoco_dry_run") as run_dry_run:
        exit_code = CLI.main(
            [
                "replay",
                "--robot",
                "fast_arm",
                "--steps",
                "2",
                "--input-source",
                "programmed_target",
            ]
        )

    assert exit_code == 0
    run_dry_run.assert_called_once()
    _, kwargs = run_dry_run.call_args
    assert kwargs["preset"] == "sweep_x"
    assert "frames" not in kwargs


def test_dry_run_cli_replay_selection_preserves_default_path() -> None:
    with patch.object(CLI, "run_replay_mujoco_dry_run") as run_dry_run:
        exit_code = CLI.main(
            [
                "replay",
                "--robot",
                "fast_arm",
                "--steps",
                "1",
                "--input-source",
                "replay",
            ]
        )

    assert exit_code == 0
    run_dry_run.assert_called_once()
    _, kwargs = run_dry_run.call_args
    assert kwargs["frames"] is not None
    assert tuple(kwargs["frames"])[0].metadata["preset"] == "r6-h-p5-default"
    assert "preset" not in kwargs


def test_dry_run_cli_exposes_input_source_and_forwards_it_to_runtime() -> None:
    help_text = CLI.build_parser()._subparsers._group_actions[0].choices[
        "replay"
    ].format_help()
    assert "--input-source" in help_text
    assert "programmed_target" in help_text

    stdout = io.StringIO()
    with patch.object(CLI, "run_replay_mujoco_dry_run") as run_dry_run:
        with patch.object(CLI.sys, "stdout", stdout):
            exit_code = CLI.main(
                [
                    "replay",
                    "--robot",
                    "fast_arm",
                    "--steps",
                    "1",
                    "--input-source",
                    "noop",
                ]
            )

    assert exit_code == 0
    run_dry_run.assert_called_once()
    _, kwargs = run_dry_run.call_args
    assert kwargs["frames"] is not None
    assert tuple(kwargs["frames"])[0].metadata["preset"] == "noop"
    assert "input_source" not in kwargs


def test_websocket_cli_exposes_input_source_and_forwards_it_to_runtime() -> None:
    help_text = CLI.build_parser()._subparsers._group_actions[0].choices[
        "viewer"
    ].format_help()
    assert "--input-source" in help_text
    assert "programmed_target" in help_text

    with patch.object(CLI, "run_input_source_websocket_publisher") as run_publisher:
        exit_code = CLI.main(
            [
                "viewer",
                "--robot",
                "fast_arm",
                "--host",
                "127.0.0.1",
                "--port",
                "8766",
                "--steps",
                "1",
                "--input-source",
                "replay",
            ]
        )

    assert exit_code == 0
    run_publisher.assert_called_once()
    _, kwargs = run_publisher.call_args
    assert kwargs["input_source"] == "replay"


def test_websocket_cli_input_source_no_client_path_runs_without_real_server() -> None:
    created_servers: list[object] = []

    class FakeWebSocketPublisherServer:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.bound_port = port
            self.wait_for_client_calls: list[float] = []
            created_servers.append(self)

        async def __aenter__(self) -> "FakeWebSocketPublisherServer":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def wait_for_client(self, *, timeout_s: float) -> bool:
            self.wait_for_client_calls.append(timeout_s)
            return False

    with patch.object(
        WEBSOCKET_RUNNER,
        "WebSocketPublisherServer",
        FakeWebSocketPublisherServer,
    ):
        WEBSOCKET_RUNNER.run_input_source_websocket_publisher(
            host="127.0.0.1",
            port=8766,
            steps=1,
            input_source="replay",
        )

    assert len(created_servers) == 1
    assert created_servers[0].wait_for_client_calls == [0.05]


def test_websocket_cli_viewer_source_wires_inbound_messages_into_the_same_viewer_input_source() -> None:
    created_servers: list[object] = []
    viewer_input_source = object()
    ingested_messages: list[tuple[object, str]] = []
    step_loop_calls: list[dict[str, object]] = []

    class FakeWebSocketPublisherServer:
        def __init__(self, *, host: str, port: int, on_message=None) -> None:
            self.host = host
            self.port = port
            self.bound_port = port
            self.on_message = on_message
            self.wait_for_client_calls: list[float] = []
            created_servers.append(self)

        async def __aenter__(self) -> "FakeWebSocketPublisherServer":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def wait_for_client(self, *, timeout_s: float) -> bool:
            self.wait_for_client_calls.append(timeout_s)
            assert self.on_message is not None
            result = self.on_message(
                '{"type":"viewer_control_message","timestamp_s":1.0,"source_kind":"keyboard","keyboard":{"active_key_codes":["KeyW"],"key_state":{"KeyW":true},"focus_state":"focused","zero_state":false}}'
            )
            if inspect.isawaitable(result):
                await result
            return True

    async def fake_run_runtime_input_source_step_loop(
        plan,
        *,
        steps,
        dt_s,
        interval_s,
        pacer=None,
        timing_metrics=None,
        collect_records=True,
    ):
        step_loop_calls.append(
            {
                "plan": plan,
                "steps": steps,
                "dt_s": dt_s,
                "interval_s": interval_s,
                "pacer": pacer,
                "timing_metrics": timing_metrics,
                "collect_records": collect_records,
            }
        )
        return ()

    with (
        patch.object(WEBSOCKET_RUNNER, "WebSocketPublisherServer", FakeWebSocketPublisherServer),
        patch.object(WEBSOCKET_RUNNER, "build_viewer_input_source", return_value=viewer_input_source),
        patch.object(
            WEBSOCKET_RUNNER,
            "ingest_viewer_control_message_json",
            side_effect=lambda source, message: ingested_messages.append((source, message)),
        ),
        patch.object(
            WEBSOCKET_RUNNER,
            "build_runtime_input_source_step_loop_plan",
            return_value=SimpleNamespace(selection="viewer-selection", pipeline=SimpleNamespace()),
        ) as build_plan,
        patch.object(
            WEBSOCKET_RUNNER,
            "run_runtime_input_source_step_loop",
            side_effect=fake_run_runtime_input_source_step_loop,
        ),
    ):
        WEBSOCKET_RUNNER.run_input_source_websocket_publisher(
            host="127.0.0.1",
            port=8766,
            steps=1,
            input_source="viewer",
        )

    assert len(created_servers) == 1
    assert created_servers[0].wait_for_client_calls == [0.05]
    assert ingested_messages == [
        (
            viewer_input_source,
            '{"type":"viewer_control_message","timestamp_s":1.0,"source_kind":"keyboard","keyboard":{"active_key_codes":["KeyW"],"key_state":{"KeyW":true},"focus_state":"focused","zero_state":false}}',
        )
    ]
    build_plan.assert_called_once()
    _, build_plan_kwargs = build_plan.call_args
    assert build_plan_kwargs["viewer_input_source"] is viewer_input_source
    assert len(step_loop_calls) == 1
    step_loop_call = step_loop_calls[0]
    assert step_loop_call["plan"] is build_plan.return_value
    assert step_loop_call["steps"] == 1
    assert step_loop_call["dt_s"] == 1.0 / 60.0
    assert step_loop_call["interval_s"] == 0.0
    assert step_loop_call["pacer"] is None
    assert step_loop_call["timing_metrics"] is not None
    assert step_loop_call["collect_records"] is False
