"""Test-only motion doubles."""

from __future__ import annotations

from xpotato_sim.schemas import InputIntent, MotionCommand


class NoOpMotionGenerator:
    """No-op motion generator stub, not a real motion or IK implementation."""

    def update(self, intent: InputIntent, dt_s: float) -> MotionCommand:
        return MotionCommand(
            timestamp_s=intent.timestamp_s,
            target=None,
            joint=None,
            metadata=dict(intent.metadata),
        )


__all__ = ["NoOpMotionGenerator"]
