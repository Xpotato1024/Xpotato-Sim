from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from xpotato_sim.plugins.input_sources.replay import ReplayInputSource
from xpotato_sim.plugins.mappings.replay_mapping import REPLAY_CONTROL_MAPPING_PLUGIN
from xpotato_sim.motion import InputIntentMotionGenerator
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.robot_bundle import (
    QPOS_FEASIBILITY_V1,
    ENDPOINT_COMMAND_V1, ENDPOINT_POSE_V1,
    RESET_INITIAL_STATE_V1,
    QposFeasibilityProvider,
    ResetInitialStateProvider,
    RobotBundle,
)
from xpotato_sim.runtime.composition.robot_profile import (
    robot_profile_runtime_metadata,
)
from xpotato_sim.runtime.composition.robot_resolution import (
    validate_production_robot_selection_consistency,
)
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.execution.command_routes import RouteMotionGeneratorFactory, build_route_motion_generator
from xpotato_sim.runtime.experiment.composition import resolve_command_execution
from xpotato_sim.runtime.experiment.contracts import (
    ControlMappingPlugin,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.input_source import InputSourceMappingAdapterContract
from xpotato_sim.schemas import RawInputFrame
from xpotato_sim.transport import StatePublisher


class _ReplayCompatibilityStatePublisher:
    """Local compatibility publisher for replay defaults."""

    def __init__(self) -> None:
        self.last_state = None

    async def publish(self, state) -> None:
        self.last_state = state


def _resolve_model_path(
    *, model_path: str | Path | None, config: RuntimeConfig
) -> Path | None:
    if model_path is not None:
        return Path(model_path)
    return config.mujoco_model_path


def _default_replay_frame() -> RawInputFrame:
    return RawInputFrame(
        source="replay",
        timestamp_s=0.0,
        metadata={"preset": "r6-a-p1-default"},
    )


def build_replay_mujoco_pipeline(
    *,
    frames: Sequence[RawInputFrame] | None = None,
    config: RuntimeConfig,
    model_path: str | Path | None = None,
    loop: bool = False,
    publisher: StatePublisher | None = None,
    state_metadata: Mapping[str, object] | None = None,
    control_mapping: ControlMappingPlugin = REPLAY_CONTROL_MAPPING_PLUGIN,
    control_mapping_parameters: Mapping[str, object] | None = None,
    mapping_input_adapter: InputSourceMappingAdapterContract | None = None,
    robot_bundle: RobotBundle,
    command_semantics_route_selection: VersionedIdentity | None = None,
) -> ControlMappedRuntimePipeline:
    robot_selection = config.robot_selection
    if robot_selection is None:
        raise ValueError("production replay composition requires robot_selection")
    validate_production_robot_selection_consistency(
        robot_selection,
        bundle_identity=robot_bundle.identity,
        profile=robot_bundle.profile,
        plugin=robot_bundle.runtime_plugin,
    )

    command_execution = resolve_command_execution(
        control_mapping,
        robot_bundle,
        command_semantics_route_selection,
    )
    initial_state_provider = robot_bundle.provider(RESET_INITIAL_STATE_V1)
    qpos_feasibility_provider = robot_bundle.provider(QPOS_FEASIBILITY_V1)
    assert isinstance(initial_state_provider, ResetInitialStateProvider)
    assert isinstance(qpos_feasibility_provider, QposFeasibilityProvider)
    initial_state = initial_state_provider.resolve_initial_state()
    if initial_state.source_kind != "named_keyframe":
        raise ValueError("production replay composition requires a named-keyframe initial state")

    replay_frames = tuple(frames) if frames is not None else (_default_replay_frame(),)
    resolved_model_path = _resolve_model_path(
        model_path=model_path,
        config=config,
    )
    state_publisher = _ReplayCompatibilityStatePublisher() if publisher is None else publisher
    plugin = robot_bundle.runtime_plugin
    local_route = isinstance(command_execution.binding, RouteMotionGeneratorFactory)
    endpoint_provider = robot_bundle.provider(ENDPOINT_COMMAND_V1) if local_route else None
    pose_provider = robot_bundle.provider(ENDPOINT_POSE_V1) if local_route else None
    motion_generator = build_route_motion_generator(
        command_execution.binding, endpoint_provider, InputIntentMotionGenerator
    )
    simulator = plugin.build_simulator(
        model_path=resolved_model_path,
        initial_keyframe_name=initial_state.source_id,
    )
    plugin.validate_model(simulator.model)
    qpos_feasibility_guard = qpos_feasibility_provider.build_guard(
        model=simulator.model,
        config_path=config.joint_limit_config_path,
    )

    return ControlMappedRuntimePipeline(
        config=config,
        input_source=ReplayInputSource(replay_frames, loop=loop),
        control_mapping=control_mapping,
        control_mapping_parameters=(
            {}
            if control_mapping_parameters is None
            else control_mapping_parameters
        ),
        mapping_input_adapter=mapping_input_adapter,
        motion_generator=motion_generator,
        endpoint_pose_provider=pose_provider,
        simulator=simulator,
        publisher=state_publisher,
        command_semantics_route=command_execution.route,
        command_execution=command_execution.binding,
        qpos_feasibility_guard=qpos_feasibility_guard,
        state_metadata=state_metadata,
        robot_profile_metadata=robot_profile_runtime_metadata(robot_bundle.profile),
    )
