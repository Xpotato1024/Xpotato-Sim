from __future__ import annotations

from xpotato_sim.schemas import JointCommand, MotionCommand


def motion_command_to_qpos_command(command: MotionCommand) -> JointCommand | None:
    """MotionCommand から backend の qpos command boundary を切り出す。"""

    if command.target is not None:
        raise ValueError("target command は qpos command boundary では未対応です")

    return command.joint
