"""双腕Gamepad入力を同一WebSocket tick系列の8関節stateへ接続する。"""
from __future__ import annotations

from dataclasses import replace
import json
import pytest

import xpotato_sim.runtime.runners.model_websocket_publisher as module
from xpotato_sim.runtime.composition.launch_profile import (
    load_launch_profile,
)
from xpotato_sim.runtime.runners.model_websocket_publisher import (
    run_model_websocket_publisher,
)


def message(raw, sequence, *, triggers=(0.0, 0.0), held=(), connected=True, stale=False):
    buttons = [{"pressed": False, "value": 0.0} for _ in range(8)]
    buttons[6] = {"pressed": triggers[0] > 0.5, "value": triggers[0]}
    buttons[7] = {"pressed": triggers[1] > 0.5, "value": triggers[1]}
    for index in held:
        buttons[index] = {"pressed": True, "value": max(1.0, buttons[index]["value"])}
    zero = not any(abs(value) > .1 for value in raw) and not any(v > .1 for v in triggers) and not held
    return json.dumps({
        "type": "viewer_control_message",
        "timestamp_s": sequence / 60,
        "source_kind": "gamepad",
        "sequence": sequence,
        "provider_id": "gamepad/v1",
        "provider_schema": "viewer_gamepad_sample/v1",
        "gamepad": {
            "index": 0,
            "id": "test-pad",
            "connected": connected,
            "raw_axes": list(raw),
            "axes": list(raw),
            "buttons": buttons,
            "stale": stale,
            "zero_state": zero,
        },
        "metadata": {"viewer_provider_session_id": "test-stream"},
    })


class FakeServer:
    instances = []
    incoming = []

    def __init__(self, *, host, port, on_message=None):
        self.host, self.port, self.bound_port = host, port, port
        self.on_message = on_message
        self.messages = []
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def wait_for_client(self, timeout_s=None):
        return True

    async def send(self, value):
        self.messages.append(value)
        if self.incoming and self.on_message is not None:
            self.on_message(self.incoming.pop(0))


def test_both_sticks_update_eight_joint_payload_in_one_runtime_sequence(monkeypatch):
    profile = replace(
        load_launch_profile("fast-arm-bimanual-gamepad"),
        steps=4,
        interval_s=.0001,
        grace_period_s=.0001,
    )
    FakeServer.instances.clear()
    FakeServer.incoming = [
        message((0., 0., 0., 0.), 0),
        message((.55, 0., -.55, 0.), 1),
    ]
    monkeypatch.setattr(module, "WebSocketPublisherServer", FakeServer)
    ready = []
    run_model_websocket_publisher(
        profile, clock=lambda: 1.0, on_ready=lambda: ready.append(True)
    )

    server = FakeServer.instances[-1]
    payloads = [json.loads(value) for value in server.messages]
    assert ready == [True]
    assert len(payloads) >= 3
    assert all(len(item["qpos"]) == 8 for item in payloads)
    metadata = payloads[-1]["metadata"]
    assert metadata["robot_profile_id"] == "fast_arm_bimanual"
    assert metadata["robot_qpos_dimension"] == 8
    assert len(metadata["robot_joint_names"]) == 8
    assert metadata["physical_output"] == "disabled"
    assert metadata["gamepad_trigger_control_v1"]["output_scope"] == "coordinated"
    assert metadata["gamepad_trigger_control_v1"]["output_side"] is None
    assert metadata["coordinated_runtime_v1"]["arm_ids"] == ["left", "right"]
    initial, moved = payloads[0]["qpos"], payloads[-1]["qpos"]
    assert any(a != b for a, b in zip(initial[:4], moved[:4]))
    assert any(a != b for a, b in zip(initial[4:], moved[4:]))


def test_no_gamepad_keeps_home_without_faulting(monkeypatch):
    profile = replace(
        load_launch_profile("fast-arm-bimanual-gamepad"),
        steps=2,
        interval_s=.0001,
        grace_period_s=.0001,
    )
    FakeServer.instances.clear()
    FakeServer.incoming = []
    monkeypatch.setattr(module, "WebSocketPublisherServer", FakeServer)
    run_model_websocket_publisher(profile, clock=lambda: 1.0)
    payloads = [json.loads(value) for value in FakeServer.instances[-1].messages]
    assert len(payloads) == 2
    assert payloads[0]["qpos"] == payloads[1]["qpos"]
    assert payloads[-1]["metadata"]["motion_status"] == "waiting_neutral"
    assert payloads[-1]["metadata"]["motion_rejection_reason"] == "awaiting_gamepad_input"


def test_initial_disconnected_sample_waits_for_fresh_neutral_without_fault(monkeypatch):
    profile = replace(
        load_launch_profile("fast-arm-bimanual-gamepad"),
        steps=5,
        interval_s=.0001,
        grace_period_s=.0001,
    )
    FakeServer.instances.clear()
    FakeServer.incoming = [
        message((0., 0., 0., 0.), 0, connected=False),
        message((0., 0., 0., 0.), 1),
        message((.55, 0., -.55, 0.), 2),
    ]
    monkeypatch.setattr(module, "WebSocketPublisherServer", FakeServer)
    run_model_websocket_publisher(profile, clock=lambda: 1.0)
    payloads = [json.loads(value) for value in FakeServer.instances[-1].messages]
    assert payloads
    waiting = [p for p in payloads if p["metadata"]["motion_status"] == "waiting_neutral"]
    assert waiting, "initial disconnected sample must remain a recoverable waiting state"
    assert all(p["metadata"]["motion_status"] != "faulted" for p in payloads[:3])
    assert any(p["metadata"]["motion_status"] == "running" for p in payloads)
    assert payloads[-1]["qpos"] != payloads[0]["qpos"]


@pytest.mark.parametrize("name,count", [("fast-arm-single-gamepad",4),("fast-arm-left-gamepad",4),("fast-arm-right-gamepad",4),("fast-arm-bimanual-gamepad",8)])
def test_same_runner_supports_each_registered_model(monkeypatch,name,count):
    profile=replace(load_launch_profile(name),steps=4,interval_s=.0001,grace_period_s=.0001)
    FakeServer.instances.clear()
    FakeServer.incoming=[message((0.,0.,0.,0.),0),message((.55,0.,-.55,0.),1)]
    monkeypatch.setattr(module,"WebSocketPublisherServer",FakeServer)
    run_model_websocket_publisher(profile,clock=lambda:1.)
    payloads=[json.loads(v) for v in FakeServer.instances[-1].messages]
    assert payloads and all(len(v["qpos"])==count for v in payloads)
    assert payloads[-1]["qpos"]!=payloads[0]["qpos"]
    assert payloads[-1]["metadata"]["gamepad_trigger_control_v1"]["endpoint_bindings"]==profile.side_to_endpoint
