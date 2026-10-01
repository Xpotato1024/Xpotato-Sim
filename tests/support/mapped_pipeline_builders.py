"""Test-only canonical mapped pipeline builders."""

from __future__ import annotations

from xpotato_sim.runtime.execution.command_routes import project_joint_position_command

from collections.abc import Mapping
from pathlib import Path
from dataclasses import dataclass

from xpotato_sim.mujoco_backend import HeadlessMuJoCoSimulator
from xpotato_sim.mujoco_backend.model_loader import ModelResourceBundle
from xpotato_sim.plugins.mappings.replay_mapping import REPLAY_CONTROL_MAPPING_PLUGIN
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.control.input_source_state import RuntimeInputSourceState
from xpotato_sim.runtime.experiment.contracts import (
    CommandSemanticsRoute,
    JOINT_POSITION_COMMAND_V1,
)
from xpotato_sim.runtime.safety.input_safety import (
    RuntimeInputSafetyResult,
    build_runtime_input_safety_result,
)
from xpotato_sim.runtime.safety.qpos_feasibility import QposFeasibilityGuard
from xpotato_sim.schemas import (
    InputIntent,
    JointPositionCommand,
    MotionCommand,
    MuJoCoState,
    RawInputFrame,
)
from tests.support.input_source_doubles import StaticInputSource
from tests.support.motion_doubles import NoOpMotionGenerator
from tests.support.mujoco_doubles import NoOpMuJoCoSimulator
from tests.support.transport_doubles import NoOpStatePublisher


@dataclass(frozen=True, slots=True)
class _TestMotionCommandExecutionBinding:
    """Legacy backend application retained only for isolated pipeline tests."""

    route_identity: object
    control_semantics_identity: object
    robot_command_semantics_identity: object
    command_type = JointPositionCommand
    requires_motion_generator = True

    def execute(
        self,
        intent: InputIntent,
        *,
        dt_s: float,
        pre_step_state: MuJoCoState,
        source_state: RuntimeInputSourceState,
        pipeline: ControlMappedRuntimePipeline,
    ) -> RuntimeInputSafetyResult:
        assert pipeline.motion_generator is not None
        command = pipeline.motion_generator.update(intent, dt_s)
        return self.execute_motion_command(
            command,
            pre_step_state=pre_step_state,
            source_state=source_state,
            pipeline=pipeline,
        )

    def execute_motion_command(
        self,
        command: MotionCommand,
        *,
        pre_step_state: MuJoCoState,
        source_state: RuntimeInputSourceState,
        pipeline: ControlMappedRuntimePipeline,
    ) -> RuntimeInputSafetyResult:
        result = build_runtime_input_safety_result(
            command,
            source_state=source_state,
            current_state=pre_step_state,
            qpos_feasibility_guard=pipeline.qpos_feasibility_guard,
        )
        # 空のtest-only envelopeは指令を作らず、診断保持とphysics stepだけを行う。
        joint = result.motion_command.joint
        if joint is not None and (joint.joint_angles_rad or joint.joint_velocities_rad_s):
            pipeline.simulator.apply_joint_position_command(project_joint_position_command(result.motion_command))
        pipeline.simulator.record_motion_command_envelope(result.motion_command)
        return result


def _test_command_execution() -> tuple[
    CommandSemanticsRoute,
    _TestMotionCommandExecutionBinding,
]:
    route = REPLAY_CONTROL_MAPPING_PLUGIN.resolve_command_semantics_route()
    return (
        route,
        _TestMotionCommandExecutionBinding(
            route.identity,
            route.control_semantics_identity,
            JOINT_POSITION_COMMAND_V1,
        ),
    )


def build_noop_pipeline(
    frame: RawInputFrame | None = None,
    config: RuntimeConfig | None = None,
) -> ControlMappedRuntimePipeline:
    runtime_config = RuntimeConfig() if config is None else config
    raw_frame = frame if frame is not None else RawInputFrame(source="noop", timestamp_s=0.0)
    route, command_execution = _test_command_execution()
    return ControlMappedRuntimePipeline(
        config=runtime_config,
        input_source=StaticInputSource(raw_frame),
        control_mapping=REPLAY_CONTROL_MAPPING_PLUGIN,
        control_mapping_parameters={},
        motion_generator=NoOpMotionGenerator(),
        simulator=NoOpMuJoCoSimulator(),
        publisher=NoOpStatePublisher(),
        command_semantics_route=route,
        command_execution=command_execution,
    )


def build_test_mujoco_pipeline(
    *,
    frame: RawInputFrame | None = None,
    config: RuntimeConfig | None = None,
    model_path: str | Path | ModelResourceBundle,
    qpos_feasibility_guard: QposFeasibilityGuard | None = None,
    initial_keyframe_name: str | None = None,
    state_metadata: Mapping[str, object] | None = None,
    robot_profile_metadata: Mapping[str, object] | None = None,
) -> ControlMappedRuntimePipeline:
    runtime_config = RuntimeConfig() if config is None else config
    raw_frame = frame if frame is not None else RawInputFrame(source="noop", timestamp_s=0.0)
    route, command_execution = _test_command_execution()
    return ControlMappedRuntimePipeline(
        config=runtime_config,
        input_source=StaticInputSource(raw_frame),
        control_mapping=REPLAY_CONTROL_MAPPING_PLUGIN,
        control_mapping_parameters={},
        motion_generator=NoOpMotionGenerator(),
        simulator=HeadlessMuJoCoSimulator.from_model_path(
            model_path,
            initial_keyframe_name=initial_keyframe_name,
        ),
        publisher=NoOpStatePublisher(),
        command_semantics_route=route,
        command_execution=command_execution,
        qpos_feasibility_guard=qpos_feasibility_guard,
        state_metadata=state_metadata,
        robot_profile_metadata=robot_profile_metadata,
    )


__all__ = ["build_noop_pipeline", "build_test_mujoco_pipeline"]
