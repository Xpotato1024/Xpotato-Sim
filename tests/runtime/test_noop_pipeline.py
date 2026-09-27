from __future__ import annotations

import asyncio

from tests.support.mujoco_doubles import NoOpMuJoCoSimulator
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from tests.support.mapped_pipeline_builders import build_noop_pipeline
from xpotato_sim.schemas import MuJoCoState, RawInputFrame


def test_build_noop_pipeline_returns_control_mapped_pipeline() -> None:
    pipeline = build_noop_pipeline()

    assert isinstance(pipeline, ControlMappedRuntimePipeline)


def test_run_once_returns_mujoco_state() -> None:
    pipeline = build_noop_pipeline()

    state = asyncio.run(pipeline.run_once())

    assert isinstance(state, MuJoCoState)
    assert state.frame_index == 1
    assert state.time_s == RuntimeConfig().dt_s


def test_build_noop_pipeline_can_flow_custom_frame() -> None:
    frame = RawInputFrame(source="test", timestamp_s=12.5, values=(1.0,))
    pipeline = build_noop_pipeline(frame=frame)

    state = asyncio.run(pipeline.run_once())

    assert isinstance(pipeline.simulator, NoOpMuJoCoSimulator)
    assert pipeline.simulator.last_command is not None
    assert pipeline.publisher.last_state == state


def test_run_once_honors_dt_override() -> None:
    pipeline = build_noop_pipeline()

    first = asyncio.run(pipeline.run_once(dt_s=0.5))
    second = asyncio.run(pipeline.run_once(dt_s=0.25))

    assert first.time_s == 0.5
    assert second.time_s == 0.75
    assert second.frame_index == 2
