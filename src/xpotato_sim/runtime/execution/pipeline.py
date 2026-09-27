"""Canonical mapped runtime pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from xpotato_sim.motion import MotionGenerator
from xpotato_sim.mujoco_backend import MuJoCoSimulator
from xpotato_sim.schemas import InputIntent, MotionCommand, MuJoCoState, RawInputFrame
from xpotato_sim.transport import StatePublisher

from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.control.input_source_state import (
    RuntimeInputSourceState,
    build_runtime_input_source_state_from_metadata,
    reconcile_runtime_input_source_state,
    annotate_raw_input_frame,
)
from xpotato_sim.runtime.experiment.contracts import ControlMappingPlugin, ControlMappingStrategy
from xpotato_sim.runtime.experiment.input_source import HealthyInputSource, ManagedInputSource
from xpotato_sim.runtime.experiment.input_source import InputSourceMappingAdapterContract
from xpotato_sim.runtime.safety.qpos_feasibility import QposFeasibilityGuard
from xpotato_sim.runtime.composition.robot_profile_metadata import merge_runtime_metadata

if TYPE_CHECKING:
    from xpotato_sim.runtime.composition.robot_bundle import EndpointPoseProvider
    from xpotato_sim.runtime.execution.command_routes import (
        CommandExecutionBinding,
    )
    from xpotato_sim.runtime.experiment.contracts import CommandSemanticsRoute
    from xpotato_sim.runtime.safety.input_safety import RuntimeInputSafetyResult


@dataclass(slots=True)
class ControlMappedRuntimePipeline:
    """Production runtime pipeline using the versioned Control Mapping Plugin."""

    config: RuntimeConfig
    input_source: HealthyInputSource
    control_mapping: ControlMappingPlugin
    motion_generator: MotionGenerator | None
    simulator: MuJoCoSimulator
    publisher: StatePublisher
    control_mapping_parameters: Mapping[str, object]
    command_semantics_route: CommandSemanticsRoute
    command_execution: CommandExecutionBinding
    mapping_input_adapter: InputSourceMappingAdapterContract | None = None
    qpos_feasibility_guard: QposFeasibilityGuard | None = None
    state_metadata: Mapping[str, object] | None = None
    robot_profile_metadata: Mapping[str, object] | None = None
    endpoint_pose_provider: EndpointPoseProvider | None = None
    _mapping_strategy: ControlMappingStrategy | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        from xpotato_sim.runtime.execution.command_routes import (
            CommandExecutionBinding,
        )

        if not isinstance(self.command_execution, CommandExecutionBinding):
            raise TypeError(
                "runtime pipeline requires a typed command execution binding"
            )
        if (
            self.command_execution.route_identity
            != self.command_semantics_route.identity
            or self.command_execution.control_semantics_identity
            != self.command_semantics_route.control_semantics_identity
            or self.command_execution.robot_command_semantics_identity
            != self.command_semantics_route.robot_command_semantics_identity
            or self.command_execution.command_type
            is not self.command_semantics_route.execution_strategy.command_type
        ):
            raise ValueError(
                "runtime pipeline command route/execution binding mismatch"
            )

    def reset_mapping_session(self) -> None:
        """試行境界で状態を破棄する。run_onceを連続利用するcallerは新試行前に呼ぶ。"""
        self._mapping_strategy = None

    def map_input(self, frame: RawInputFrame, *, pre_step_state: MuJoCoState | None = None,
                  endpoint_pose_provider: EndpointPoseProvider | None = None) -> InputIntent:
        """固定Mappingと同stepのroute-owned観測contextから入力を変換する。"""
        from xpotato_sim.runtime.execution.command_routes import MappingRuntimeContextBinding

        parameters = self.control_mapping_parameters
        if isinstance(self.command_execution, MappingRuntimeContextBinding):
            provider = endpoint_pose_provider or self.endpoint_pose_provider
            if provider is None:
                raise ValueError("measured endpoint context requires an endpoint pose provider")
            state = self.simulator.snapshot() if pre_step_state is None else pre_step_state
            parameters = self.command_execution.mapping_parameters(parameters, state=state, provider=provider)
        mapping_input = self.mapping_input_adapter(frame) if self.mapping_input_adapter is not None else frame
        if self._mapping_strategy is None:
            self._mapping_strategy = self.control_mapping.create_session_strategy()
        try:
            intent = self._mapping_strategy.map_input(mapping_input, parameters)
        except Exception:
            self.reset_mapping_session()
            raise
        if not isinstance(intent, InputIntent):
            raise TypeError("control mapping strategy must return a typed InputIntent")
        return intent

    def execute_intent(
        self,
        intent: InputIntent,
        *,
        dt_s: float,
        pre_step_state: MuJoCoState,
        source_state: RuntimeInputSourceState,
        endpoint_pose_provider: EndpointPoseProvider | None = None,
    ) -> RuntimeInputSafetyResult:
        from xpotato_sim.runtime.execution.command_routes import RuntimeIntentPreparation

        if isinstance(self.command_execution, RuntimeIntentPreparation):
            intent = self.command_execution.prepare_intent(
                intent, dt_s=dt_s, state=pre_step_state,
                provider=endpoint_pose_provider or self.endpoint_pose_provider,
            )
        return self.command_execution.execute(
            intent,
            dt_s=dt_s,
            pre_step_state=pre_step_state,
            source_state=source_state,
            pipeline=self,
        )

    def execute_motion_command(
        self,
        command: MotionCommand,
        *,
        pre_step_state: MuJoCoState,
        source_state: RuntimeInputSourceState,
    ) -> RuntimeInputSafetyResult:
        from xpotato_sim.runtime.execution.command_routes import (
            MotionCommandExecutionBinding,
        )

        if not isinstance(
            self.command_execution,
            MotionCommandExecutionBinding,
        ):
            raise TypeError(
                "selected command route does not accept a MotionCommand envelope"
            )
        return self.command_execution.execute_motion_command(
            command,
            pre_step_state=pre_step_state,
            source_state=source_state,
            pipeline=self,
        )

    async def run_once(self, dt_s: float | None = None) -> MuJoCoState:
        dt = self.config.dt_s if dt_s is None else dt_s
        frame = self.input_source.read_frame()
        if isinstance(self.input_source, ManagedInputSource):
            # live/viewerのhealthは取得元の正本。frameの省略値でactiveへ戻さない。
            source_state = reconcile_runtime_input_source_state(
                frame, self.input_source.current_health(), source_kind=frame.source
            )
            frame = annotate_raw_input_frame(frame, source_state)
        else:
            # 既存offline/replayの記録済み状態をinitial healthで上書きしない。
            source_state = build_runtime_input_source_state_from_metadata(
                frame.metadata, default_source_kind=frame.source
            )
        pre_step_state = self.simulator.snapshot()
        intent = self.map_input(frame, pre_step_state=pre_step_state)
        safety_result = self.execute_intent(
            intent,
            dt_s=dt,
            pre_step_state=pre_step_state,
            source_state=source_state,
        )
        command = safety_result.motion_command
        qpos_rejected = safety_result.qpos_feasibility_rejected
        self.simulator.step(dt)
        state = self.simulator.snapshot()
        state = replace(
            state,
            metadata=merge_runtime_metadata(
                state.metadata,
                self.state_metadata,
                authoritative_profile_metadata=self.robot_profile_metadata,
            ),
        )
        if qpos_rejected:
            state = MuJoCoState(
                frame_index=state.frame_index,
                time_s=state.time_s,
                qpos=state.qpos,
                qvel=state.qvel,
                bodies=state.bodies,
                sites=state.sites,
                target_position_m=state.target_position_m,
                metadata=merge_runtime_metadata(
                    state.metadata,
                    command.metadata,
                    {"endpoint_evaluation": None},
                    authoritative_profile_metadata=self.robot_profile_metadata,
                ),
            )
        await self.publisher.publish(state)
        return state
