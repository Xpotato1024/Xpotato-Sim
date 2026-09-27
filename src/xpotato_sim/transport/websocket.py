from __future__ import annotations

import json
from typing import Protocol

from xpotato_sim.schemas import MuJoCoState
from xpotato_sim.transport.payload import mujoco_state_to_payload


class WebSocketSender(Protocol):
    async def send(self, message: str) -> None:
        ...


class WebSocketStatePublisher:
    """Delivery-only state publisher that serializes MuJoCoState for WebSocket."""

    def __init__(self, sender: WebSocketSender) -> None:
        self._sender = sender

    async def publish(self, state: MuJoCoState) -> None:
        await self._sender.send(serialize_mujoco_state_message(state))


def serialize_mujoco_state_message(state: MuJoCoState) -> str:
    payload = mujoco_state_to_payload(state)
    return json.dumps(payload)


__all__ = ["WebSocketSender", "WebSocketStatePublisher", "serialize_mujoco_state_message"]
