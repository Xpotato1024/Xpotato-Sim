from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from xpotato_sim.plugins.input_sources.replay import ReplayInputSource
from xpotato_sim.plugins.mappings.replay_mapping import REPLAY_CONTROL_MAPPING_PLUGIN
from xpotato_sim.plugins.robots.catalog import (
    RobotCatalog,
    resolve_robot_bundle,
    resolve_robot_profile,
)
from xpotato_sim.runtime.composition.robot_profile import robot_profile_runtime_metadata
from xpotato_sim.runtime.composition.robot_resolution import (
    validate_production_robot_selection_consistency,
)
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.evaluation.endpoint_metrics import build_endpoint_evaluation_state_publisher
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.execution.command_routes import build_route_motion_generator
from xpotato_sim.runtime.experiment.composition import resolve_command_execution
from xpotato_sim.runtime.experiment.contracts import ControlMappingPlugin
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from xpotato_sim.runtime.experiment.input_source import HealthyInputSource, InputSourceMappingAdapterContract
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
    EndpointCommandProvider,
    EndpointPoseProvider,
    QposFeasibilityProvider,
    ResetInitialStateProvider,
    RobotBundle,
)
from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.transport import StatePublisher

DEFAULT_CONCRETE_TARGET_POSITION_M = (0.6, 0.0, 0.1)


def _resolve_model_path(
    *, model_path: str | Path | None, config: RuntimeConfig, robot_bundle: RobotBundle
) -> Path | None:
    if model_path is not None:
        return Path(model_path)
    if config.mujoco_model_path is not None:
        return config.mujoco_model_path
    return None


def _default_concrete_frame() -> RawInputFrame:
    return RawInputFrame(
        source="replay",
        timestamp_s=0.0,
        metadata={
            "preset": "r6-h-p5-default",
            "target_position_m": DEFAULT_CONCRETE_TARGET_POSITION_M,
            "desired_endpoint_m": DEFAULT_CONCRETE_TARGET_POSITION_M,
        },
    )


def build_concrete_mujoco_pipeline(
    *,
    frames: Sequence[RawInputFrame] | None = None,
    config: RuntimeConfig | None = None,
    model_path: str | Path | None = None,
    loop: bool = False,
    publisher: StatePublisher,
    seed_joint_angles_rad: tuple[float, ...] | None = None,
    discontinuity_threshold_rad: float | None = None,
    discontinuity_threshold_label: str = "global safety threshold",
    robot_catalog: RobotCatalog | None = None,
    control_mapping: ControlMappingPlugin = REPLAY_CONTROL_MAPPING_PLUGIN,
    control_mapping_parameters: Mapping[str, object] | None = None,
    mapping_input_adapter: InputSourceMappingAdapterContract | None = None,
    input_source: HealthyInputSource | None = None,
    command_semantics_route_selection: VersionedIdentity | None = None,
) -> ControlMappedRuntimePipeline:
    """明示readerまたはreplay framesからRobot-ownedな単一simulationを組み立てる。

    readerを指定した場合、そのlifecycleは呼出側runtimeが所有する。ここではstart/readせず、
    不要なreplay sourceやダミーframeを生成しない。二つの取得経路の同時指定は拒否する。
    """
    if input_source is not None:
        if not isinstance(input_source, HealthyInputSource):
            raise TypeError("input_source must provide the typed source and health contract")
        if frames:
            raise ValueError("input_source and replay frames cannot be combined")
    runtime_config = RuntimeConfig(robot_profile_id="fast_arm") if config is None else config
    if runtime_config.robot_profile_id is None:
        raise ValueError("production concrete composition requires robot_profile_id")
    selection = runtime_config.robot_selection
    assert selection is not None
    if robot_catalog is None:
        profile = resolve_robot_profile(
            selection.plugin_id,
            robot_logical_version=selection.contract_version,
        )
        robot_bundle = resolve_robot_bundle(
            selection.plugin_id,
            robot_logical_version=selection.contract_version,
        )
    else:
        profile = robot_catalog.resolve_profile(selection)
        robot_bundle = robot_catalog.resolve_bundle(selection)
    if robot_bundle.profile is not profile:
        raise ValueError("Robot Bundle/profile catalog consistency mismatch")
    plugin = robot_bundle.runtime_plugin
    validate_production_robot_selection_consistency(
        selection,
        bundle_identity=robot_bundle.identity,
        profile=profile,
        plugin=plugin,
    )
    command_execution = resolve_command_execution(
        control_mapping,
        robot_bundle,
        command_semantics_route_selection,
    )
    initial_state_provider = robot_bundle.provider(RESET_INITIAL_STATE_V1)
    endpoint_command_provider = (
        robot_bundle.provider(ENDPOINT_COMMAND_V1)
        if command_execution.binding.requires_motion_generator
        else None
    )
    endpoint_pose_provider = robot_bundle.provider(ENDPOINT_POSE_V1)
    qpos_feasibility_provider = robot_bundle.provider(QPOS_FEASIBILITY_V1)
    assert isinstance(initial_state_provider, ResetInitialStateProvider)
    if endpoint_command_provider is not None:
        assert isinstance(endpoint_command_provider, EndpointCommandProvider)
    assert isinstance(endpoint_pose_provider, EndpointPoseProvider)
    assert isinstance(qpos_feasibility_provider, QposFeasibilityProvider)
    initial_state = initial_state_provider.resolve_initial_state()
    if initial_state.source_kind != "named_keyframe":
        raise ValueError("production concrete composition requires a named-keyframe initial state")
    if input_source is None:
        replay_frames = tuple(frames) if frames is not None else (_default_concrete_frame(),)
        input_source = ReplayInputSource(replay_frames, loop=loop)
    resolved_model_path = _resolve_model_path(
        model_path=model_path, config=runtime_config, robot_bundle=robot_bundle
    )
    motion_generator = build_route_motion_generator(
        command_execution.binding, endpoint_command_provider,
        lambda: endpoint_command_provider.build_target_motion_generator(
            seed_joint_angles_rad=seed_joint_angles_rad,
            discontinuity_threshold_rad=discontinuity_threshold_rad,
            discontinuity_threshold_label=discontinuity_threshold_label,
        ),
    )
    simulator = plugin.build_simulator(
        model_path=resolved_model_path,
        initial_keyframe_name=initial_state.source_id,
    )
    plugin.validate_model(simulator.model)
    fk_solver = plugin.build_forward_kinematics()

    return ControlMappedRuntimePipeline(
        config=runtime_config,
        input_source=input_source,
        control_mapping=control_mapping,
        control_mapping_parameters=(
            {}
            if control_mapping_parameters is None
            else control_mapping_parameters
        ),
        mapping_input_adapter=mapping_input_adapter,
        motion_generator=motion_generator,
        endpoint_pose_provider=endpoint_pose_provider,
        simulator=simulator,
        publisher=build_endpoint_evaluation_state_publisher(
            publisher,
            simulator=simulator,
            fk_solver=fk_solver,
            endpoint_pose_provider=endpoint_pose_provider,
            initial_state=simulator.snapshot(),
            solver_joint_count=plugin.profile.qpos_dimension,
        ),
        qpos_feasibility_guard=qpos_feasibility_provider.build_guard(
            model=simulator.model,
            config_path=runtime_config.joint_limit_config_path,
        ),
        command_semantics_route=command_execution.route,
        command_execution=command_execution.binding,
        robot_profile_metadata=robot_profile_runtime_metadata(robot_bundle.profile),
    )


__all__ = ["build_concrete_mujoco_pipeline"]
