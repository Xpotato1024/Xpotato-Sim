"""Explicit deterministic noop Input Source Plugin."""

from collections.abc import Mapping

from xpotato_sim.runtime.experiment.input_source import InputSourceHealth, InputSourceHealthStatus
from xpotato_sim.schemas import RawInputFrame


class NoopInputSource:
    """同一zero/no-op frameを返すside-effect-free reader。

    explicit compatibility path用であり、fallback selectionではない。start/closeやexternal
    I/Oを持たず、healthはactiveのまま、readごとに同じfrozen frameを返す。
    """

    def __init__(self, frame: RawInputFrame) -> None:
        self._frame = frame

    def read_frame(self) -> RawInputFrame:
        return self._frame

    def current_health(self) -> InputSourceHealth:
        return InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0)


def build_frames(parameters: Mapping[str, object]) -> tuple[RawInputFrame, ...]:
    return (RawInputFrame(source="noop", timestamp_s=0.0, metadata=dict(parameters["metadata"])),)


def build_reader(parameters: Mapping[str, object]) -> NoopInputSource:
    frames = build_frames(parameters)
    return NoopInputSource(frames[0])


__all__ = ["NoopInputSource", "build_frames", "build_reader"]
