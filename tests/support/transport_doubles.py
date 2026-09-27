"""Test-only transport doubles."""

from __future__ import annotations

from xpotato_sim.schemas import MuJoCoState


class NoOpStatePublisher:
    """No-op state publisher stub, not a real network transport."""

    def __init__(self) -> None:
        self.last_state: MuJoCoState | None = None

    async def publish(self, state: MuJoCoState) -> None:
        self.last_state = state


__all__ = ["NoOpStatePublisher"]
