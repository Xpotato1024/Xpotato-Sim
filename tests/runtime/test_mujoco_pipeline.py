from __future__ import annotations

import asyncio

from xpotato_sim.mujoco_backend import HeadlessMuJoCoSimulator
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.plugins.mappings.replay_mapping import REPLAY_CONTROL_MAPPING_PLUGIN
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.schemas import MuJoCoState
from tests.support.input_source_doubles import StaticInputSource
from tests.support.motion_doubles import NoOpMotionGenerator
from tests.support.mapped_pipeline_builders import (
    build_noop_pipeline,
    build_test_mujoco_pipeline,
)
from tests.support.transport_doubles import NoOpStatePublisher


def test_build_noop_pipeline_still_works() -> None:
    pipeline = build_noop_pipeline()

    assert isinstance(pipeline, ControlMappedRuntimePipeline)


def test_build_mujoco_pipeline_returns_runtime_pipeline_and_state() -> None:
    pipeline = build_test_mujoco_pipeline(model_path=FAST_ARM_ROBOT_PROFILE.mujoco_model_asset)

    assert isinstance(pipeline, ControlMappedRuntimePipeline)
    assert isinstance(pipeline.input_source, StaticInputSource)
    assert pipeline.control_mapping is REPLAY_CONTROL_MAPPING_PLUGIN
    assert isinstance(pipeline.motion_generator, NoOpMotionGenerator)
    assert isinstance(pipeline.simulator, HeadlessMuJoCoSimulator)
    assert isinstance(pipeline.publisher, NoOpStatePublisher)

    state = asyncio.run(pipeline.run_once())

    assert isinstance(state, MuJoCoState)
    assert any(site.name == "tip" for site in state.sites)
    assert any(body.name == "base_link" for body in state.bodies)


def test_generic_builder_does_not_infer_fast_arm_when_model_is_absent() -> None:
    import xpotato_sim.runtime as runtime

    assert not hasattr(runtime, "build_mujoco_pipeline")


def test_build_mujoco_pipeline_accepts_explicit_default_model_path() -> None:
    pipeline = build_test_mujoco_pipeline(model_path=FAST_ARM_ROBOT_PROFILE.mujoco_model_asset)

    state = asyncio.run(pipeline.run_once())

    assert isinstance(state, MuJoCoState)
    assert state.frame_index == 1
    assert any(site.name == "tip" for site in state.sites)
    assert any(body.name == "base_link" for body in state.bodies)
