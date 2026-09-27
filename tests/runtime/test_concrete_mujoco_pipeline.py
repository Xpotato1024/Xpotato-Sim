from __future__ import annotations

import asyncio
from dataclasses import replace
from math import dist

import pytest

import xpotato_sim.runtime.composition.concrete_mujoco_pipeline as concrete_pipeline_module
from xpotato_sim.motion import TargetToJointMotionGenerator
from xpotato_sim.plugins.mappings.replay_mapping import REPLAY_CONTROL_MAPPING_PLUGIN
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state
from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import FastArmEndpointInverseKinematicsSolver
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.runtime.evaluation.endpoint_metrics import EndpointEvaluationStatePublisher
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.robot_bundle import RobotBundle
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from xpotato_sim.schemas import JointCommand, MuJoCoState, RawInputFrame
from generic_qpos_test_doubles import RejectingGenericQposGuard


class RecordingPublisher:
    def __init__(self) -> None:
        self.states: list[MuJoCoState] = []

    async def publish(self, state: MuJoCoState) -> None:
        self.states.append(state)


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


def test_build_concrete_mujoco_pipeline_uses_concrete_solver_path() -> None:
    publisher = RecordingPublisher()
    pipeline = build_concrete_mujoco_pipeline(publisher=publisher)

    assert isinstance(pipeline, ControlMappedRuntimePipeline)
    assert isinstance(pipeline.motion_generator, TargetToJointMotionGenerator)
    assert isinstance(pipeline.motion_generator._ik_solver, FastArmEndpointInverseKinematicsSolver)
    assert isinstance(pipeline.publisher, EndpointEvaluationStatePublisher)
    assert pipeline.publisher.publisher is publisher
    assert pipeline.simulator.last_command is None


def test_build_concrete_mujoco_pipeline_binds_current_bundle_canonical_execution() -> None:
    robot_bundle = resolve_robot_bundle("fast_arm")
    route = REPLAY_CONTROL_MAPPING_PLUGIN.resolve_command_semantics_route()

    pipeline = build_concrete_mujoco_pipeline(publisher=RecordingPublisher())

    assert pipeline.command_semantics_route is route
    assert pipeline.command_execution.provider is (
        robot_bundle.command_semantic_provider(
            route.robot_command_semantics_identity
        )
    )


def test_build_concrete_mujoco_pipeline_uses_catalog_bundle_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = concrete_pipeline_module.resolve_robot_bundle

    def recording_resolver(profile_id: str, **kwargs):  # noqa: ANN202
        calls.append(f"{profile_id}/v{kwargs['robot_logical_version']}")
        return original(profile_id, **kwargs)

    monkeypatch.setattr(concrete_pipeline_module, "resolve_robot_bundle", recording_resolver)
    build_concrete_mujoco_pipeline(publisher=RecordingPublisher())
    assert calls == ["fast_arm/v1"]


def test_concrete_builder_rejects_aliased_bundle_identity_before_backend_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aliased_bundle = _bundle_with_identity(VersionedIdentity("robot_b", 1))

    class _AliasedCatalog:
        def resolve_profile(self, selection):  # noqa: ANN001, ANN201
            _ = selection
            return aliased_bundle.profile

        def resolve_bundle(self, selection):  # noqa: ANN001, ANN201
            _ = selection
            return aliased_bundle

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
        build_concrete_mujoco_pipeline(
            config=RuntimeConfig(robot_profile_id="robot_b"),
            publisher=RecordingPublisher(),
            robot_catalog=_AliasedCatalog(),  # type: ignore[arg-type]
        )

    assert build_calls == 0


@pytest.mark.parametrize("reject", [False, True])
def test_concrete_pipeline_profile_metadata_cannot_be_spoofed(reject: bool) -> None:
    spoofed = {
        "robot_profile_id": "spoofed",
        "model_contract_version": "spoofed/v9",
        "robot_joint_names": ("wrong",),
        "robot_qpos_dimension": 999,
    }
    frame = RawInputFrame(
        source="replay",
        timestamp_s=0.0,
        metadata={
            **spoofed,
            "desired_endpoint_m": (0.6, 0.0, 0.1),
            "target_position_m": (0.6, 0.0, 0.1),
        },
    )
    publisher = RecordingPublisher()
    pipeline = build_concrete_mujoco_pipeline(frames=(frame,), publisher=publisher)
    if reject:
        pipeline.qpos_feasibility_guard = RejectingGenericQposGuard()

    state = asyncio.run(pipeline.run_once())

    assert state.metadata["robot_profile_id"] == "fast_arm"
    assert state.metadata["model_contract_version"] == FAST_ARM_ROBOT_PROFILE.model_contract_version
    assert state.metadata["robot_joint_names"] == FAST_ARM_ROBOT_PROFILE.canonical_joint_names
    assert state.metadata["robot_qpos_dimension"] == 4


def test_concrete_mujoco_pipeline_emits_non_empty_joint_command_and_updates_qpos() -> None:
    publisher = RecordingPublisher()
    pipeline = build_concrete_mujoco_pipeline(publisher=publisher)

    state = asyncio.run(pipeline.run_once())

    assert isinstance(state, MuJoCoState)
    assert len(publisher.states) == 1
    assert pipeline.simulator.last_command is not None
    assert pipeline.simulator.last_command.joint is not None
    assert pipeline.simulator.last_command.joint != JointCommand()
    assert pipeline.simulator.last_command.metadata["target_position_m"] == (0.6, 0.0, 0.1)
    assert pipeline.simulator.last_command.metadata["desired_endpoint_m"] == (0.6, 0.0, 0.1)
    assert pipeline.simulator.last_command.joint.joint_angles_rad[:2] != (0.0, 0.0)
    assert len(pipeline.simulator.last_command.joint.joint_angles_rad) == 4
    assert pipeline.simulator.last_command.joint.joint_angles_rad[2:] != (0.0, 0.0)
    assert state.qpos[:4] == pytest.approx(pipeline.simulator.last_command.joint.joint_angles_rad, abs=1e-9)
    assert len(publisher.states) == 1
    assert "endpoint_evaluation" in publisher.states[0].metadata
    endpoint_evaluation = publisher.states[0].metadata["endpoint_evaluation"]
    assert endpoint_evaluation["unit"] == "meter"
    assert endpoint_evaluation["site_endpoint_coordinate_frame"] == (
        "MuJoCo world / scene frame"
    )
    assert endpoint_evaluation["site_endpoint_m"] == pytest.approx(
        extract_fast_arm_tip_site_endpoint_from_state(state).position_m,
        abs=1e-9,
    )


def test_concrete_mujoco_pipeline_handles_small_3d_target_without_padding_or_plane_rejection() -> None:
    publisher = RecordingPublisher()
    frame = RawInputFrame(
        source="replay",
        timestamp_s=1.0,
        metadata={
            "preset": "small-3d-target",
            "desired_endpoint_m": (0.58, 0.04, 0.12),
            "target_position_m": (0.58, 0.04, 0.12),
        },
    )
    pipeline = build_concrete_mujoco_pipeline(frames=(frame,), publisher=publisher)

    state = asyncio.run(pipeline.run_once())

    assert isinstance(state, MuJoCoState)
    assert pipeline.simulator.last_command is not None
    assert pipeline.simulator.last_command.metadata.get("target_rejected") is not True
    assert len(pipeline.simulator.last_command.joint.joint_angles_rad) == 4
    assert pipeline.simulator.last_command.joint.joint_angles_rad[2:] != (0.0, 0.0)
    assert state.qpos[:4] == pytest.approx(pipeline.simulator.last_command.joint.joint_angles_rad, abs=1e-9)


def test_concrete_mujoco_pipeline_reports_model_mismatch_from_neutral_startup() -> None:
    publisher = RecordingPublisher()
    frame = RawInputFrame(
        source="replay",
        timestamp_s=1.0,
        metadata={
            "preset": "small-3d-target",
            "desired_endpoint_m": (0.57, 0.03, 0.11),
            "target_position_m": (0.57, 0.03, 0.11),
        },
    )
    pipeline = build_concrete_mujoco_pipeline(frames=(frame,), publisher=publisher)
    initial_state = pipeline.simulator.snapshot()
    initial_tip_position_m = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m
    desired_endpoint_m = frame.metadata["desired_endpoint_m"]
    initial_error_norm_m = dist(initial_tip_position_m, desired_endpoint_m)

    pipeline.motion_generator.set_current_qpos_rad(initial_state.qpos)
    state = asyncio.run(pipeline.run_once())
    final_tip_position_m = extract_fast_arm_tip_site_endpoint_from_state(state).position_m
    final_error_norm_m = dist(final_tip_position_m, desired_endpoint_m)

    assert isinstance(state, MuJoCoState)
    assert final_tip_position_m != initial_tip_position_m
    assert final_tip_position_m != desired_endpoint_m
    assert final_error_norm_m > initial_error_norm_m
    assert pipeline.simulator.last_command.metadata.get("target_rejected") is not True
    assert publisher.states[0].metadata["endpoint_evaluation"]["desired_to_site_error_norm_m"] == pytest.approx(
        final_error_norm_m,
        abs=1e-9,
    )
    assert len(pipeline.simulator.last_command.joint.joint_angles_rad) == 4
    assert pipeline.simulator.last_command.joint.joint_angles_rad[2:] != (0.0, 0.0)


def test_concrete_mujoco_pipeline_rejects_missing_target_position() -> None:
    publisher = RecordingPublisher()
    frame = RawInputFrame(source="replay", timestamp_s=1.0, metadata={"preset": "missing-target"})
    pipeline = build_concrete_mujoco_pipeline(frames=(frame,), publisher=publisher)

    with pytest.raises(ValueError, match="target_position_m"):
        asyncio.run(pipeline.run_once())


def test_concrete_mujoco_pipeline_rejects_unreachable_target() -> None:
    publisher = RecordingPublisher()
    frame = RawInputFrame(
        source="replay",
        timestamp_s=1.0,
        metadata={"preset": "unreachable", "target_position_m": (10.0, 0.0, 0.0)},
    )
    pipeline = build_concrete_mujoco_pipeline(frames=(frame,), publisher=publisher)

    state = asyncio.run(pipeline.run_once())

    assert isinstance(state, MuJoCoState)
    assert pipeline.simulator.last_command is not None
    assert pipeline.simulator.last_command.metadata["target_rejected"] is True
    assert pipeline.simulator.last_command.metadata["target_rejection_reason"] == "target_unreachable"
    assert pipeline.simulator.last_command.metadata["target_rejection_message"] == (
        "target_position_m is outside the reachable workspace"
    )
    assert state.target_position_m is None
    assert "endpoint_evaluation" not in publisher.states[0].metadata
