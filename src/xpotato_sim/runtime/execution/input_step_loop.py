from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic

from xpotato_sim.plugins.input_sources.viewer import (
    DEFAULT_VIEWER_SAFE_ENDPOINT_M,
    ViewerInputSource,
    ViewerManagedInputSourceReader,
)
from xpotato_sim.plugins.robots.catalog import (
    RobotCatalog,
    resolve_robot_bundle,
    resolve_robot_profile,
)
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline
from xpotato_sim.runtime.control.input_step_diagnostics import (
    PostStepMeasurement,
    input_signal_display_projection,
    annotate_runtime_input_state,
    measure_post_step_endpoint,
)
from xpotato_sim.runtime.control.input_source_selection import (
    RuntimeInputSourceSelection,
)
from xpotato_sim.runtime.control.input_source_state import (
    annotate_raw_input_frame,
    build_runtime_input_source_state_from_metadata,
    reconcile_runtime_input_source_state,
)
from xpotato_sim.runtime.experiment.input_source import (
    ManagedInputSource,
    InputSourceMappingAdapterContract,
    ViewerBridgeRuntimeCapability,
    ViewerEndpointRebaseCapability,
)
from xpotato_sim.runtime.experiment.contracts import (
    CommandSemanticsRoute,
    ControlMappingPlugin,
)
from xpotato_sim.runtime.execution.live_timing import (
    AbsoluteDeadlinePacer,
    LiveRuntimeTimingMetrics,
)
from xpotato_sim.runtime.execution.command_routes import CommandExecutionBinding
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    EndpointCommandProvider,
    EndpointPoseProvider,
    QposFeasibilityProvider,
    RobotBundle,
)
from xpotato_sim.runtime.composition.robot_profile_metadata import merge_runtime_metadata
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.execution.command_routes import MappingRuntimeContextBinding
from xpotato_sim.runtime.execution.input_source_adapters import (
    RuntimeInputSourceExecutionAdapter,
)
from xpotato_sim.runtime.composition.replay_mujoco_pipeline import build_replay_mujoco_pipeline
from xpotato_sim.schemas import InputIntent, MotionCommand, MuJoCoState, RawInputFrame
from xpotato_sim.transport import StatePublisher

VIEWER_ENDPOINT_CONTINUITY_THRESHOLD_RAD = 0.2


class _InputLoopStatePublisher:
    """Retain the latest local state when no external publisher is configured."""

    def __init__(self) -> None:
        self.last_state: MuJoCoState | None = None

    async def publish(self, state: MuJoCoState) -> None:
        self.last_state = state


@dataclass(frozen=True, slots=True)
class RuntimeInputSourceStepLoopPlan:
    selection: RuntimeInputSourceSelection
    pipeline: ControlMappedRuntimePipeline
    annotate_target_position_m: bool
    endpoint_pose_provider: EndpointPoseProvider
    endpoint_command_provider: EndpointCommandProvider | None
    qpos_feasibility_provider: QposFeasibilityProvider
    endpoint_site_name: str | None
    execution_adapter: RuntimeInputSourceExecutionAdapter
    control_mapping: ControlMappingPlugin
    command_semantics_route: CommandSemanticsRoute
    command_execution: CommandExecutionBinding
    control_mapping_parameters: Mapping[str, object] = field(default_factory=dict)
    mapping_input_adapter: InputSourceMappingAdapterContract | None = None
    viewer_bridge_capability: ViewerBridgeRuntimeCapability | None = None

    def __post_init__(self) -> None:
        if (
            self.command_execution.route_identity
            != self.command_semantics_route.identity
            or self.command_execution.control_semantics_identity
            != self.command_semantics_route.control_semantics_identity
            or self.command_execution.robot_command_semantics_identity
            != self.command_semantics_route.robot_command_semantics_identity
        ):
            raise ValueError(
                "runtime plan command route/execution binding mismatch"
            )
        if (
            self.pipeline.command_semantics_route
            != self.command_semantics_route
            or self.pipeline.command_execution is not self.command_execution
        ):
            raise ValueError(
                "runtime plan and pipeline command execution binding mismatch"
            )


@dataclass(frozen=True, slots=True)
class RuntimeInputSourceStepLoopRecord:
    frame: RawInputFrame
    intent: InputIntent
    motion_command: MotionCommand
    state: MuJoCoState


def _resolve_model_path(
    *, model_path: str | Path | None, config: RuntimeConfig, robot_bundle: RobotBundle
) -> Path | None:
    if model_path is not None:
        return Path(model_path)
    if config.mujoco_model_path is not None:
        return config.mujoco_model_path
    return None


def _coerce_viewer_endpoint_m(value: object) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return DEFAULT_VIEWER_SAFE_ENDPOINT_M

    endpoint_m = tuple(float(component) for component in value)
    if len(endpoint_m) != 3:
        return DEFAULT_VIEWER_SAFE_ENDPOINT_M

    return endpoint_m


def _extract_current_endpoint_m(
    pipeline: ControlMappedRuntimePipeline, provider: EndpointPoseProvider
) -> tuple[float, float, float] | None:
    return provider.observe_endpoint_pose(pipeline.simulator.snapshot()).position_m


def build_runtime_input_source_step_loop_plan(
    selection: RuntimeInputSourceSelection,
    *,
    config: RuntimeConfig | None = None,
    publisher: StatePublisher | None = None,
    model_path: str | Path | None = None,
    viewer_clock: Callable[[], float] | None = None,
    viewer_input_source: ViewerInputSource | None = None,
    robot_catalog: RobotCatalog | None = None,
) -> RuntimeInputSourceStepLoopPlan:
    runtime_config = RuntimeConfig(robot_profile_id="fast_arm") if config is None else config
    execution_adapter = selection.execution_adapter
    if execution_adapter is None:
        raise ValueError(
            "plugin-backed runtime input source selection requires an execution adapter"
        )
    if selection.control_mapping is None:
        raise ValueError(
            "plugin-backed runtime input source selection requires a Control Mapping Plugin"
        )
    if runtime_config.robot_profile_id is None:
        raise ValueError("production input step-loop requires robot_profile_id")
    robot_selection = runtime_config.robot_selection
    assert robot_selection is not None
    if robot_catalog is None:
        profile = resolve_robot_profile(
            runtime_config.robot_profile_id,
            robot_logical_version=runtime_config.robot_logical_version,
        )
        robot_bundle = resolve_robot_bundle(
            runtime_config.robot_profile_id,
            robot_logical_version=runtime_config.robot_logical_version,
        )
    else:
        profile = robot_catalog.resolve_profile(robot_selection)
        robot_bundle = robot_catalog.resolve_bundle(robot_selection)
    if robot_bundle.profile is not profile:
        raise ValueError("Robot Bundle/profile catalog consistency mismatch")
    selected_command_semantics_route = (
        selection.control_mapping.resolve_command_semantics_route(
            selection.command_semantics_route_selection
        )
    )
    if (
        selection.resolved_command_semantics_route is not None
        and selection.resolved_command_semantics_route
        != selected_command_semantics_route
    ):
        raise ValueError("runtime command semantics selection changed after source readiness")
    endpoint_pose_provider = robot_bundle.provider(ENDPOINT_POSE_V1)
    qpos_feasibility_provider = robot_bundle.provider(QPOS_FEASIBILITY_V1)
    assert isinstance(endpoint_pose_provider, EndpointPoseProvider)
    assert isinstance(qpos_feasibility_provider, QposFeasibilityProvider)
    resolved_model_path = _resolve_model_path(
        model_path=model_path, config=runtime_config, robot_bundle=robot_bundle
    )
    if viewer_input_source is not None and not execution_adapter.uses_viewer_endpoint_compatibility:
        raise ValueError(
            "viewer_input_source can only be supplied for the viewer endpoint compatibility adapter"
        )

    if execution_adapter.annotates_target_position and not execution_adapter.uses_viewer_endpoint_compatibility:
        if selection.control_mapping.runtime_context_parameters and selection.runtime_reader is None:
            raise ValueError("measured-endpoint input requires an explicit managed reader")
        pipeline = build_concrete_mujoco_pipeline(
            frames=selection.frames,
            config=runtime_config,
            model_path=resolved_model_path,
            loop=selection.loop,
            publisher=publisher if publisher is not None else _InputLoopStatePublisher(),
            control_mapping=selection.control_mapping,
            control_mapping_parameters=selection.control_mapping_parameters,
            mapping_input_adapter=selection.mapping_input_adapter,
            robot_catalog=robot_catalog,
            command_semantics_route_selection=selected_command_semantics_route.identity,
            input_source=(
                selection.runtime_reader
                if selection.control_mapping.runtime_context_parameters
                else None
            ),
        )
        if selection.runtime_reader is not None:
            pipeline.input_source = selection.runtime_reader
        endpoint_command_provider = (
            robot_bundle.provider(ENDPOINT_COMMAND_V1)
            if pipeline.command_execution.requires_motion_generator
            else None
        )
        if endpoint_command_provider is not None:
            assert isinstance(endpoint_command_provider, EndpointCommandProvider)
        return RuntimeInputSourceStepLoopPlan(
            selection=selection,
            pipeline=pipeline,
            annotate_target_position_m=True,
            endpoint_pose_provider=endpoint_pose_provider,
            endpoint_command_provider=endpoint_command_provider,
            qpos_feasibility_provider=qpos_feasibility_provider,
            endpoint_site_name=robot_bundle.profile.endpoint.site_name,
            command_semantics_route=pipeline.command_semantics_route,
            command_execution=pipeline.command_execution,
            execution_adapter=execution_adapter,
            control_mapping=selection.control_mapping,
            control_mapping_parameters=selection.control_mapping_parameters,
            mapping_input_adapter=selection.mapping_input_adapter,
        )

    if execution_adapter.uses_replay_pipeline:
        pipeline = build_replay_mujoco_pipeline(
            frames=selection.frames,
            config=runtime_config,
            model_path=resolved_model_path,
            loop=selection.loop,
            publisher=publisher,
            control_mapping=selection.control_mapping,
            control_mapping_parameters=selection.control_mapping_parameters,
            mapping_input_adapter=selection.mapping_input_adapter,
            robot_bundle=robot_bundle,
            command_semantics_route_selection=selected_command_semantics_route.identity,
        )
        if selection.runtime_reader is not None:
            pipeline.input_source = selection.runtime_reader
        endpoint_command_provider = (
            robot_bundle.provider(ENDPOINT_COMMAND_V1)
            if pipeline.command_execution.requires_motion_generator
            else None
        )
        if endpoint_command_provider is not None:
            assert isinstance(endpoint_command_provider, EndpointCommandProvider)
        return RuntimeInputSourceStepLoopPlan(
            selection=selection,
            pipeline=pipeline,
            annotate_target_position_m=False,
            endpoint_pose_provider=endpoint_pose_provider,
            endpoint_command_provider=endpoint_command_provider,
            qpos_feasibility_provider=qpos_feasibility_provider,
            endpoint_site_name=robot_bundle.profile.endpoint.site_name,
            command_semantics_route=pipeline.command_semantics_route,
            command_execution=pipeline.command_execution,
            execution_adapter=execution_adapter,
            control_mapping=selection.control_mapping,
            control_mapping_parameters=selection.control_mapping_parameters,
            mapping_input_adapter=selection.mapping_input_adapter,
        )

    if execution_adapter.uses_viewer_endpoint_compatibility:
        viewer_capability: ViewerBridgeRuntimeCapability | None = (
            viewer_input_source or selection.viewer_bridge_capability
        )
        if viewer_capability is None and selection.plugin_selection is not None:
            raise ValueError("plugin-backed viewer input source is missing its runtime bridge capability")
        if (
            viewer_clock is not None
            and viewer_input_source is None
            and selection.plugin_selection is not None
        ):
            assert viewer_capability is not None
            viewer_capability.rebind_clock(viewer_clock)
        pipeline_input_source = (
            ViewerManagedInputSourceReader(viewer_input_source)
            if viewer_input_source is not None
            else selection.runtime_reader
        )
        if pipeline_input_source is None:
            initial_endpoint_m = _coerce_viewer_endpoint_m(
                selection.initial_metadata.get(
                    "desired_endpoint_m",
                    selection.initial_metadata.get("target_position_m"),
                )
            )
            fallback_viewer_source = ViewerInputSource(
                clock=viewer_clock if viewer_clock is not None else monotonic,
                initial_endpoint_m=initial_endpoint_m,
            )
            pipeline_input_source = ViewerManagedInputSourceReader(
                fallback_viewer_source
            )
            viewer_capability = fallback_viewer_source

        if viewer_capability is None:
            raise ValueError("viewer input source is missing its runtime bridge capability")

        pipeline = build_concrete_mujoco_pipeline(
            frames=selection.frames,
            config=runtime_config,
            model_path=resolved_model_path,
            loop=selection.loop,
            publisher=publisher if publisher is not None else _InputLoopStatePublisher(),
            discontinuity_threshold_rad=VIEWER_ENDPOINT_CONTINUITY_THRESHOLD_RAD,
            discontinuity_threshold_label="viewer endpoint continuity threshold",
            control_mapping=selection.control_mapping,
            control_mapping_parameters=selection.control_mapping_parameters,
            mapping_input_adapter=selection.mapping_input_adapter,
            robot_catalog=robot_catalog,
            command_semantics_route_selection=selected_command_semantics_route.identity,
        )
        pipeline.input_source = pipeline_input_source
        endpoint_command_provider = (
            robot_bundle.provider(ENDPOINT_COMMAND_V1)
            if pipeline.command_execution.requires_motion_generator
            else None
        )
        if endpoint_command_provider is not None:
            assert isinstance(endpoint_command_provider, EndpointCommandProvider)
        initial_tip_site_position_m = _extract_current_endpoint_m(
            pipeline, endpoint_pose_provider
        )
        if initial_tip_site_position_m is not None:
            _sync_viewer_input_source_endpoint(
                viewer_capability,
                endpoint_m=initial_tip_site_position_m,
            )

        return RuntimeInputSourceStepLoopPlan(
            selection=selection,
            pipeline=pipeline,
            annotate_target_position_m=True,
            endpoint_pose_provider=endpoint_pose_provider,
            endpoint_command_provider=endpoint_command_provider,
            qpos_feasibility_provider=qpos_feasibility_provider,
            endpoint_site_name=robot_bundle.profile.endpoint.site_name,
            command_semantics_route=pipeline.command_semantics_route,
            command_execution=pipeline.command_execution,
            execution_adapter=execution_adapter,
            control_mapping=selection.control_mapping,
            control_mapping_parameters=selection.control_mapping_parameters,
            mapping_input_adapter=selection.mapping_input_adapter,
            viewer_bridge_capability=viewer_capability,
        )

    raise ValueError(f"unsupported input source execution adapter: {execution_adapter!r}")


def _sync_viewer_input_source_endpoint(
    capability: ViewerEndpointRebaseCapability,
    *,
    endpoint_m: tuple[float, float, float] | None,
) -> None:
    if endpoint_m is None:
        endpoint_m = DEFAULT_VIEWER_SAFE_ENDPOINT_M

    capability.rebase_current_endpoint_m(endpoint_m)


async def run_runtime_input_source_step_loop(
    plan: RuntimeInputSourceStepLoopPlan,
    *,
    steps: int,
    dt_s: float | None = None,
    interval_s: float = 0.0,
    pacer: AbsoluteDeadlinePacer | None = None,
    timing_metrics: LiveRuntimeTimingMetrics | None = None,
    collect_records: bool = True,
) -> tuple[RuntimeInputSourceStepLoopRecord, ...]:
    if steps < 1:
        raise ValueError("steps must be a positive integer")

    reader = plan.pipeline.input_source
    managed_reader = reader if isinstance(reader, ManagedInputSource) else None
    start_attempted = False
    close_attempted = False
    primary_failure: BaseException | None = None
    plan.pipeline.reset_mapping_session()
    try:
        if managed_reader is not None:
            start_attempted = True
            managed_reader.start()
        return await _run_runtime_input_source_step_loop(
            plan,
            steps=steps,
            dt_s=dt_s,
            interval_s=interval_s,
            pacer=pacer,
            timing_metrics=timing_metrics,
            collect_records=collect_records,
        )
    except BaseException as failure:
        primary_failure = failure
        raise
    finally:
        plan.pipeline.reset_mapping_session()
        if start_attempted and managed_reader is not None and not close_attempted:
            close_attempted = True
            try:
                managed_reader.close()
            except BaseException as cleanup_failure:
                if primary_failure is not None:
                    primary_failure.add_note(
                        f"input source cleanup failed: {cleanup_failure!r}"
                    )
                else:
                    raise


async def _run_runtime_input_source_step_loop(
    plan: RuntimeInputSourceStepLoopPlan,
    *,
    steps: int,
    dt_s: float | None = None,
    interval_s: float = 0.0,
    pacer: AbsoluteDeadlinePacer | None = None,
    timing_metrics: LiveRuntimeTimingMetrics | None = None,
    collect_records: bool = True,
) -> tuple[RuntimeInputSourceStepLoopRecord, ...]:
    if steps < 1:
        raise ValueError("steps must be a positive integer")

    dt = plan.pipeline.config.dt_s if dt_s is None else dt_s
    if timing_metrics is not None:
        timing_metrics.start()
    if pacer is not None:
        pacer.start()
    records: list[RuntimeInputSourceStepLoopRecord] = []
    last_valid_endpoint_m: tuple[float, float, float] | None = None
    if plan.execution_adapter.uses_viewer_endpoint_compatibility:
        last_valid_endpoint_m = _extract_current_endpoint_m(
            plan.pipeline, plan.endpoint_pose_provider
        )
        if last_valid_endpoint_m is None:
            last_valid_endpoint_m = _coerce_viewer_endpoint_m(
                plan.selection.initial_metadata.get(
                    "desired_endpoint_m",
                    plan.selection.initial_metadata.get("target_position_m"),
                )
            )

    for index in range(steps):
        compute_started_s = timing_metrics.clock() if timing_metrics is not None else 0.0
        raw_frame = plan.pipeline.input_source.read_frame()
        if plan.execution_adapter.uses_replay_pipeline:
            source_state = build_runtime_input_source_state_from_metadata(
                raw_frame.metadata,
                default_source_kind=plan.selection.source_name,
            )
        else:
            source_state = reconcile_runtime_input_source_state(
                raw_frame, plan.pipeline.input_source.current_health(),
                source_kind=plan.selection.source_name,
            )
        frame = annotate_raw_input_frame(raw_frame, source_state)
        pre_step_state = plan.pipeline.simulator.snapshot()
        mapped_intent = plan.pipeline.map_input(
            frame, pre_step_state=pre_step_state, endpoint_pose_provider=plan.endpoint_pose_provider,
        )
        if not isinstance(mapped_intent, InputIntent):
            raise TypeError(
                "control mapping strategy must return a typed InputIntent"
            )
        intent = mapped_intent
        # The frame remains the source record, but expose the canonical
        # mapping result in its compatibility metadata for existing
        # viewer diagnostics and runtime records. The source itself never
        # computes these fields.
        frame = replace(
            frame,
            values=intent.values,
            buttons=intent.buttons,
            metadata={**frame.metadata, **intent.metadata},
        )
        if plan.execution_adapter.uses_viewer_endpoint_compatibility:
            pre_step_tip_site_position_m = _extract_current_endpoint_m(
                plan.pipeline, plan.endpoint_pose_provider
            )
            frame = replace(
                frame,
                metadata={
                    **frame.metadata,
                    "current_tip_position_m": pre_step_tip_site_position_m,
                    "desired_endpoint_m": last_valid_endpoint_m,
                    "target_position_m": last_valid_endpoint_m,
                },
            )
        motion_intent = intent
        if plan.execution_adapter.uses_viewer_endpoint_compatibility:
            motion_intent = replace(intent, metadata={**frame.metadata, **intent.metadata})
        safety_result = plan.pipeline.execute_intent(
            motion_intent,
            dt_s=dt,
            pre_step_state=pre_step_state,
            source_state=source_state,
            endpoint_pose_provider=plan.endpoint_pose_provider,
        )
        step_endpoint_m = last_valid_endpoint_m
        if (
            not safety_result.motion_command.metadata.get("target_rejected", False)
            and not safety_result.qpos_feasibility_rejected
        ):
            desired_endpoint_m = safety_result.motion_command.metadata.get("desired_endpoint_m")
            if desired_endpoint_m is not None:
                step_endpoint_m = _coerce_viewer_endpoint_m(desired_endpoint_m)

        compute_finished_s = timing_metrics.clock() if timing_metrics is not None else 0.0
        simulation_started_s = compute_finished_s
        plan.pipeline.simulator.step(dt)

        state = plan.pipeline.simulator.snapshot()
        simulation_finished_s = timing_metrics.clock() if timing_metrics is not None else 0.0
        annotation_started_s = simulation_finished_s
        state = replace(
            state,
            metadata=merge_runtime_metadata(
                state.metadata,
                plan.pipeline.state_metadata,
                authoritative_profile_metadata=plan.pipeline.robot_profile_metadata,
            ),
        )
        measurement = PostStepMeasurement(None, None, None)
        if (
            plan.execution_adapter.uses_viewer_endpoint_compatibility
            or isinstance(plan.command_execution, MappingRuntimeContextBinding)
        ) and plan.endpoint_site_name is not None:
            measurement = measure_post_step_endpoint(
                pre_step_state,
                state,
                site_name=plan.endpoint_site_name,
            )
        annotated_state = annotate_runtime_input_state(
            source_state=safety_result.source_state,
            frame=frame,
            intent=intent,
            motion_command=safety_result.motion_command,
            last_valid_endpoint_m=last_valid_endpoint_m,
            state=state,
            measurement=measurement,
            annotate_target_position_m=plan.annotate_target_position_m,
            safety_result=safety_result,
            authoritative_profile_metadata=plan.pipeline.robot_profile_metadata,
        )
        # 上流metadataの自己申告は除去し、このframeのraw projectionだけを公開する。
        display_metadata = dict(annotated_state.metadata)
        display_metadata.pop("input_signal_v1", None)
        if plan.selection.produced_sample_schema is not None:
            display_metadata["input_signal_v1"] = input_signal_display_projection(
                raw_frame, sample_schema=plan.selection.produced_sample_schema.canonical_id,
                state=annotated_state)
        annotated_state = replace(annotated_state, metadata=display_metadata)
        annotation_finished_s = timing_metrics.clock() if timing_metrics is not None else 0.0
        publish_started_s = annotation_finished_s
        await plan.pipeline.publisher.publish(annotated_state)
        publish_finished_s = timing_metrics.clock() if timing_metrics is not None else 0.0
        if plan.execution_adapter.uses_viewer_endpoint_compatibility:
            if step_endpoint_m is None:
                step_endpoint_m = annotated_state.target_position_m or last_valid_endpoint_m
            if step_endpoint_m is not None:
                last_valid_endpoint_m = step_endpoint_m
            pipeline_capability = plan.pipeline.input_source
            capability = (
                pipeline_capability
                if isinstance(pipeline_capability, ViewerEndpointRebaseCapability)
                else plan.viewer_bridge_capability
            )
            if capability is None:
                raise RuntimeError("viewer execution adapter has no runtime bridge capability")
            _sync_viewer_input_source_endpoint(
                capability,
                endpoint_m=step_endpoint_m,
            )
        post_publish_finished_s = timing_metrics.clock() if timing_metrics is not None else 0.0

        if collect_records:
            records.append(
                RuntimeInputSourceStepLoopRecord(
                    frame=frame,
                    intent=intent,
                    motion_command=safety_result.motion_command,
                    state=annotated_state,
                )
            )

        if timing_metrics is not None:
            timing_metrics.record_frame(
                compute_time_s=compute_finished_s - compute_started_s,
                simulation_step_time_s=simulation_finished_s - simulation_started_s,
                annotation_time_s=(annotation_finished_s - annotation_started_s)
                + (post_publish_finished_s - publish_finished_s),
                publish_wait_or_enqueue_time_s=publish_finished_s - publish_started_s,
            )

        if pacer is not None:
            await pacer.pace()
        elif interval_s > 0.0 and index + 1 < steps:
            await asyncio.sleep(interval_s)

    return tuple(records)


__all__ = [
    "RuntimeInputSourceStepLoopPlan",
    "RuntimeInputSourceStepLoopRecord",
    "build_runtime_input_source_step_loop_plan",
    "run_runtime_input_source_step_loop",
]
