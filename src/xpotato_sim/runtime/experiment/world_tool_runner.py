"""Production 6軸compositionをsoftware-only MuJoCo trialへ接続する。

このmoduleはmanifest/readinessで固定された値だけを使用し、Task terminal
classificationやcanonical evidenceを再計算しない。experiment log、metric集計、
artifact出力は後続ownerへ残す。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from math import ceil
from types import MappingProxyType
from typing import TypeVar, cast

from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
    EndpointCommandProvider,
    EndpointPoseObservation,
    EndpointPoseProvider,
    QposFeasibilityProvider,
    ResetInitialStateProvider,
)
from xpotato_sim.runtime.composition.robot_profile import (
    robot_profile_runtime_metadata,
)
from xpotato_sim.runtime.control.input_source_state import (
    RuntimeInputSourceState,
    build_runtime_input_source_state_from_health,
    build_runtime_input_source_state_from_metadata,
)
from xpotato_sim.runtime.control.viewer_motion_policy import (
    CONTROL_FRAME_RESOLUTION_INVALID_DEFAULTED,
    build_viewer_local_motion_metadata,
)
from xpotato_sim.runtime.evaluation.manifest import (
    EvaluationConditionPairReadiness,
    EvaluationReadiness,
    FreezeRecord,
    SoftwareExecutionIdentity,
    build_evaluation_condition_pair_readiness,
    verify_freeze_identity,
)
from xpotato_sim.runtime.evaluation.r7_g_free_space import (
    build_r7_g_free_space_manifest_pair,
)
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.execution.command_routes import build_route_motion_generator
from xpotato_sim.runtime.experiment.composition import resolve_command_execution
from xpotato_sim.runtime.experiment.contracts import (
    EvidenceStatus,
    PluginAxis,
    PluginParameterOwner,
    TaskTerminalClassification,
    TaskTransition,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    EndpointReachMotionStatus,
    EndpointReachObservation,
)
from xpotato_sim.runtime.experiment.measured_state import (
    runtime_measurement_matches_frozen,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceMode,
    ManagedInputSource,
)
from xpotato_sim.runtime.safety.input_safety import RuntimeInputSafetyResult
from xpotato_sim.schemas import InputIntent, MuJoCoState, RawInputFrame


class ExperimentRunnerError(RuntimeError):
    """software-only experiment runnerのfail-closed error。"""


class ExperimentStopReason(str, Enum):
    """Task terminalまたは明示的step上限による有限停止理由。"""

    TASK_TERMINAL = "task_terminal"
    BOUNDED_STEP_LIMIT = "bounded_step_limit"


_ExecutionFact = TypeVar("_ExecutionFact")


def _immutable_execution_fact(value: _ExecutionFact) -> _ExecutionFact:
    """owner生成時のnested runtime valueをtrace用にdeep-freezeする。"""

    if isinstance(value, Mapping):
        return cast(
            _ExecutionFact,
            MappingProxyType(
                {
                    key: _immutable_execution_fact(item)
                    for key, item in value.items()
                }
            ),
        )
    if isinstance(value, (list, tuple)):
        return cast(
            _ExecutionFact,
            tuple(_immutable_execution_fact(item) for item in value),
        )
    if isinstance(value, (set, frozenset)):
        return cast(
            _ExecutionFact,
            frozenset(_immutable_execution_fact(item) for item in value),
        )
    if is_dataclass(value) and not isinstance(value, type):
        return cast(
            _ExecutionFact,
            replace(
                value,
                **{
                    item.name: _immutable_execution_fact(
                        getattr(value, item.name)
                    )
                    for item in fields(value)
                    if item.init
                },
            ),
        )
    return value


@dataclass(frozen=True, slots=True)
class ExperimentMotionStepTrace:
    """既存execution loopが生成した1 stepのowner facts。"""

    sample_index: int
    runtime_timestamp_s: float
    raw_frame: RawInputFrame
    intent: InputIntent
    safety_result: RuntimeInputSafetyResult
    pre_state: MuJoCoState
    pre_endpoint_world_m: tuple[float, float, float] | None
    post_state: MuJoCoState
    post_endpoint_world_m: tuple[float, float, float] | None
    task_observation: EndpointReachObservation


@dataclass(frozen=True, slots=True)
class ExperimentConditionExecutionResult:
    """1 conditionのTask-owned terminal結果を保持するin-memory boundary。"""

    condition_id: str
    requested_control_frame: str
    freeze_record: FreezeRecord
    evaluator_identities: tuple[VersionedIdentity, ...]
    transition: TaskTransition
    step_count: int
    final_elapsed_time_s: float
    stop_reason: ExperimentStopReason
    initial_measured_qpos_rad: tuple[float, ...]
    initial_measured_endpoint_world_m: tuple[float, float, float] | None
    initial_measured_tool_orientation_wxyz: tuple[float, float, float, float]
    final_measured_endpoint_world_m: tuple[float, float, float] | None
    motion_steps: tuple[ExperimentMotionStepTrace, ...]

    @property
    def classification(self) -> TaskTerminalClassification:
        """TaskTransitionのclassificationをそのまま公開する。"""

        return self.transition.classification


@dataclass(frozen=True, slots=True)
class WorldToolExperimentExecutionResult:
    """同じpair freezeから得たworld/toolの有限実行結果。"""

    pair_identity: str
    world: ExperimentConditionExecutionResult
    tool: ExperimentConditionExecutionResult


class _NullStatePublisher:
    async def publish(self, state: MuJoCoState) -> None:
        _ = state


@dataclass(slots=True)
class _AssembledCondition:
    readiness: EvaluationReadiness
    pipeline: ControlMappedRuntimePipeline
    endpoint_pose_provider: EndpointPoseProvider
    scene_provider: object
    scene: object


def _parameters_for_axis(
    readiness: EvaluationReadiness,
    axis: PluginAxis,
) -> Mapping[str, object]:
    manifest = readiness.manifest
    selections = {
        PluginAxis.ROBOT_BUNDLE: manifest.robot_bundle,
        PluginAxis.ENVIRONMENT: manifest.environment,
        PluginAxis.CONTROL_MAPPING: manifest.control_mapping,
        PluginAxis.TASK: manifest.task,
        PluginAxis.INPUT_SOURCE: manifest.input_source,
    }
    selection = selections.get(axis)
    if selection is None:
        raise ExperimentRunnerError(f"unsupported single-axis parameter lookup: {axis.value}")
    owner = PluginParameterOwner(axis, selection)
    matches = tuple(item.values for item in manifest.parameters if item.owner == owner)
    if len(matches) > 1:
        raise ExperimentRunnerError(f"duplicate {axis.value} parameter owner after readiness")
    return {} if not matches else matches[0]


def _source_state_from_frame(
    frame: RawInputFrame,
    *,
    health: InputSourceHealth,
    source_kind: str,
) -> RuntimeInputSourceState:
    source_state = build_runtime_input_source_state_from_health(
        health,
        source_kind=source_kind,
    )
    native_state = build_runtime_input_source_state_from_metadata(
        frame.metadata,
        default_source_kind=source_state.source_kind,
    )
    for field, key in (
        ("source_active", "source_active"),
        ("command_age_ms", "command_age_ms"),
        ("stale_reason", "stale_reason"),
    ):
        if key in frame.metadata and getattr(native_state, field) != getattr(
            source_state, field
        ):
            raise ExperimentRunnerError(
                "input source frame metadata and typed health disagree"
            )
    return source_state


def _bounded_step_count(readiness: EvaluationReadiness) -> int:
    manifest = readiness.manifest
    steps = ceil(manifest.timeout_s / manifest.cadence_s)
    if steps < 1:
        raise ExperimentRunnerError("manifest cadence/timeout produced no executable step")
    return steps


def _assemble_condition(readiness: EvaluationReadiness) -> _AssembledCondition:
    verify_freeze_identity(readiness.freeze_record, readiness.manifest, readiness)
    composition = readiness.composition
    manifest = readiness.manifest
    bundle = composition.robot_bundle

    environment_parameters = _parameters_for_axis(readiness, PluginAxis.ENVIRONMENT)
    input_parameters = _parameters_for_axis(readiness, PluginAxis.INPUT_SOURCE)
    mapping_parameters = _parameters_for_axis(readiness, PluginAxis.CONTROL_MAPPING)
    scene_provider = composition.environment.scene_provider
    scene = scene_provider.compose_scene(environment_parameters)

    reader = composition.input_source.create_runtime_reader(input_parameters)
    if composition.input_source.mode not in {InputSourceMode.OFFLINE, InputSourceMode.REPLAY}:
        raise ExperimentRunnerError(
            "software-only experiment runner requires an offline or replay Input Source"
        )
    if isinstance(reader, ManagedInputSource):
        raise ExperimentRunnerError(
            "software-only experiment runner does not start managed Input Sources"
        )

    initial_state_provider = bundle.provider(RESET_INITIAL_STATE_V1)
    endpoint_pose_provider = bundle.provider(ENDPOINT_POSE_V1)
    qpos_feasibility_provider = bundle.provider(QPOS_FEASIBILITY_V1)
    if not isinstance(initial_state_provider, ResetInitialStateProvider):
        raise ExperimentRunnerError("Robot Bundle lacks reset_initial_state/v1")
    if not isinstance(endpoint_pose_provider, EndpointPoseProvider):
        raise ExperimentRunnerError("Robot Bundle lacks endpoint_pose/v1")
    if not isinstance(qpos_feasibility_provider, QposFeasibilityProvider):
        raise ExperimentRunnerError("Robot Bundle lacks qpos_feasibility/v1")

    command_execution = resolve_command_execution(
        composition.control_mapping,
        bundle,
        composition.resolved_command_semantics_route.identity,
    )
    endpoint_command_provider: EndpointCommandProvider | None = None
    if command_execution.binding.requires_motion_generator:
        candidate = bundle.provider(ENDPOINT_COMMAND_V1)
        if not isinstance(candidate, EndpointCommandProvider):
            raise ExperimentRunnerError("Robot Bundle lacks endpoint_command/v1")
        endpoint_command_provider = candidate

    initial_state = initial_state_provider.resolve_initial_state()
    if initial_state.source_kind != "named_keyframe":
        raise ExperimentRunnerError(
            "production experiment requires a named-keyframe initial state"
        )
    if initial_state.source_id != manifest.initial_keyframe_name:
        raise ExperimentRunnerError(
            "resolved Robot initial state changed after readiness"
        )

    plugin = bundle.runtime_plugin
    simulator = plugin.build_simulator(
        model_path=None,
        initial_keyframe_name=initial_state.source_id,
    )
    plugin.validate_model(simulator.model)
    runtime_config = RuntimeConfig(
        dt_s=manifest.cadence_s,
        robot_profile_id=manifest.robot_bundle.plugin_id,
        robot_logical_version=manifest.robot_bundle.contract_version,
    )
    pipeline = ControlMappedRuntimePipeline(
        config=runtime_config,
        input_source=reader,
        control_mapping=composition.control_mapping,
        control_mapping_parameters=mapping_parameters,
        mapping_input_adapter=composition.input_source.mapping_input_adapter,
        motion_generator=build_route_motion_generator(
            command_execution.binding, endpoint_command_provider,
            lambda: endpoint_command_provider.build_local_endpoint_motion_generator(),
        ),
        endpoint_pose_provider=endpoint_pose_provider,
        simulator=simulator,
        publisher=_NullStatePublisher(),
        qpos_feasibility_guard=qpos_feasibility_provider.build_guard(
            model=simulator.model,
            config_path=runtime_config.joint_limit_config_path,
        ),
        command_semantics_route=command_execution.route,
        command_execution=command_execution.binding,
        robot_profile_metadata=robot_profile_runtime_metadata(bundle.profile),
    )
    return _AssembledCondition(
        readiness=readiness,
        pipeline=pipeline,
        endpoint_pose_provider=endpoint_pose_provider,
        scene_provider=scene_provider,
        scene=scene,
    )


def _measurement_observation(
    provider: EndpointPoseProvider,
    state: MuJoCoState,
    *,
    elapsed_time_s: float,
    motion_status: EndpointReachMotionStatus = EndpointReachMotionStatus.NOMINAL,
    reason: str | None = None,
    observed_pose: EndpointPoseObservation | None = None,
) -> EndpointReachObservation:
    try:
        observation = (
            provider.observe_endpoint_pose(state)
            if observed_pose is None
            else observed_pose
        )
        if not isinstance(observation, EndpointPoseObservation):
            raise TypeError("endpoint_pose/v1 returned an invalid observation")
        if observation.position_m is None:
            raise LookupError("endpoint_pose/v1 position is unavailable")
    except LookupError as exc:
        return EndpointReachObservation(
            elapsed_time_s=elapsed_time_s,
            position_world_m=None,
            measurement_status=EvidenceStatus.UNAVAILABLE,
            motion_status=EndpointReachMotionStatus.TECHNICAL_INVALID,
            reason=str(exc) or "endpoint measurement unavailable",
        )
    except (TypeError, ValueError) as exc:
        return EndpointReachObservation(
            elapsed_time_s=elapsed_time_s,
            position_world_m=None,
            measurement_status=EvidenceStatus.INVALID,
            motion_status=EndpointReachMotionStatus.TECHNICAL_INVALID,
            reason=str(exc) or "endpoint measurement invalid",
        )
    try:
        return EndpointReachObservation(
            elapsed_time_s=elapsed_time_s,
            position_world_m=observation.position_m,
            motion_status=motion_status,
            reason=reason,
        )
    except (TypeError, ValueError) as exc:
        return EndpointReachObservation(
            elapsed_time_s=elapsed_time_s,
            position_world_m=None,
            measurement_status=EvidenceStatus.INVALID,
            motion_status=EndpointReachMotionStatus.TECHNICAL_INVALID,
            reason=str(exc) or "endpoint measurement invalid",
        )


def _validate_measured_initial_state(
    readiness: EvaluationReadiness,
    state: MuJoCoState,
    observation: EndpointPoseObservation,
) -> None:
    """reset後のactual qpos / orientationをfrozen manifestへ照合する。"""

    expected_qpos = readiness.manifest.initial_qpos_rad
    if len(state.qpos) < len(expected_qpos):
        raise ExperimentRunnerError(
            "reset state qpos dimension is smaller than the frozen initial state"
        )
    actual_qpos = state.qpos[: len(expected_qpos)]
    if not runtime_measurement_matches_frozen(actual_qpos, expected_qpos):
        raise ExperimentRunnerError("reset state qpos does not match the frozen manifest")

    actual_orientation = observation.quaternion_wxyz
    expected_orientation = readiness.manifest.initial_tool_orientation_wxyz
    if actual_orientation is None or len(actual_orientation) != 4:
        raise ExperimentRunnerError("reset endpoint orientation is unavailable")
    if not runtime_measurement_matches_frozen(
        actual_orientation,
        expected_orientation,
        allow_quaternion_sign_equivalence=True,
    ):
        raise ExperimentRunnerError(
            "reset endpoint orientation does not match the frozen manifest"
        )


def _project_motion_status(
    result: RuntimeInputSafetyResult,
) -> tuple[EndpointReachMotionStatus, str | None]:
    metadata = result.motion_command.metadata
    if result.is_stale:
        return (
            EndpointReachMotionStatus.STALE,
            result.stale_reason or "runtime input became stale",
        )
    if metadata.get("control_frame_resolution_status") == CONTROL_FRAME_RESOLUTION_INVALID_DEFAULTED:
        return (
            EndpointReachMotionStatus.TECHNICAL_INVALID,
            str(
                metadata.get(
                    "control_frame_resolution_reason",
                    "invalid control frame was defaulted",
                )
            ),
        )
    if result.qpos_feasibility_rejected:
        return (
            EndpointReachMotionStatus.REJECTED,
            str(metadata.get("qpos_rejection_reason", "qpos feasibility rejected")),
        )
    if metadata.get("target_rejected") is True:
        return (
            EndpointReachMotionStatus.REJECTED,
            str(metadata.get("target_rejection_reason", "endpoint target rejected")),
        )
    status = metadata.get("motion_status")
    reason_value = metadata.get("motion_rejection_reason")
    reason = reason_value if isinstance(reason_value, str) and reason_value else None
    if status in {"accepted", "scaled"}:
        return EndpointReachMotionStatus.NOMINAL, None
    if status == "held":
        return EndpointReachMotionStatus.HELD, reason or "motion command held"
    if status == "rejected":
        return EndpointReachMotionStatus.REJECTED, reason or "motion command rejected"
    if status == "reset":
        return EndpointReachMotionStatus.RESET, reason or "runtime reset during trial"
    return (
        EndpointReachMotionStatus.TECHNICAL_INVALID,
        reason or f"unsupported runtime motion status: {status!r}",
    )


def _technical_invalid_observation(
    *, elapsed_time_s: float, reason: str
) -> EndpointReachObservation:
    return EndpointReachObservation(
        elapsed_time_s=elapsed_time_s,
        position_world_m=None,
        measurement_status=EvidenceStatus.INVALID,
        motion_status=EndpointReachMotionStatus.TECHNICAL_INVALID,
        reason=reason,
    )


def run_experiment_condition(
    readiness: EvaluationReadiness,
) -> ExperimentConditionExecutionResult:
    """1つのfrozen conditionをdeterministic simulation timeで有限実行する。"""

    if not isinstance(readiness, EvaluationReadiness):
        raise TypeError("run_experiment_condition requires EvaluationReadiness")
    assembled = _assemble_condition(readiness)
    pipeline = assembled.pipeline
    manifest = readiness.manifest
    task_binding = readiness.task_execution_binding
    task_state = task_binding.initial_state()

    try:
        assembled.scene_provider.reset_scene(assembled.scene)
        pipeline.simulator.reset()
    except (TypeError, ValueError, RuntimeError) as exc:
        raise ExperimentRunnerError(f"condition reset failed: {exc}") from exc

    initial_state = pipeline.simulator.snapshot()
    try:
        initial_pose = assembled.endpoint_pose_provider.observe_endpoint_pose(
            initial_state
        )
        if not isinstance(initial_pose, EndpointPoseObservation):
            raise TypeError("endpoint_pose/v1 returned an invalid observation")
        _validate_measured_initial_state(readiness, initial_state, initial_pose)
    except ExperimentRunnerError:
        raise
    except (TypeError, ValueError, RuntimeError) as exc:
        raise ExperimentRunnerError(
            f"initial reset verification failed: {exc}"
        ) from exc
    initial_observation = _measurement_observation(
        assembled.endpoint_pose_provider,
        initial_state,
        elapsed_time_s=0.0,
        observed_pose=initial_pose,
    )
    transition = task_binding.advance(task_state, initial_observation)
    initial_endpoint = initial_observation.position_world_m
    final_endpoint = initial_endpoint
    final_elapsed = 0.0
    executed_steps = 0
    stop_reason = ExperimentStopReason.TASK_TERMINAL
    motion_steps: list[ExperimentMotionStepTrace] = []

    for step_index in range(_bounded_step_count(readiness)):
        if transition.classification is not TaskTerminalClassification.RUNNING:
            break
        elapsed = (step_index + 1) * manifest.cadence_s
        executed_steps = step_index + 1
        final_elapsed = elapsed
        try:
            raw_frame = pipeline.input_source.read_frame()
            if not isinstance(raw_frame, RawInputFrame):
                raise TypeError("Input Source returned an invalid RawInputFrame")
            health = pipeline.input_source.current_health()
            source_state = _source_state_from_frame(
                raw_frame,
                health=health,
                source_kind=composition_source_kind(readiness),
            )
            mapping_input = (
                pipeline.mapping_input_adapter(raw_frame)
                if pipeline.mapping_input_adapter is not None
                else raw_frame
            )
            intent = pipeline.control_mapping.strategy.map_input(
                mapping_input,
                pipeline.control_mapping_parameters,
            )
            if not isinstance(intent, InputIntent):
                raise TypeError("Control Mapping returned an invalid InputIntent")
            pre_state = pipeline.simulator.snapshot()
            pre_pose = assembled.endpoint_pose_provider.observe_endpoint_pose(pre_state)
            if not isinstance(pre_pose, EndpointPoseObservation):
                raise TypeError("endpoint_pose/v1 returned an invalid observation")
            motion_metadata = {
                **dict(raw_frame.metadata),
                **dict(intent.metadata),
                "current_tip_orientation_wxyz": pre_pose.quaternion_wxyz,
            }
            motion_intent = replace(
                intent,
                metadata=build_viewer_local_motion_metadata(
                    motion_metadata,
                    dt_s=manifest.cadence_s,
                ),
            )
            safety_result = pipeline.execute_intent(
                motion_intent,
                dt_s=manifest.cadence_s,
                pre_step_state=pre_state,
                source_state=source_state,
            )
            if not isinstance(safety_result, RuntimeInputSafetyResult):
                raise TypeError("command route returned an invalid safety result")
            pipeline.simulator.step(manifest.cadence_s)
            post_state = pipeline.simulator.snapshot()
            motion_status, reason = _project_motion_status(safety_result)
            task_observation = _measurement_observation(
                assembled.endpoint_pose_provider,
                post_state,
                elapsed_time_s=elapsed,
                motion_status=motion_status,
                reason=reason,
            )
            motion_steps.append(
                ExperimentMotionStepTrace(
                    sample_index=step_index,
                    runtime_timestamp_s=elapsed,
                    raw_frame=_immutable_execution_fact(raw_frame),
                    intent=_immutable_execution_fact(motion_intent),
                    safety_result=_immutable_execution_fact(safety_result),
                    pre_state=_immutable_execution_fact(pre_state),
                    pre_endpoint_world_m=pre_pose.position_m,
                    post_state=_immutable_execution_fact(post_state),
                    post_endpoint_world_m=task_observation.position_world_m,
                    task_observation=_immutable_execution_fact(task_observation),
                )
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            task_observation = _technical_invalid_observation(
                elapsed_time_s=elapsed,
                reason=f"runtime execution failed closed: {exc}",
            )
        transition = task_binding.advance(transition.state, task_observation)
        if task_observation.position_world_m is not None:
            final_endpoint = task_observation.position_world_m
    else:
        if transition.classification is TaskTerminalClassification.RUNNING:
            stop_reason = ExperimentStopReason.BOUNDED_STEP_LIMIT

    evaluator_identities = tuple(
        evaluator.identity for evaluator in readiness.composition.evaluators
    )
    return ExperimentConditionExecutionResult(
        condition_id=manifest.condition_id,
        requested_control_frame=manifest.requested_control_frame,
        freeze_record=readiness.freeze_record,
        evaluator_identities=evaluator_identities,
        transition=transition,
        step_count=executed_steps,
        final_elapsed_time_s=final_elapsed,
        stop_reason=stop_reason,
        initial_measured_qpos_rad=tuple(initial_state.qpos),
        initial_measured_endpoint_world_m=initial_endpoint,
        initial_measured_tool_orientation_wxyz=tuple(initial_pose.quaternion_wxyz),
        final_measured_endpoint_world_m=final_endpoint,
        motion_steps=tuple(motion_steps),
    )


def composition_source_kind(readiness: EvaluationReadiness) -> str:
    """resolved Input Source identityをruntime health projectionへ渡す。"""

    return readiness.composition.input_source.identity.name


def run_evaluation_condition_pair(
    readiness: EvaluationConditionPairReadiness,
) -> WorldToolExperimentExecutionResult:
    """validated pairをcondition orderどおりworld/toolへ実行する。"""

    if not isinstance(readiness, EvaluationConditionPairReadiness):
        raise TypeError(
            "run_evaluation_condition_pair requires EvaluationConditionPairReadiness"
        )
    world = run_experiment_condition(readiness.world)
    tool = run_experiment_condition(readiness.tool)
    return WorldToolExperimentExecutionResult(
        pair_identity=readiness.pair_identity,
        world=world,
        tool=tool,
    )


def run_r7_g_world_tool_experiment(
    *,
    manifest_software_revision_identity: str,
    execution_identity: SoftwareExecutionIdentity,
) -> WorldToolExperimentExecutionResult:
    """canonical production manifest pairを構築・freeze・実行する。"""

    if not isinstance(execution_identity, SoftwareExecutionIdentity):
        raise TypeError("execution_identity must use SoftwareExecutionIdentity")
    pair = build_r7_g_free_space_manifest_pair(
        software_revision_identity=manifest_software_revision_identity,
    )
    readiness = build_evaluation_condition_pair_readiness(
        pair,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=execution_identity,
    )
    return run_evaluation_condition_pair(readiness)


__all__ = [
    "ExperimentConditionExecutionResult",
    "ExperimentMotionStepTrace",
    "ExperimentRunnerError",
    "ExperimentStopReason",
    "WorldToolExperimentExecutionResult",
    "run_evaluation_condition_pair",
    "run_experiment_condition",
    "run_r7_g_world_tool_experiment",
]
