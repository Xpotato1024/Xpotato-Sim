from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path
from unittest.mock import patch

import xpotato_sim.runtime.runners.websocket_publisher as websocket_runner_module
from xpotato_sim.runtime.runners.websocket_publisher import run_replay_mujoco_websocket_publisher
from xpotato_sim.runtime.runners.live_websocket_delivery import LiveLatestStateWebSocketPublisher


ROOT = Path(__file__).resolve().parents[2]
WEBSOCKET_RUNNER_MODULE = ROOT / "src" / "xpotato_sim" / "runtime" / "runners" / "websocket_publisher.py"


class _FakeWebSocketPublisherServer:
    instances: list["_FakeWebSocketPublisherServer"] = []

    def __init__(self, *, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.bound_port = port
        self.wait_for_client_calls: list[float | None] = []
        self.messages: list[str] = []
        self.__class__.instances.append(self)

    async def __aenter__(self) -> "_FakeWebSocketPublisherServer":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def wait_for_client(self, timeout_s: float | None = None) -> bool:
        self.wait_for_client_calls.append(timeout_s)
        return True

    async def send(self, message: str) -> None:
        self.messages.append(message)


def _collect_payloads(steps: int, *, preset: str | None = None) -> list[dict[str, object]]:
    _FakeWebSocketPublisherServer.instances.clear()
    original_server = websocket_runner_module.WebSocketPublisherServer
    websocket_runner_module.WebSocketPublisherServer = _FakeWebSocketPublisherServer
    try:
        run_replay_mujoco_websocket_publisher(
            host="127.0.0.1",
            port=8766,
            steps=steps,
            dt_s=1.0 / 60.0,
            interval_s=0.0,
            grace_period_s=0.0,
            preset=preset,
        )
    finally:
        websocket_runner_module.WebSocketPublisherServer = original_server

    assert _FakeWebSocketPublisherServer.instances, "fake server was not constructed"
    return [json.loads(message) for message in _FakeWebSocketPublisherServer.instances[-1].messages]


def _assert_endpoint_evaluation(payload: dict[str, object]) -> None:
    endpoint_evaluation = payload["endpoint_evaluation"]
    assert isinstance(endpoint_evaluation, dict)
    assert endpoint_evaluation["unit"] == "meter"
    assert endpoint_evaluation["desired_endpoint_coordinate_frame"] == "command-side endpoint frame"
    assert endpoint_evaluation["fk_endpoint_coordinate_frame"] == "solver-defined frame"
    assert endpoint_evaluation["site_endpoint_coordinate_frame"] == "MuJoCo world / scene frame"


def test_websocket_publisher_runner_sweep_x_uses_programmed_input_source_metadata() -> None:
    payloads = _collect_payloads(2, preset="sweep_x")

    assert len(payloads) == 2
    assert [payload["metadata"]["source_kind"] for payload in payloads] == ["programmed_target", "programmed_target"]
    assert [payload["metadata"]["trajectory_name"] for payload in payloads] == ["sweep_x", "sweep_x"]
    assert [payload["metadata"]["phase"] for payload in payloads] == ["initial_hold", "initial_hold"]
    assert [payload["metadata"]["preset"] for payload in payloads] == ["sweep_x", "sweep_x"]
    assert payloads[0]["metadata"]["desired_endpoint_m"] == payloads[0]["target_position_m"]
    _assert_endpoint_evaluation(payloads[0])


def test_websocket_runner_module_uses_programmed_input_source_and_not_noop_motion_generator() -> None:
    source_text = WEBSOCKET_RUNNER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source_text, filename=str(WEBSOCKET_RUNNER_MODULE))

    assert "build_sweep_x_input_source" in source_text
    assert "NoOpMotionGenerator" not in source_text
    assert "SUPPORTED_WEBSOCKET_PUBLISHER_PRESETS" in source_text

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "xpotato_sim.plugins.input_sources.programmed_target"
        ):
            imported_names.update(alias.name for alias in node.names)

    assert "build_sweep_x_input_source" in imported_names


def test_websocket_runner_programmed_target_uses_runtime_step_loop_helper() -> None:
    created_servers: list[object] = []

    class FakeWebSocketPublisherServer:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.bound_port = port
            self.wait_for_client_calls: list[float | None] = []
            self.messages: list[str] = []
            created_servers.append(self)

        async def __aenter__(self) -> "FakeWebSocketPublisherServer":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def wait_for_client(self, timeout_s: float | None = None) -> bool:
            self.wait_for_client_calls.append(timeout_s)
            return True

        async def send(self, message: str) -> None:
            self.messages.append(message)

    with patch.object(websocket_runner_module, "WebSocketPublisherServer", FakeWebSocketPublisherServer):
        asyncio.run(
            websocket_runner_module._run_input_source_websocket_publisher_async(
                host="127.0.0.1",
                port=8766,
                steps=2,
                dt_s=1.0 / 60.0,
                interval_s=0.0,
                grace_period_s=0.0,
                preset="sweep_x",
                input_source="programmed_target",
                robot_profile_id="fast_arm",
            )
        )

    assert len(created_servers) == 1
    payloads = [json.loads(message) for message in created_servers[0].messages]
    assert len(payloads) == 2
    assert [payload["frame_index"] for payload in payloads] == [1, 2]
    assert [payload["metadata"]["source_kind"] for payload in payloads] == ["programmed_target", "programmed_target"]
    assert payloads[0]["metadata"]["desired_endpoint_m"] == payloads[0]["target_position_m"]
    assert payloads[0]["endpoint_evaluation"]["desired_endpoint_m"] == payloads[0]["target_position_m"]


def test_websocket_runner_viewer_path_uses_live_latest_delivery_and_interval_zero_compatibility() -> None:
    created_servers: list[object] = []

    class FakeWebSocketPublisherServer:
        def __init__(self, *, host: str, port: int, on_message=None) -> None:
            self.host = host
            self.port = port
            self.bound_port = port
            self.on_message = on_message
            self.messages: list[str] = []
            created_servers.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def wait_for_client(self, timeout_s: float | None = None) -> bool:
            return True

        async def send(self, message: str) -> None:
            self.messages.append(message)

    with patch.object(websocket_runner_module, "WebSocketPublisherServer", FakeWebSocketPublisherServer):
        asyncio.run(
            websocket_runner_module._run_input_source_websocket_publisher_async(
                host="127.0.0.1",
                port=8766,
                steps=2,
                dt_s=1.0 / 60.0,
                interval_s=0.0,
                grace_period_s=0.0,
                preset=None,
                input_source="viewer",
                robot_profile_id="fast_arm",
            )
        )

    assert len(created_servers) == 1
    payloads = [json.loads(message) for message in created_servers[0].messages]
    assert payloads[-1]["frame_index"] == 2
    assert payloads[-1]["version"] == 0


def test_websocket_runner_viewer_path_has_bounded_final_flush(capsys) -> None:
    never_released = asyncio.Event()

    class BlockedWebSocketPublisherServer:
        def __init__(self, *, host: str, port: int, on_message=None) -> None:
            self.host = host
            self.port = port
            self.bound_port = port
            self.on_message = on_message

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def wait_for_client(self, timeout_s: float | None = None) -> bool:
            return True

        async def send(self, message: str) -> None:
            await never_released.wait()

    class ShortTimeoutPublisher(LiveLatestStateWebSocketPublisher):
        async def drain(self, *, timeout_s: float | None = 0.01) -> bool:
            return await super().drain(timeout_s=timeout_s)

        async def close(self, *, flush_timeout_s: float | None = 0.01) -> bool:
            return await super().close(flush_timeout_s=flush_timeout_s)

    with (
        patch.object(websocket_runner_module, "WebSocketPublisherServer", BlockedWebSocketPublisherServer),
        patch.object(websocket_runner_module, "LiveLatestStateWebSocketPublisher", ShortTimeoutPublisher),
    ):
        asyncio.run(
            websocket_runner_module._run_input_source_websocket_publisher_async(
                host="127.0.0.1",
                port=8766,
                steps=1,
                dt_s=1.0 / 60.0,
                interval_s=0.0,
                grace_period_s=0.0,
                preset=None,
                input_source="viewer",
                robot_profile_id="fast_arm",
            )
        )

    output = capsys.readouterr().out
    assert '"shutdown_timeout_count": 1' in output
    assert '"shutdown_dropped_frame_count": 1' in output
    assert "Completed after publishing 1 frame(s)." in output
