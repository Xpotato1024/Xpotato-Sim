from __future__ import annotations

import asyncio
import json
import socket

from websockets.asyncio.client import connect

from xpotato_sim.schemas import BodyTransform, MuJoCoState, SiteTransform
from xpotato_sim.transport import WebSocketPublisherServer, WebSocketStatePublisher


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _receive_single_payload() -> dict[str, object]:
    port = _find_free_port()

    async with WebSocketPublisherServer(host="127.0.0.1", port=port) as server:
        publisher = WebSocketStatePublisher(server)
        state = MuJoCoState(
            frame_index=1,
            time_s=0.25,
            qpos=(1.0, 2.0),
            qvel=(3.0, 4.0),
            bodies=(
                BodyTransform(
                    name="base_link",
                    position_m=(0.0, 0.0, 0.0),
                    quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
                ),
            ),
            sites=(
                SiteTransform(
                    name="tip",
                    position_m=(0.1, 0.2, 0.3),
                    quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
                ),
            ),
        )

        async with connect(f"ws://127.0.0.1:{port}") as websocket:
            await publisher.publish(state)
            message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            return json.loads(message)


async def _receive_inbound_message_and_single_payload() -> tuple[list[str], dict[str, object]]:
    port = _find_free_port()
    received_messages: list[str] = []
    message_received = asyncio.Event()

    async def on_message(message: str) -> None:
        received_messages.append(message)
        message_received.set()

    async with WebSocketPublisherServer(host="127.0.0.1", port=port, on_message=on_message) as server:
        publisher = WebSocketStatePublisher(server)
        state = MuJoCoState(
            frame_index=1,
            time_s=0.25,
            qpos=(1.0, 2.0),
            qvel=(3.0, 4.0),
            bodies=(
                BodyTransform(
                    name="base_link",
                    position_m=(0.0, 0.0, 0.0),
                    quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
                ),
            ),
            sites=(
                SiteTransform(
                    name="tip",
                    position_m=(0.1, 0.2, 0.3),
                    quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
                ),
            ),
        )

        async with connect(f"ws://127.0.0.1:{port}") as websocket:
            await websocket.send('{"type":"viewer_control_message","timestamp_s":1.0,"source_kind":"keyboard","keyboard":{"active_key_codes":[],"key_state":{},"focus_state":"focused","zero_state":true}}')
            await asyncio.wait_for(message_received.wait(), timeout=5.0)
            await publisher.publish(state)
            message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            return received_messages, json.loads(message)


async def _receive_inbound_message_with_handler_error() -> tuple[tuple[BaseException, ...], dict[str, object]]:
    port = _find_free_port()
    handler_called = asyncio.Event()

    async def on_message(message: str) -> None:
        handler_called.set()
        raise RuntimeError(f"bad message: {message}")

    async with WebSocketPublisherServer(host="127.0.0.1", port=port, on_message=on_message) as server:
        publisher = WebSocketStatePublisher(server)
        state = MuJoCoState(
            frame_index=1,
            time_s=0.25,
            qpos=(1.0, 2.0),
            qvel=(3.0, 4.0),
            bodies=(
                BodyTransform(
                    name="base_link",
                    position_m=(0.0, 0.0, 0.0),
                    quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
                ),
            ),
            sites=(
                SiteTransform(
                    name="tip",
                    position_m=(0.1, 0.2, 0.3),
                    quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
                ),
            ),
        )

        async with connect(f"ws://127.0.0.1:{port}") as websocket:
            await websocket.send('{"type":"viewer_control_message","timestamp_s":1.0,"source_kind":"keyboard","keyboard":{"active_key_codes":[],"key_state":{},"focus_state":"focused","zero_state":true}}')
            await asyncio.wait_for(handler_called.wait(), timeout=5.0)
            await publisher.publish(state)
            message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            return server.message_handler_errors, json.loads(message)


def test_websocket_publisher_server_delivers_payload_v0_to_connected_client() -> None:
    payload = asyncio.run(_receive_single_payload())

    assert payload["version"] == 0
    assert payload["frame_index"] == 1
    assert payload["qpos"] == [1.0, 2.0]
    assert payload["qvel"] == [3.0, 4.0]
    assert any(body["name"] == "base_link" for body in payload["bodies"])
    assert any(site["name"] == "tip" for site in payload["sites"])


def test_websocket_publisher_server_receives_inbound_text_messages_and_still_publishes_payloads() -> None:
    received_messages, payload = asyncio.run(_receive_inbound_message_and_single_payload())

    assert received_messages == [
        '{"type":"viewer_control_message","timestamp_s":1.0,"source_kind":"keyboard","keyboard":{"active_key_codes":[],"key_state":{},"focus_state":"focused","zero_state":true}}'
    ]
    assert payload["version"] == 0
    assert payload["frame_index"] == 1


def test_websocket_publisher_server_records_handler_errors_without_stopping_payload_publish() -> None:
    handler_errors, payload = asyncio.run(_receive_inbound_message_with_handler_error())

    assert len(handler_errors) == 1
    assert isinstance(handler_errors[0], RuntimeError)
    assert "bad message" in str(handler_errors[0])
    assert payload["version"] == 0
    assert payload["frame_index"] == 1


def test_websocket_publisher_server_stop_is_clean() -> None:
    port = _find_free_port()

    async def run() -> None:
        server = WebSocketPublisherServer(host="127.0.0.1", port=port)
        await server.start()
        assert server.is_running
        await server.stop()
        assert not server.is_running

    asyncio.run(run())


def test_websocket_publisher_server_rejects_invalid_configuration() -> None:
    for kwargs in (
        {"host": "", "port": 8766},
        {"host": "127.0.0.1", "port": 0},
        {"host": "127.0.0.1", "port": 70000},
    ):
        try:
            WebSocketPublisherServer(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")
