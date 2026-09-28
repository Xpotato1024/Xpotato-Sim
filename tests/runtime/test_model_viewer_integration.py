"""実WebSocketで双腕入力・中立切替・stale時の全体停止を検証する。実機I/Oなし。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import socket
from time import monotonic

import pytest
from websockets.asyncio.client import connect

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.runners.model_websocket_publisher import _run_model_websocket_publisher_async
from tests.runtime.test_model_websocket_publisher import message


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def _until(ws, predicate):
    async with asyncio.timeout(4):
        while True:
            value = json.loads(await ws.recv())
            if predicate(value):
                return value


def _sample(raw, seq, held=(), triggers=(0.0, 0.0)):
    return message(raw, seq, triggers=triggers, held=held)


def test_live_websocket_both_sticks_trigger_z_and_fault_latch():
    async def scenario():
        profile = replace(load_launch_profile("fast-arm-bimanual-gamepad"),
                          backend_port=_free_port(), steps=1000, interval_s=.01,
                          grace_period_s=2.)
        ready = asyncio.Event()
        task = asyncio.create_task(_run_model_websocket_publisher_async(
            profile, clock=monotonic, on_ready=ready.set))
        try:
            await asyncio.wait_for(ready.wait(), 4)
            async with connect(f"ws://{profile.host}:{profile.backend_port}", proxy=None) as ws:
                first = json.loads(await ws.recv())
                assert len(first["qpos"]) == 8
                assert first["metadata"]["physical_output"] == "disabled"
                assert first["metadata"]["gamepad_trigger_control_v1"]["output_scope"] == "coordinated"
                assert all(s["status"] == "waiting_trigger_neutral" for s in first["metadata"]["gamepad_trigger_control_v1"]["sides"].values())
                await ws.send(_sample((0.,0.,0.,0.), 0))
                neutral = await _until(ws, lambda p: p["metadata"]["motion_status"] == "running")
                assert neutral["qpos"] == first["qpos"]
                await ws.send(_sample((.55,0.,-.55,0.), 1))
                moved = await _until(ws, lambda p: p["qpos"][:4] != first["qpos"][:4] and p["qpos"][4:] != first["qpos"][4:])
                assert moved["metadata"]["robot_joint_names"] == list(profile.model_registration().joint_names)
                assert moved["metadata"]["coordinated_runtime_v1"]["tick"] > 0
                await ws.send(_sample((.55,0.,0.,0.), 2, (5,)))
                signed = await _until(ws, lambda p: p["metadata"]["gamepad_trigger_control_v1"]["sides"]["right"]["z_sign"] == -1)
                assert signed["metadata"]["gamepad_trigger_control_v1"]["sides"]["left"]["velocity_m_s"][0] > 0
                await ws.send(_sample((.55,0.,0.,0.), 3, triggers=(0., .55)))
                vertical = await _until(ws, lambda p: p["metadata"]["gamepad_trigger_control_v1"]["sides"]["right"]["velocity_m_s"][2] < 0)
                assert vertical["metadata"]["gamepad_trigger_control_v1"]["sides"]["right"]["trigger_value"] == pytest.approx(.55)
                fault = await _until(ws, lambda p: p["metadata"]["motion_status"] == "faulted")
                assert "stale" in fault["metadata"]["motion_rejection_reason"]
                assert not fault["metadata"]["source_active"]
                await ws.send(_sample((0.,0.,0.,0.), 4))
                later = json.loads(await ws.recv())
                assert later["metadata"]["motion_status"] == "faulted"
                assert later["qpos"] == fault["qpos"]
                assert later["time_s"] == fault["time_s"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("bad", ["not-json", '{"type":"unknown"}', message((0.,0.,0.,0.),0).replace('gamepad/v1','keyboard/v1')])
def test_invalid_initial_input_preserves_fault_reason_and_home(bad):
    async def scenario():
        profile = replace(load_launch_profile("fast-arm-bimanual-gamepad"),
                          backend_port=_free_port(), steps=40, interval_s=.01, grace_period_s=2.)
        ready = asyncio.Event()
        task = asyncio.create_task(_run_model_websocket_publisher_async(
            profile, clock=monotonic, on_ready=ready.set))
        try:
            await asyncio.wait_for(ready.wait(), 4)
            async with connect(f"ws://{profile.host}:{profile.backend_port}", proxy=None) as ws:
                first = json.loads(await ws.recv())
                await ws.send(bad)
                fault = await _until(ws, lambda p: p["metadata"]["motion_status"] == "faulted")
                assert "source_ingress_failed" in fault["metadata"]["motion_rejection_reason"]
                assert fault["qpos"] == first["qpos"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
