from __future__ import annotations

from typing import Protocol, runtime_checkable

from xpotato_sim.schemas import InputIntent, MotionCommand


class MotionGenerator(Protocol):
    def update(self, intent: InputIntent, dt_s: float) -> MotionCommand:
        ...


@runtime_checkable
class EndpointDeltaMotionGenerator(Protocol):
    """world座標の位置増分/sampleを明示APIで受ける局所solver capability。"""

    def set_current_qpos_rad(self, current_qpos_rad: tuple[float, ...]) -> None: ...

    def update_delta(self, intent: InputIntent, dt_s: float) -> MotionCommand: ...
