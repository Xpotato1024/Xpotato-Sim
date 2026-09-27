from __future__ import annotations

from collections.abc import Sequence

from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
)
from xpotato_sim.schemas import RawInputFrame


class ReplayInputSource:
    """stored RawInputFrameを決定的orderingで返すoffline replay source。

    filesystem/device access、start/close side effectはない。``loop=False`` では末尾後に
    ``StopIteration``、``loop=True`` では先頭へ戻る。所有するfrozen frame referenceを
    cloneせず返し、単一runtime loopからの直列readを前提とする。
    """

    def __init__(self, frames: Sequence[RawInputFrame], *, loop: bool = False) -> None:
        if not frames:
            raise ValueError("ReplayInputSource requires at least one frame")

        self._frames = tuple(frames)
        self._loop = loop
        self._index = 0

    def read_frame(self) -> RawInputFrame:
        if self._index >= len(self._frames):
            if not self._loop:
                raise StopIteration("ReplayInputSource reached end of frames")
            self._index = 0

        frame = self._frames[self._index]
        self._index += 1
        return frame

    def current_health(self) -> InputSourceHealth:
        return InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0)


__all__ = [
    "ReplayInputSource",
]
