from __future__ import annotations

import asyncio

from tests.support.input_source_doubles import StaticInputSource
from tests.support.kinematics_solver_doubles import ZeroForwardKinematicsSolver, ZeroInverseKinematicsSolver
from tests.support.motion_doubles import NoOpMotionGenerator
from tests.support.mujoco_doubles import NoOpMuJoCoSimulator
from xpotato_sim.schemas import (
    InputIntent,
    JointCommand,
    MotionCommand,
    MuJoCoState,
    RawInputFrame,
)
from tests.support.transport_doubles import NoOpStatePublisher


def test_static_input_source_double_returns_provided_frame() -> None:
    frame = RawInputFrame(source="replay", timestamp_s=3.25, values=(1.0, 2.0))
    source = StaticInputSource(frame)

    assert source.read_frame() is frame


def test_noop_motion_generator_returns_command() -> None:
    intent = InputIntent(source="keyboard", timestamp_s=5.0)
    generator = NoOpMotionGenerator()
    command = generator.update(intent, dt_s=0.016)

    assert isinstance(command, MotionCommand)
    assert command.timestamp_s == 5.0
    assert command.target is None
    assert command.joint is None


def test_zero_kinematics_solvers_return_zero_and_empty_commands() -> None:
    fk = ZeroForwardKinematicsSolver()
    ik = ZeroInverseKinematicsSolver()

    assert fk.forward((1.0, 2.0, 3.0)) == (0.0, 0.0, 0.0)
    assert ik.solve((0.1, 0.2, 0.3)) == JointCommand()


def test_noop_mujoco_simulator_snapshot_returns_state() -> None:
    simulator = NoOpMuJoCoSimulator()
    simulator.step(0.25)
    snapshot = simulator.snapshot()

    assert isinstance(snapshot, MuJoCoState)
    assert snapshot.frame_index == 1
    assert snapshot.time_s == 0.25
    assert snapshot.qpos == ()
    assert snapshot.bodies == ()


def test_noop_state_publisher_publish_is_awaitable() -> None:
    publisher = NoOpStatePublisher()
    state = MuJoCoState(frame_index=7, time_s=1.0)

    async def run_publish() -> None:
        await publisher.publish(state)

    asyncio.run(run_publish())

    assert publisher.last_state == state
