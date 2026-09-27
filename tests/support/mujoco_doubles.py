"""Test-only MuJoCo backend doubles."""

from __future__ import annotations

from xpotato_sim.schemas import MotionCommand, MuJoCoState


class NoOpMuJoCoSimulator:
    """No-op MuJoCo simulator stub, not a real simulator or model loader."""

    def __init__(self) -> None:
        self._time_s = 0.0
        self._frame_index = 0
        self._last_command: MotionCommand | None = None

    def apply_command(self, command: MotionCommand) -> None:
        self._last_command = command

    @property
    def last_command(self) -> MotionCommand | None:
        return self._last_command

    def step(self, dt_s: float) -> None:
        self._time_s += dt_s
        self._frame_index += 1

    def snapshot(self) -> MuJoCoState:
        return MuJoCoState(frame_index=self._frame_index, time_s=self._time_s)


__all__ = ["NoOpMuJoCoSimulator"]
