"""解決済みMapping semanticをtyped Robot command providerへbindする境界。

routeはcommand型とprovider capabilityをside effect前に検証し、unsupported routeを
暗黙fallbackしない。実際のsimulation stepやtransportはこのmoduleのownerではない。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from math import isfinite

from xpotato_sim.motion.base import EndpointDeltaMotionGenerator, MotionGenerator
from xpotato_sim.runtime.control.viewer_motion_policy import build_viewer_local_motion_metadata
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from xpotato_sim.runtime.composition.robot_bundle import (
    RobotCommandSemanticProvider,
    EndpointCommandProvider, EndpointPoseProvider, EndpointPoseObservation,
)
from xpotato_sim.runtime.control.input_source_state import RuntimeInputSourceState
from xpotato_sim.runtime.experiment.contracts import (
    CommandSemanticsRoute,
    VersionedIdentity,
    robot_command_semantic_contract,
)
from xpotato_sim.runtime.safety.input_safety import (
    RuntimeInputSafetyResult,
    build_runtime_input_safety_result,
)
from xpotato_sim.schemas import (
    EndpointVelocityCommand,
    InputIntent,
    JointPositionCommand,
    MotionCommand,
    MuJoCoState,
)

if TYPE_CHECKING:
    from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline


@runtime_checkable
class CommandExecutionBinding(Protocol):
    """Control Mapping出力を1つのtyped Robot commandへ投影する実行契約。"""

    route_identity: VersionedIdentity
    control_semantics_identity: VersionedIdentity
    robot_command_semantics_identity: VersionedIdentity
    command_type: type
    requires_motion_generator: bool

    def execute(
        self,
        intent: InputIntent,
        *,
        dt_s: float,
        pre_step_state: MuJoCoState,
        source_state: RuntimeInputSourceState,
        pipeline: ControlMappedRuntimePipeline,
    ) -> RuntimeInputSafetyResult: ...


@runtime_checkable
class RouteMotionGeneratorFactory(Protocol):
    """resolved routeがRobot-owned generatorの構築を選ぶoptional capability。"""

    def build_motion_generator(self, provider: EndpointCommandProvider) -> MotionGenerator: ...


@runtime_checkable
class MappingRuntimeContextBinding(Protocol):
    """固定parameterから分離した同stepの観測contextをMappingへ渡す契約。"""

    mapping_context_parameters: frozenset[str]

    def mapping_parameters(self, parameters: Mapping[str, object], *, state: MuJoCoState,
                           provider: EndpointPoseProvider) -> Mapping[str, object]: ...


@runtime_checkable
class RuntimeIntentPreparation(Protocol):
    """実行入口によらずroute-ownedな座標解決を行う契約。"""

    def prepare_intent(self, intent: InputIntent, *, dt_s: float, state: MuJoCoState,
                       provider: EndpointPoseProvider | None) -> InputIntent: ...


def build_route_motion_generator(binding: CommandExecutionBinding,
                                 provider: EndpointCommandProvider | None,
                                 fallback: Callable[[], MotionGenerator]) -> MotionGenerator | None:
    """typed routeの構築契約を優先し、旧target/replayだけ明示fallbackを使う。"""
    if not binding.requires_motion_generator:
        return None
    if isinstance(binding, RouteMotionGeneratorFactory):
        if provider is None:
            raise ValueError("selected route requires an endpoint command provider")
        return binding.build_motion_generator(provider)
    return fallback()


@runtime_checkable
class MotionCommandExecutionBinding(CommandExecutionBinding, Protocol):
    """Mapping出力からMotionCommandを得るroute bindingの共通Protocol。"""

    def execute_motion_command(
        self,
        command: MotionCommand,
        *,
        pre_step_state: MuJoCoState,
        source_state: RuntimeInputSourceState,
        pipeline: ControlMappedRuntimePipeline,
    ) -> RuntimeInputSafetyResult: ...


@dataclass(frozen=True, slots=True)
class ResolvedCommandExecution:
    """route identityと検証済みexecution bindingの解決結果。"""

    route: CommandSemanticsRoute
    binding: CommandExecutionBinding

    def __post_init__(self) -> None:
        if not isinstance(self.binding, CommandExecutionBinding):
            raise TypeError(
                "resolved command execution requires a typed execution binding"
            )
        if (
            self.binding.route_identity != self.route.identity
            or self.binding.control_semantics_identity
            != self.route.control_semantics_identity
            or self.binding.robot_command_semantics_identity
            != self.route.robot_command_semantics_identity
        ):
            raise ValueError(
                "selected command route and execution binding identity mismatch"
            )
        semantic_contract = robot_command_semantic_contract(
            self.route.robot_command_semantics_identity
        )
        if (
            self.route.execution_strategy.command_type
            is not semantic_contract.command_type
            or self.binding.command_type is not semantic_contract.command_type
        ):
            raise TypeError(
                "selected command route/execution binding command type mismatch"
            )


def _validate_provider(
    provider: object,
    *,
    semantic_identity: VersionedIdentity,
    command_type: type,
) -> RobotCommandSemanticProvider:
    semantic_contract = robot_command_semantic_contract(semantic_identity)
    if command_type is not semantic_contract.command_type:
        raise TypeError(
            "command route execution strategy/semantic contract command type mismatch"
        )
    if not isinstance(provider, RobotCommandSemanticProvider):
        raise TypeError(
            "command route execution strategy requires a typed "
            "RobotCommandSemanticProvider"
        )
    if provider.command_semantics_identity != semantic_identity:
        raise ValueError(
            "command route execution strategy/Robot provider semantic mismatch"
        )
    if provider.command_type is not command_type:
        raise TypeError(
            "command route execution strategy/Robot provider command type mismatch"
        )
    return provider


def project_joint_position_command(
    command: MotionCommand,
) -> JointPositionCommand:
    """Project a validated runtime envelope onto the Robot command boundary."""

    if not isinstance(command, MotionCommand):
        raise TypeError(
            "joint-position projection requires a MotionCommand envelope"
        )
    if command.joint is None:
        raise ValueError(
            "joint_position_command/v1 requires MotionCommand.joint"
        )
    if command.joint.joint_velocities_rad_s:
        raise ValueError(
            "joint_position_command/v1 does not accept joint velocities"
        )
    return JointPositionCommand(
        timestamp_s=command.timestamp_s,
        joint_angles_rad=command.joint.joint_angles_rad,
    )


@dataclass(frozen=True, slots=True)
class JointPositionCommandExecutionBinding:
    """Mapping結果をRobot-owned qpos orderingのJointPositionCommandへ投影する。"""

    route_identity: VersionedIdentity
    control_semantics_identity: VersionedIdentity
    robot_command_semantics_identity: VersionedIdentity
    provider: RobotCommandSemanticProvider
    command_type: ClassVar[type] = JointPositionCommand
    requires_motion_generator: bool = True

    def execute(
        self,
        intent: InputIntent,
        *,
        dt_s: float,
        pre_step_state: MuJoCoState,
        source_state: RuntimeInputSourceState,
        pipeline: ControlMappedRuntimePipeline,
    ) -> RuntimeInputSafetyResult:
        motion_generator = pipeline.motion_generator
        if motion_generator is None:
            raise RuntimeError(
                "joint-position command execution requires a MotionGenerator"
            )
        set_current_qpos = getattr(motion_generator, "set_current_qpos_rad", None)
        if callable(set_current_qpos):
            set_current_qpos(tuple(pre_step_state.qpos))
        motion_command = motion_generator.update(intent, dt_s)
        return self.execute_motion_command(
            motion_command,
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
        safety_result = build_runtime_input_safety_result(
            command,
            source_state=source_state,
            current_state=pre_step_state,
            qpos_feasibility_guard=pipeline.qpos_feasibility_guard,
        )
        robot_command = project_joint_position_command(
            safety_result.motion_command
        )
        self.provider.execute(robot_command, backend=pipeline.simulator)
        record_motion_envelope = getattr(
            pipeline.simulator,
            "record_motion_command_envelope",
            None,
        )
        if callable(record_motion_envelope):
            record_motion_envelope(safety_result.motion_command)
        return safety_result


@dataclass(frozen=True, slots=True)
class LocalEndpointVelocityCommandExecutionBinding(JointPositionCommandExecutionBinding):
    """速度経路のgeneratorとworld/tool解決をsourceではなくrouteが所有する。"""

    def build_motion_generator(self, provider: EndpointCommandProvider) -> MotionGenerator:
        return provider.build_local_endpoint_motion_generator()

    def prepare_intent(self, intent: InputIntent, *, dt_s: float, state: MuJoCoState,
                       provider: EndpointPoseProvider | None) -> InputIntent:
        metadata = dict(intent.metadata)
        metadata["intent_kind"] = "local_endpoint_velocity"
        # tool姿勢は入力metadataではなく同stepのRobot providerをauthorityにする。
        metadata["current_tip_orientation_wxyz"] = None
        if provider is not None:
            observation = provider.observe_endpoint_pose(state)
            if not isinstance(observation, EndpointPoseObservation):
                raise TypeError("endpoint pose provider returned an invalid observation")
            metadata["current_tip_orientation_wxyz"] = observation.quaternion_wxyz
        return replace(intent, metadata=build_viewer_local_motion_metadata(metadata, dt_s=dt_s))


@dataclass(frozen=True, slots=True)
class EndpointDeltaCommandExecutionBinding(JointPositionCommandExecutionBinding):
    """位置増分/sampleを明示methodで実行し、観測contextを同じsnapshotへ結ぶ。"""

    mapping_context_parameters: ClassVar[frozenset[str]] = frozenset({"current_tip_position_m"})

    def build_motion_generator(self, provider: EndpointCommandProvider) -> MotionGenerator:
        generator = provider.build_local_endpoint_motion_generator()
        if not isinstance(generator, EndpointDeltaMotionGenerator):
            raise TypeError("endpoint delta route requires an explicit delta motion generator")
        return generator

    def mapping_parameters(self, parameters: Mapping[str, object], *, state: MuJoCoState,
                           provider: EndpointPoseProvider) -> Mapping[str, object]:
        observation = provider.observe_endpoint_pose(state)
        position = observation.position_m if isinstance(observation, EndpointPoseObservation) else None
        if position is None or len(position) != 3 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
            for value in position
        ):
            raise ValueError("measured endpoint context is unavailable or invalid")
        return {**parameters, "current_tip_position_m": tuple(position)}

    def execute(self, intent: InputIntent, *, dt_s: float, pre_step_state: MuJoCoState,
                source_state: RuntimeInputSourceState,
                pipeline: ControlMappedRuntimePipeline) -> RuntimeInputSafetyResult:
        generator = pipeline.motion_generator
        if not isinstance(generator, EndpointDeltaMotionGenerator):
            raise TypeError("endpoint delta route requires an explicit delta motion generator")
        if intent.metadata.get("control_frame", "world") != "world":
            raise ValueError("endpoint delta route requires world frame")
        generator.set_current_qpos_rad(tuple(pre_step_state.qpos))
        command = generator.update_delta(
            replace(intent, metadata={**intent.metadata, "control_frame": "world"}), dt_s
        )
        return self.execute_motion_command(command, pre_step_state=pre_step_state,
                                           source_state=source_state, pipeline=pipeline)


@dataclass(frozen=True, slots=True)
class NativeEndpointVelocityCommandExecutionBinding:
    """local frameのendpoint velocity commandを対応providerへ渡すbinding。

    velocity unitはm/s、providerはRobot command semanticに従いreject/holdを決める。
    """

    route_identity: VersionedIdentity
    control_semantics_identity: VersionedIdentity
    robot_command_semantics_identity: VersionedIdentity
    provider: RobotCommandSemanticProvider
    command_type: ClassVar[type] = EndpointVelocityCommand
    requires_motion_generator: bool = False

    def execute(
        self,
        intent: InputIntent,
        *,
        dt_s: float,
        pre_step_state: MuJoCoState,
        source_state: RuntimeInputSourceState,
        pipeline: ControlMappedRuntimePipeline,
    ) -> RuntimeInputSafetyResult:
        _ = dt_s
        _ = pre_step_state
        velocity_value = intent.metadata.get("local_endpoint_velocity_m_s")
        if not isinstance(velocity_value, (tuple, list)) or len(velocity_value) != 3:
            raise TypeError(
                "native endpoint-velocity execution requires "
                "local_endpoint_velocity_m_s"
            )
        velocity = tuple(velocity_value)
        frame_value = intent.metadata.get(
            "local_endpoint_velocity_frame",
            intent.metadata.get("control_frame"),
        )
        if not isinstance(frame_value, str) or not frame_value:
            raise TypeError(
                "native endpoint-velocity execution requires a velocity frame"
            )
        stale_reason = source_state.stale_reason
        if stale_reason is None and not source_state.source_active:
            stale_reason = "source_inactive"
        applied_velocity = (0.0, 0.0, 0.0) if stale_reason is not None else velocity
        command = EndpointVelocityCommand(
            timestamp_s=intent.timestamp_s,
            velocity_m_s=applied_velocity,
            frame=frame_value,
        )
        self.provider.execute(command, backend=pipeline.simulator)
        projection = MotionCommand(
            timestamp_s=intent.timestamp_s,
            metadata={
                **dict(intent.metadata),
                "command_route_execution": self.route_identity.canonical_id,
                "robot_command_semantics": (
                    self.robot_command_semantics_identity.canonical_id
                ),
                "endpoint_velocity_m_s": applied_velocity,
                "endpoint_velocity_frame": frame_value,
                "runtime_input_safety_applied": stale_reason is not None,
            },
        )
        return RuntimeInputSafetyResult(
            motion_command=projection,
            source_state=RuntimeInputSourceState(
                source_kind=source_state.source_kind,
                source_active=source_state.source_active,
                command_age_ms=source_state.command_age_ms,
                stale_reason=stale_reason,
            ),
            is_stale=stale_reason is not None,
            should_update_target_position_m=False,
            stale_reason=stale_reason,
            command_age_ms=source_state.command_age_ms,
        )


@dataclass(frozen=True, slots=True)
class JointPositionCommandRouteExecutionStrategy:
    """joint-position routeをcompatible providerへbindするdeclaration strategy。"""

    route_identity: VersionedIdentity
    control_semantics_identity: VersionedIdentity
    robot_command_semantics_identity: VersionedIdentity
    command_type: ClassVar[type] = JointPositionCommand

    def bind(self, provider: object) -> JointPositionCommandExecutionBinding:
        typed_provider = _validate_provider(
            provider,
            semantic_identity=self.robot_command_semantics_identity,
            command_type=JointPositionCommand,
        )
        return JointPositionCommandExecutionBinding(
            route_identity=self.route_identity,
            control_semantics_identity=self.control_semantics_identity,
            robot_command_semantics_identity=self.robot_command_semantics_identity,
            provider=typed_provider,
        )


@dataclass(frozen=True, slots=True)
class LocalEndpointVelocityCommandRouteExecutionStrategy(JointPositionCommandRouteExecutionStrategy):
    """joint-position providerへ結ぶ速度変換strategy。"""

    def bind(self, provider: object) -> LocalEndpointVelocityCommandExecutionBinding:
        typed = _validate_provider(provider, semantic_identity=self.robot_command_semantics_identity,
                                   command_type=JointPositionCommand)
        return LocalEndpointVelocityCommandExecutionBinding(
            self.route_identity, self.control_semantics_identity, self.robot_command_semantics_identity, typed)


@dataclass(frozen=True, slots=True)
class EndpointDeltaCommandRouteExecutionStrategy(JointPositionCommandRouteExecutionStrategy):
    """joint-position providerへ結ぶ位置増分変換strategy。"""

    def bind(self, provider: object) -> EndpointDeltaCommandExecutionBinding:
        typed = _validate_provider(provider, semantic_identity=self.robot_command_semantics_identity,
                                   command_type=JointPositionCommand)
        return EndpointDeltaCommandExecutionBinding(
            self.route_identity, self.control_semantics_identity, self.robot_command_semantics_identity, typed)


@dataclass(frozen=True, slots=True)
class NativeEndpointVelocityCommandRouteExecutionStrategy:
    """endpoint-velocity passthrough routeをcompatible providerへbindするstrategy。"""

    route_identity: VersionedIdentity
    control_semantics_identity: VersionedIdentity
    robot_command_semantics_identity: VersionedIdentity
    command_type: ClassVar[type] = EndpointVelocityCommand

    def bind(
        self, provider: object
    ) -> NativeEndpointVelocityCommandExecutionBinding:
        typed_provider = _validate_provider(
            provider,
            semantic_identity=self.robot_command_semantics_identity,
            command_type=EndpointVelocityCommand,
        )
        return NativeEndpointVelocityCommandExecutionBinding(
            route_identity=self.route_identity,
            control_semantics_identity=self.control_semantics_identity,
            robot_command_semantics_identity=self.robot_command_semantics_identity,
            provider=typed_provider,
        )


__all__ = [
    "CommandExecutionBinding",
    "RouteMotionGeneratorFactory",
    "MappingRuntimeContextBinding",
    "RuntimeIntentPreparation",
    "build_route_motion_generator",
    "EndpointDeltaCommandExecutionBinding",
    "EndpointDeltaCommandRouteExecutionStrategy",
    "LocalEndpointVelocityCommandExecutionBinding",
    "LocalEndpointVelocityCommandRouteExecutionStrategy",
    "JointPositionCommandExecutionBinding",
    "JointPositionCommandRouteExecutionStrategy",
    "MotionCommandExecutionBinding",
    "NativeEndpointVelocityCommandExecutionBinding",
    "NativeEndpointVelocityCommandRouteExecutionStrategy",
    "project_joint_position_command",
    "ResolvedCommandExecution",
]
