from __future__ import annotations

from typing import Protocol

from xpotato_sim.schemas import JointPositionCommand, MotionCommand, MuJoCoState


class MuJoCoSimulator(Protocol):
    def apply_joint_position_command(
        self, command: JointPositionCommand
    ) -> None:
        ...

    def record_motion_command_envelope(
        self, command: MotionCommand
    ) -> None:
        ...

    def step(self, dt_s: float) -> None:
        ...

    def snapshot(self) -> MuJoCoState:
        ...
