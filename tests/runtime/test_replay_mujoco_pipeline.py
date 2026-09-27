from __future__ import annotations

import asyncio
from dataclasses import replace
from inspect import signature

import pytest

from xpotato_sim.motion import InputIntentMotionGenerator
from xpotato_sim.mujoco_backend import HeadlessMuJoCoSimulator
from xpotato_sim.plugins.robots.fast_arm.adapter.feasibility import (
    FastArmJointLimitGuard,
)
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.robot_bundle import RobotBundle
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.composition.replay_mujoco_pipeline import build_replay_mujoco_pipeline
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.plugins.mappings.replay_mapping import REPLAY_CONTROL_MAPPING_PLUGIN
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from xpotato_sim.schemas import (
    JointPositionCommand,
    MotionCommand,
    MuJoCoState,
    RawInputFrame,
)


def _build_replay_pipeline(**kwargs):
    return build_replay_mujoco_pipeline(
        config=RuntimeConfig(robot_profile_id="fast_arm"),
        robot_bundle=resolve_robot_bundle("fast_arm"),
        **kwargs,
    )


def _bundle_with_identity(identity: VersionedIdentity) -> RobotBundle:
    bundle = resolve_robot_bundle("fast_arm")
    return RobotBundle(
        identity=identity,
        profile=bundle.profile,
        runtime_plugin=bundle.runtime_plugin,
        capability_providers=tuple(
            type(binding)(
                binding.identity,
                replace(binding.provider, robot_identity=identity),
            )
            for binding in bundle.capability_providers
        ),
        command_semantic_providers=tuple(
            type(binding)(
                binding.identity,
                replace(binding.provider, robot_identity=identity),
            )
            for binding in bundle.command_semantic_providers
        ),
        parameter_contract=bundle.parameter_contract,
    )


def test_build_replay_mujoco_pipeline_returns_runtime_pipeline() -> None:
    pipeline = _build_replay_pipeline()

    assert isinstance(pipeline, ControlMappedRuntimePipeline)
    assert isinstance(pipeline.motion_generator, InputIntentMotionGenerator)
    assert isinstance(pipeline.simulator, HeadlessMuJoCoSimulator)
    assert hasattr(pipeline.publisher, "last_state")
    assert isinstance(pipeline.qpos_feasibility_guard, FastArmJointLimitGuard)
    assert pipeline.robot_profile_metadata["robot_profile_id"] == "fast_arm"


def test_replay_builder_binds_current_bundle_canonical_execution() -> None:
    robot_bundle = resolve_robot_bundle("fast_arm")
    route = REPLAY_CONTROL_MAPPING_PLUGIN.resolve_command_semantics_route()

    pipeline = build_replay_mujoco_pipeline(
        config=RuntimeConfig(robot_profile_id="fast_arm"),
        robot_bundle=robot_bundle,
    )

    assert pipeline.command_semantics_route is route
    assert pipeline.command_execution.provider is (
        robot_bundle.command_semantic_provider(
            route.robot_command_semantics_identity
        )
    )


def test_replay_builder_rejects_cross_robot_bundle_before_backend_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    robot_b = _bundle_with_identity(VersionedIdentity("robot_b", 1))
    build_calls = 0

    def fail_if_built(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("backend must not be built for an identity mismatch")

    monkeypatch.setattr(
        type(robot_b.runtime_plugin),
        "build_simulator",
        fail_if_built,
    )

    with pytest.raises(
        ValueError,
        match="production Robot selection/Bundle logical identity mismatch",
    ):
        build_replay_mujoco_pipeline(
            config=RuntimeConfig(robot_profile_id="fast_arm"),
            robot_bundle=robot_b,
        )

    assert build_calls == 0


def test_replay_builder_rejects_stale_bundle_logical_version() -> None:
    robot_v2 = _bundle_with_identity(VersionedIdentity("fast_arm", 2))

    with pytest.raises(
        ValueError,
        match="selection=fast_arm/v1, bundle=fast_arm/v2",
    ):
        build_replay_mujoco_pipeline(
            config=RuntimeConfig(
                robot_profile_id="fast_arm",
                robot_logical_version=1,
            ),
            robot_bundle=robot_v2,
        )


def test_replay_builder_rejects_aliased_bundle_identity_before_backend_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aliased_bundle = _bundle_with_identity(VersionedIdentity("robot_b", 1))
    build_calls = 0

    def fail_if_built(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("backend must not be built for an aliased Bundle")

    monkeypatch.setattr(
        type(aliased_bundle.runtime_plugin),
        "build_simulator",
        fail_if_built,
    )

    with pytest.raises(
        ValueError,
        match="production Robot Bundle/Profile logical identity mismatch",
    ):
        build_replay_mujoco_pipeline(
            config=RuntimeConfig(robot_profile_id="robot_b"),
            robot_bundle=aliased_bundle,
        )

    assert build_calls == 0


def test_replay_builder_rejects_aliased_logical_version_before_backend_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aliased_bundle = _bundle_with_identity(VersionedIdentity("fast_arm", 2))
    build_calls = 0

    def fail_if_built(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("backend must not be built for an aliased version")

    monkeypatch.setattr(
        type(aliased_bundle.runtime_plugin),
        "build_simulator",
        fail_if_built,
    )

    with pytest.raises(
        ValueError,
        match="production Robot Bundle/Profile logical version mismatch",
    ):
        build_replay_mujoco_pipeline(
            config=RuntimeConfig(
                robot_profile_id="fast_arm",
                robot_logical_version=2,
            ),
            robot_bundle=aliased_bundle,
        )

    assert build_calls == 0


def test_replay_builder_has_no_foreign_backend_injection_surface() -> None:
    parameters = signature(build_replay_mujoco_pipeline).parameters

    for name in (
        "resolved_command_execution",
        "simulator",
        "robot_profile_metadata",
        "qpos_feasibility_guard",
        "initial_keyframe_name",
    ):
        assert name not in parameters


def test_run_once_replays_frame_into_mujoco_state() -> None:
    frame = RawInputFrame(
        source="replay",
        timestamp_s=3.5,
        metadata={"case": "R6-A-P1"},
    )
    pipeline = _build_replay_pipeline(frames=(frame,))

    state = asyncio.run(pipeline.run_once())

    assert isinstance(state, MuJoCoState)
    assert state.frame_index == 1
    assert state.time_s > 0.0
    assert any(site.name == "tip" for site in state.sites)
    assert any(body.name == "base_link" for body in state.bodies)
    assert pipeline.publisher.last_state == state


def test_motion_command_reaches_simulator() -> None:
    frame = RawInputFrame(source="replay", timestamp_s=7.25)
    pipeline = _build_replay_pipeline(frames=(frame,))

    asyncio.run(pipeline.run_once())

    assert isinstance(pipeline.simulator.last_command, MotionCommand)
    assert pipeline.simulator.last_command.timestamp_s == frame.timestamp_s
    assert isinstance(
        pipeline.simulator.last_joint_position_command,
        JointPositionCommand,
    )


def test_replay_eof_raises_stop_iteration_without_looping() -> None:
    frame = RawInputFrame(source="replay", timestamp_s=1.0)
    pipeline = _build_replay_pipeline(frames=(frame,), loop=False)

    asyncio.run(pipeline.run_once())

    try:
        asyncio.run(pipeline.run_once())
    except StopIteration:
        return
    except RuntimeError as exc:
        assert isinstance(exc.__cause__, StopIteration)
        return

    raise AssertionError("expected StopIteration on replay EOF")


def test_custom_dt_s_is_forwarded_to_simulator() -> None:
    frame = RawInputFrame(source="replay", timestamp_s=2.0)
    pipeline = _build_replay_pipeline(frames=(frame,))

    asyncio.run(pipeline.run_once(dt_s=0.125))

    assert pipeline.simulator.last_dt_s == 0.125


def test_replay_builder_requires_explicit_robot_selection() -> None:
    with pytest.raises(ValueError, match="requires robot_selection"):
        build_replay_mujoco_pipeline(
            config=RuntimeConfig(),
            robot_bundle=resolve_robot_bundle("fast_arm"),
        )
