from __future__ import annotations

import asyncio
import json
import socket

import pytest
from websockets.asyncio.client import connect

import xpotato_sim.runtime.runners.websocket_publisher as runner
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.transport import WebSocketPublisherServer

from test_replay_mujoco_websocket_publisher import _run_with_fake_server


@pytest.mark.parametrize("preset", [None, "sweep_x"])
def test_standalone_replay_uses_one_pipeline_step_per_frame(monkeypatch, preset) -> None:
    original = ControlMappedRuntimePipeline.run_once
    durations = []

    async def record_step(self, dt_s=None, **kwargs):
        durations.append(dt_s)
        return await original(self, dt_s=dt_s, **kwargs)

    monkeypatch.setattr(ControlMappedRuntimePipeline, "run_once", record_step)
    server = _run_with_fake_server(
        runner.run_replay_mujoco_websocket_publisher,
        client_connected=True,
        steps=24,
        dt_s=1.0 / 90.0,
        interval_s=0.0,
        grace_period_s=0.0,
        preset=preset,
    )
    payloads = [json.loads(message) for message in server.messages]
    assert durations == [1.0 / 90.0] * 24
    assert [payload["frame_index"] for payload in payloads] == list(range(1, 25))
    assert [payload["time_s"] for payload in payloads] == pytest.approx(
        [index / 90.0 for index in range(1, 25)]
    )
    assert all("input_signal" not in payload["metadata"] for payload in payloads)
    if preset is None:
        assert all(payload["target_position_m"] is None for payload in payloads)
        assert all("preset" not in payload["metadata"] for payload in payloads)
    else:
        expected_phases = (
            ["initial_hold"] * 3
            + ["move_positive_x"] * 6
            + ["slow_or_hold_at_positive_x"] * 3
            + ["return_to_initial"] * 6
            + ["final_hold"] * 6
        )
        assert [payload["metadata"]["phase"] for payload in payloads] == expected_phases
        assert [payload["metadata"]["frame_index"] for payload in payloads] == list(range(21)) + [20] * 3
        assert [payload["metadata"]["t_s"] for payload in payloads] == pytest.approx(
            [index / 30.0 for index in range(21)] + [20.0 / 30.0] * 3
        )
        for payload in payloads:
            assert payload["metadata"]["preset"] == "sweep_x"
            assert payload["metadata"]["source_kind"] == "programmed_target"
            assert payload["target_position_m"] == payload["metadata"]["desired_endpoint_m"]
            assert payload["endpoint_evaluation"]["desired_endpoint_m"] == payload["target_position_m"]


def test_sweep_projection_preserves_authoritative_profile(monkeypatch) -> None:
    original = runner._sweep_x_replay_frames

    def spoofed_frames(steps):
        return tuple(
            RawInputFrame(
                source=frame.source,
                timestamp_s=frame.timestamp_s,
                metadata={
                    **frame.metadata,
                    "robot_profile_id": "spoofed",
                    "model_contract_version": "spoofed/v9",
                    "robot_joint_names": ("wrong",),
                    "robot_qpos_dimension": 999,
                },
            )
            for frame in original(steps)
        )

    monkeypatch.setattr(runner, "_sweep_x_replay_frames", spoofed_frames)
    server = _run_with_fake_server(
        runner.run_replay_mujoco_websocket_publisher,
        client_connected=True, steps=24, preset="sweep_x", interval_s=0.0, grace_period_s=0.0,
    )
    from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE

    for message in server.messages:
        metadata = json.loads(message)["metadata"]
        assert metadata["robot_profile_id"] == "fast_arm"
        assert metadata["model_contract_version"] == FAST_ARM_ROBOT_PROFILE.model_contract_version
        assert metadata["robot_joint_names"] == list(FAST_ARM_ROBOT_PROFILE.canonical_joint_names)
        assert metadata["robot_qpos_dimension"] == 4


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.parametrize("preset", [None, "sweep_x"])
@pytest.mark.parametrize("mode", ["complete", "absent", "error", "cancel"])
def test_standalone_replay_loopback_releases_listener(monkeypatch, preset, mode) -> None:
    async def scenario() -> None:
        ready = asyncio.Event()
        sending = asyncio.Event()
        instances = []
        build_calls = []
        original_builder = runner.build_concrete_mujoco_pipeline

        class ObservedServer(WebSocketPublisherServer):
            async def start(self):
                await super().start()
                instances.append(self)
                ready.set()

            async def send(self, message):
                sending.set()
                if mode == "error":
                    raise RuntimeError("injected replay publish failure")
                if mode == "cancel":
                    await asyncio.Event().wait()
                await super().send(message)

        def record_build(*args, **kwargs):
            build_calls.append(True)
            return original_builder(*args, **kwargs)

        monkeypatch.setattr(runner, "WebSocketPublisherServer", ObservedServer)
        monkeypatch.setattr(runner, "build_concrete_mujoco_pipeline", record_build)
        port = _free_loopback_port()
        task = asyncio.create_task(runner._run_replay_mujoco_websocket_publisher_async(
            host="127.0.0.1", port=port, steps=3, dt_s=1.0 / 90.0,
            interval_s=0.0, grace_period_s=0.02 if mode == "absent" else 5.0,
            preset=preset, robot_profile_id="fast_arm",
        ))
        try:
            await asyncio.wait_for(ready.wait(), timeout=5.0)
            if mode == "absent":
                await asyncio.wait_for(task, timeout=2.0)
                assert build_calls == []
            else:
                async with connect(f"ws://127.0.0.1:{port}") as client:
                    if mode == "complete":
                        payloads = [
                            json.loads(await asyncio.wait_for(client.recv(), timeout=5.0))
                            for _ in range(3)
                        ]
                        await asyncio.wait_for(task, timeout=5.0)
                        assert [payload["frame_index"] for payload in payloads] == [1, 2, 3]
                        assert [payload["time_s"] for payload in payloads] == pytest.approx(
                            [index / 90.0 for index in (1, 2, 3)]
                        )
                    elif mode == "error":
                        with pytest.raises(RuntimeError, match="injected replay publish failure"):
                            await asyncio.wait_for(task, timeout=5.0)
                    else:
                        await asyncio.wait_for(sending.wait(), timeout=5.0)
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await asyncio.wait_for(task, timeout=5.0)
                    await asyncio.wait_for(client.wait_closed(), timeout=5.0)
                assert build_calls == [True]
            assert len(instances) == 1
            assert not instances[0].is_running
            assert instances[0].message_handler_errors == ()
            with pytest.raises(OSError):
                async with connect(f"ws://127.0.0.1:{port}", open_timeout=1.0):
                    pass
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
