from __future__ import annotations

from typing import Protocol

from xpotato_sim.schemas import MuJoCoState


class StatePublisher(Protocol):
    async def publish(self, state: MuJoCoState) -> None:
        ...
