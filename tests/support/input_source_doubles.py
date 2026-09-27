"""Test-only input source doubles."""

from __future__ import annotations

from xpotato_sim.schemas import RawInputFrame


class StaticInputSource:
    """Static input source stub that always returns the provided frame."""

    def __init__(self, frame: RawInputFrame) -> None:
        self._frame = frame

    def read_frame(self) -> RawInputFrame:
        return self._frame


__all__ = ["StaticInputSource"]
