from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite

from xpotato_sim.mujoco_backend import RuntimeMuJoCoEndpointEvaluation
from xpotato_sim.plugins.input_sources.replay import ReplayInputSource
from xpotato_sim.plugins.mappings.replay_mapping import REPLAY_CONTROL_MAPPING_PLUGIN
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle, resolve_robot_profile
from xpotato_sim.runtime.composition.robot_profile import RobotProfile, robot_profile_runtime_metadata
from xpotato_sim.runtime.control.desired_endpoint_resolver import resolve_desired_endpoint_from_motion_command
from xpotato_sim.runtime.evaluation.endpoint_metrics import build_runtime_endpoint_evaluation_payload
from xpotato_sim.runtime.evaluation.kinematics import evaluate_fk_endpoint_from_qpos
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.robot_bundle import (
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
    EndpointCommandProvider,
    EndpointPoseProvider,
    QposFeasibilityProvider,
    ResetInitialStateProvider,
)
from xpotato_sim.runtime.composition.robot_profile_metadata import merge_runtime_metadata
from xpotato_sim.runtime.control.input_source_state import (
    build_runtime_input_source_state_from_metadata,
)
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.experiment.composition import resolve_command_execution
from xpotato_sim.runtime.experiment.contracts import ControlMappingPlugin
from xpotato_sim.schemas import (
    InputIntent,
    JointCommand,
    MotionCommand,
    MuJoCoState,
    RawInputFrame,
)
from xpotato_sim.transport import mujoco_state_to_payload

_DEFAULT_DT_S = 1.0 / 60.0


@dataclass(frozen=True, slots=True)
class OfflineInputRuntimeSmokeResult:
    motion_command: MotionCommand
    resolved_desired_endpoint_m: tuple[float, float, float]
    state: MuJoCoState
    endpoint_evaluation: Mapping[str, object] | None
    payload: Mapping[str, object] | None


class _OfflineStatePublisher:
    async def publish(self, state: MuJoCoState) -> None:
        _ = state


def _coerce_vector3(name: str, value: object) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three values")

    components = tuple(float(component) for component in value)
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three values")

    for component_index, component in enumerate(components):
        if not isfinite(component):
            raise ValueError(f"{name} must contain only finite values at index {component_index}")

    return components


def _optional_feedback_target_position_m(metadata: Mapping[str, object]) -> tuple[float, float, float] | None:
    target_position_m = metadata.get("target_position_m")
    if target_position_m is None:
        return None

    try:
        return _coerce_vector3('MotionCommand.metadata["target_position_m"]', target_position_m)
    except ValueError:
        return None


def _sanitize_motion_metadata(
    metadata: Mapping[str, object],
    *,
    resolved_desired_endpoint_m: tuple[float, float, float],
) -> tuple[dict[str, object], tuple[float, float, float] | None]:
    sanitized_metadata = dict(metadata)
    sanitized_metadata["desired_endpoint_m"] = resolved_desired_endpoint_m

    feedback_target_position_m = _optional_feedback_target_position_m(sanitized_metadata)
    if feedback_target_position_m is None:
        sanitized_metadata.pop("target_position_m", None)
    else:
        sanitized_metadata["target_position_m"] = feedback_target_position_m

    return sanitized_metadata, feedback_target_position_m


def _build_runtime_input_intent(
    command: MotionCommand,
    *,
    resolved_desired_endpoint_m: tuple[float, float, float],
) -> tuple[InputIntent, tuple[float, float, float] | None]:
    metadata, feedback_target_position_m = _sanitize_motion_metadata(
        command.metadata,
        resolved_desired_endpoint_m=resolved_desired_endpoint_m,
    )

    return (
        InputIntent(
            source=str(metadata.get("source_kind", "offline_input")),
            timestamp_s=command.timestamp_s,
            metadata=metadata,
        ),
        feedback_target_position_m,
    )


def _build_plugin_site_evaluation(
    *,
    state: MuJoCoState,
    endpoint_pose_provider: EndpointPoseProvider,
    profile: RobotProfile,
) -> RuntimeMuJoCoEndpointEvaluation:
    endpoint_position_m = endpoint_pose_provider.observe_endpoint_pose(state).position_m
    if endpoint_position_m is None:
        raise ValueError("selected robot runtime plugin did not provide an endpoint position")

    endpoint = profile.endpoint
    if endpoint.site_name is not None:
        kind = "site"
        name = endpoint.site_name
    elif endpoint.body_name is not None:
        kind = "body"
        name = endpoint.body_name
    else:  # pragma: no cover - RobotProfile validates this declaration
        raise ValueError("selected robot profile does not declare an endpoint reference")

    return RuntimeMuJoCoEndpointEvaluation(
        role="endpoint",
        kind=kind,
        name=name,
        position_m=endpoint_position_m,
        unit=profile.coordinate_units.position_unit,
        coordinate_frame=profile.coordinate_units.coordinate_frame,
    )

def run_offline_input_runtime_stepping_smoke(
    command: MotionCommand,
    *,
    initial_qpos: Sequence[float] | None = None,
    steps: int = 1,
    config: RuntimeConfig | None = None,
    control_mapping: ControlMappingPlugin = REPLAY_CONTROL_MAPPING_PLUGIN,
) -> OfflineInputRuntimeSmokeResult:
    if steps < 1:
        raise ValueError("steps must be a positive integer")

    resolved = resolve_desired_endpoint_from_motion_command(command)
    runtime_intent, feedback_target_position_m = _build_runtime_input_intent(
        command,
        resolved_desired_endpoint_m=resolved.desired_endpoint_m,
    )

    runtime_config = RuntimeConfig(robot_profile_id="fast_arm") if config is None else config
    if runtime_config.robot_profile_id is None:
        raise ValueError("production offline input smoke requires robot_profile_id")
    profile = resolve_robot_profile(
        runtime_config.robot_profile_id,
        robot_logical_version=runtime_config.robot_logical_version,
    )
    robot_bundle = resolve_robot_bundle(
        runtime_config.robot_profile_id,
        robot_logical_version=runtime_config.robot_logical_version,
    )
    if robot_bundle.profile is not profile:
        raise ValueError("Robot Bundle/profile catalog consistency mismatch")
    canonical_command_execution = resolve_command_execution(
        control_mapping,
        robot_bundle,
        None,
    )
    if not canonical_command_execution.binding.requires_motion_generator:
        raise ValueError(
            "offline MotionCommand smoke requires a motion-generator command route"
        )
    plugin = robot_bundle.runtime_plugin
    initial_state_provider = robot_bundle.provider(RESET_INITIAL_STATE_V1)
    endpoint_pose_provider = robot_bundle.provider(ENDPOINT_POSE_V1)
    endpoint_command_provider = robot_bundle.provider(ENDPOINT_COMMAND_V1)
    qpos_feasibility_provider = robot_bundle.provider(QPOS_FEASIBILITY_V1)
    assert isinstance(initial_state_provider, ResetInitialStateProvider)
    assert isinstance(endpoint_pose_provider, EndpointPoseProvider)
    assert isinstance(endpoint_command_provider, EndpointCommandProvider)
    assert isinstance(qpos_feasibility_provider, QposFeasibilityProvider)
    initial_state = initial_state_provider.resolve_initial_state()
    if initial_state.source_kind != "named_keyframe":
        raise ValueError("production offline input smoke requires a named-keyframe initial state")
    simulator = plugin.build_simulator(
        model_path=runtime_config.mujoco_model_path,
        initial_keyframe_name=initial_state.source_id,
    )
    plugin.validate_model(simulator.model)
    qpos_guard = qpos_feasibility_provider.build_guard(
        model=simulator.model,
        config_path=runtime_config.joint_limit_config_path,
    )

    initial_qpos_tuple = None if initial_qpos is None else tuple(float(value) for value in initial_qpos)
    if initial_qpos_tuple is not None:
        simulator.apply_qpos_command(JointCommand(joint_angles_rad=initial_qpos_tuple))

    seed_joint_angles_rad = tuple(simulator.snapshot().qpos)
    motion_generator = endpoint_command_provider.build_target_motion_generator(
        seed_joint_angles_rad=seed_joint_angles_rad,
        discontinuity_threshold_rad=None,
        discontinuity_threshold_label="global safety threshold",
    )
    fk_solver = plugin.build_forward_kinematics()
    runtime_motion_command = motion_generator.update(runtime_intent, _DEFAULT_DT_S)
    pipeline = ControlMappedRuntimePipeline(
        config=runtime_config,
        input_source=ReplayInputSource(
            (
                RawInputFrame(
                    source=str(
                        runtime_intent.metadata.get(
                            "source_kind",
                            "offline_input",
                        )
                    ),
                    timestamp_s=runtime_intent.timestamp_s,
                    metadata=runtime_intent.metadata,
                ),
            )
        ),
        control_mapping=control_mapping,
        motion_generator=motion_generator,
        simulator=simulator,
        publisher=_OfflineStatePublisher(),
        control_mapping_parameters={},
        command_semantics_route=canonical_command_execution.route,
        command_execution=canonical_command_execution.binding,
        qpos_feasibility_guard=qpos_guard,
        robot_profile_metadata=robot_profile_runtime_metadata(profile),
    )
    source_state = build_runtime_input_source_state_from_metadata(
        runtime_intent.metadata,
        default_source_kind="offline_input",
    )

    applied_command = runtime_motion_command
    qpos_rejected = False
    for _ in range(steps):
        safety_result = pipeline.execute_motion_command(
            runtime_motion_command,
            pre_step_state=simulator.snapshot(),
            source_state=source_state,
        )
        applied_command = safety_result.motion_command
        qpos_rejected = (
            qpos_rejected
            or safety_result.qpos_feasibility_rejected
        )
        simulator.step(_DEFAULT_DT_S)

    state = simulator.snapshot()
    state = replace(
        state,
        target_position_m=None if qpos_rejected else feedback_target_position_m,
        metadata=merge_runtime_metadata(
            applied_command.metadata,
            authoritative_profile_metadata=robot_profile_runtime_metadata(profile),
        ),
    )

    endpoint_evaluation = None
    if not qpos_rejected:
        try:
            fk_evaluation = evaluate_fk_endpoint_from_qpos(
                fk_solver,
                applied_command.joint.joint_angles_rad if applied_command.joint is not None else (),
                solver_joint_count=profile.qpos_dimension,
            )
            site_evaluation = _build_plugin_site_evaluation(
                state=state,
                endpoint_pose_provider=endpoint_pose_provider,
                profile=profile,
            )
        except ValueError:
            pass
        else:
            endpoint_evaluation = build_runtime_endpoint_evaluation_payload(
                desired_endpoint_m=applied_command.metadata.get("desired_endpoint_m"),
                fk_evaluation=fk_evaluation,
                site_evaluation=site_evaluation,
                qpos_like_joint_angles_rad=(
                    None if applied_command.joint is None else applied_command.joint.joint_angles_rad
                ),
            )

    if endpoint_evaluation is not None:
        state = replace(
            state,
            metadata=merge_runtime_metadata(
                state.metadata,
                {"endpoint_evaluation": endpoint_evaluation},
                authoritative_profile_metadata=robot_profile_runtime_metadata(profile),
            ),
        )

    payload = mujoco_state_to_payload(state)
    if endpoint_evaluation is None:
        payload = dict(payload)

    return OfflineInputRuntimeSmokeResult(
        motion_command=applied_command,
        resolved_desired_endpoint_m=resolved.desired_endpoint_m,
        state=state,
        endpoint_evaluation=endpoint_evaluation,
        payload=payload,
    )


__all__ = [
    "OfflineInputRuntimeSmokeResult",
    "run_offline_input_runtime_stepping_smoke",
]
