from __future__ import annotations

from dataclasses import dataclass

from xpotato_sim.motion import TargetToJointMotionGenerator
from xpotato_sim.schemas import InputIntent, JointCommand


class RecordingIKSolver:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[float, float, float], tuple[float, ...] | None]] = []

    def solve(
        self,
        target_position_m: tuple[float, float, float],
        seed_joint_angles_rad: tuple[float, ...] | None = None,
    ) -> JointCommand:
        self.calls.append((target_position_m, seed_joint_angles_rad))
        return JointCommand(joint_angles_rad=(1.0, 2.0, 3.0))


@dataclass(slots=True)
class FutureTargetPositionCompatibleIntent:
    source: str
    timestamp_s: float
    target_delta_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    joint_delta_rad: tuple[float, ...] = ()
    metadata: dict[str, object] | None = None
    target_position_m: tuple[float, float, float] | None = None


def test_target_position_triggers_optional_ik_solver_for_future_compatible_object() -> None:
    solver = RecordingIKSolver()
    intent = FutureTargetPositionCompatibleIntent(
        source="replay",
        timestamp_s=2.0,
        metadata={"origin": "ik"},
        target_position_m=(0.1, 0.2, 0.3),
    )

    command = TargetToJointMotionGenerator(
        solver,
        seed_joint_angles_rad=(0.4, 0.5, 0.6),
    ).update(intent, dt_s=0.016)

    assert solver.calls == [((0.1, 0.2, 0.3), (0.4, 0.5, 0.6))]
    assert command.timestamp_s == 2.0
    assert command.joint == JointCommand(joint_angles_rad=(1.0, 2.0, 3.0))
    assert command.target is None
    assert command.metadata["origin"] == "ik"
    assert command.metadata is not intent.metadata


def test_target_delta_is_preserved_in_ik_skeleton() -> None:
    solver = RecordingIKSolver()
    intent = InputIntent(
        source="replay",
        timestamp_s=2.0,
        target_delta_m=(0.2, 0.0, 0.0),
    )

    command = TargetToJointMotionGenerator(solver).update(intent, dt_s=0.016)

    assert command.target is not None
    assert command.target.delta_m == (0.2, 0.0, 0.0)
    assert command.joint is None
    assert solver.calls == []
