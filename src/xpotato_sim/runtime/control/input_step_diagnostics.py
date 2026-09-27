from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from xpotato_sim.runtime.control.desired_endpoint_resolver import (
    resolve_desired_endpoint_from_motion_command,
)
from xpotato_sim.runtime.evaluation.endpoint_progress import endpoint_progress_metadata
from xpotato_sim.runtime.control.input_source_state import (
    RuntimeInputSourceState,
    runtime_input_source_state_to_metadata,
)
from xpotato_sim.runtime.composition.robot_profile_metadata import merge_runtime_metadata
from xpotato_sim.runtime.safety.input_safety import RuntimeInputSafetyResult
from xpotato_sim.schemas import InputIntent, MotionCommand, MuJoCoState, RawInputFrame


_STALE_RESOLVED_METADATA_KEYS = (
    "resolved_world_endpoint_velocity_m_s",
    "endpoint_velocity_m_s",
    "endpoint_velocity_frame",
    "endpoint_delta_m",
    "endpoint_delta_requested_m",
    "endpoint_delta_achieved_m",
)


@dataclass(frozen=True, slots=True)
class PostStepMeasurement:
    pre_step_tip_position_m: tuple[float, float, float] | None
    post_step_tip_position_m: tuple[float, float, float] | None
    actual_tip_delta_m: tuple[float, float, float] | None

    @property
    def available(self) -> bool:
        return self.actual_tip_delta_m is not None


@dataclass(frozen=True, slots=True)
class TargetFeedbackAnnotation:
    should_publish_target: bool
    target_position_m: tuple[float, float, float] | None
    metadata: Mapping[str, object]


def _extract_site_position_m(
    state: MuJoCoState, site_name: str
) -> tuple[float, float, float] | None:
    site = next((site for site in state.sites if site.name == site_name), None)
    return None if site is None else tuple(site.position_m)


def measure_post_step_endpoint(
    pre_step_state: MuJoCoState,
    post_step_state: MuJoCoState,
    *,
    site_name: str,
) -> PostStepMeasurement:
    pre_step_tip_position_m = _extract_site_position_m(pre_step_state, site_name)
    post_step_tip_position_m = _extract_site_position_m(post_step_state, site_name)
    actual_tip_delta_m = None
    if pre_step_tip_position_m is not None and post_step_tip_position_m is not None:
        actual_tip_delta_m = tuple(
            post_step_tip_position_m[index] - pre_step_tip_position_m[index]
            for index in range(3)
        )
    return PostStepMeasurement(
        pre_step_tip_position_m=pre_step_tip_position_m,
        post_step_tip_position_m=post_step_tip_position_m,
        actual_tip_delta_m=actual_tip_delta_m,
    )


def measure_post_step_tip(
    pre_step_state: MuJoCoState,
    post_step_state: MuJoCoState,
) -> PostStepMeasurement:
    return measure_post_step_endpoint(
        pre_step_state,
        post_step_state,
        site_name="tip",
    )


def build_diagnostic_metadata(
    *,
    state_metadata: Mapping[str, object],
    frame_metadata: Mapping[str, object],
    intent_metadata: Mapping[str, object],
    motion_command: MotionCommand,
    measurement: PostStepMeasurement,
    should_publish_target: bool,
    target_rejected: bool,
    qpos_rejected: bool = False,
    authoritative_profile_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    metadata = merge_runtime_metadata(
        state_metadata,
        frame_metadata,
        intent_metadata,
        motion_command.metadata,
        authoritative_profile_metadata=authoritative_profile_metadata,
    )
    if metadata.get("control_frame_resolution_status") == "tool_orientation_unavailable":
        for stale_key in _STALE_RESOLVED_METADATA_KEYS:
            metadata.pop(stale_key, None)

    if not should_publish_target or qpos_rejected:
        metadata.pop("desired_endpoint_m", None)
        metadata.pop("target_position_m", None)
        metadata["runtime_input_safety_applied"] = True
        metadata["endpoint_evaluation"] = None

    if measurement.actual_tip_delta_m is not None:
        metadata["actual_tip_delta_m"] = measurement.actual_tip_delta_m

    if (
        motion_command.metadata.get("local_motion_policy") == "finite_difference_jacobian"
        and not target_rejected
        and "endpoint_delta_requested_m" in motion_command.metadata
    ):
        metadata.update(
            endpoint_progress_metadata(
                motion_command.metadata["endpoint_delta_requested_m"],
                measurement.actual_tip_delta_m,
            )
        )
    return metadata


def annotate_target_feedback(
    *,
    state: MuJoCoState,
    motion_command: MotionCommand,
    metadata: Mapping[str, object],
    annotate_target_position_m: bool,
    should_publish_target: bool,
    last_valid_endpoint_m: tuple[float, float, float] | None,
) -> TargetFeedbackAnnotation:
    annotated_metadata = dict(metadata)
    target_position_m = state.target_position_m
    if annotate_target_position_m and should_publish_target:
        try:
            resolved = resolve_desired_endpoint_from_motion_command(motion_command)
        except ValueError:
            resolved = None
        if resolved is not None:
            target_position_m = resolved.desired_endpoint_m
            annotated_metadata["desired_endpoint_m"] = resolved.desired_endpoint_m
            annotated_metadata["target_position_m"] = resolved.desired_endpoint_m
    elif not should_publish_target and last_valid_endpoint_m is not None:
        target_position_m = last_valid_endpoint_m

    return TargetFeedbackAnnotation(
        should_publish_target=should_publish_target,
        target_position_m=target_position_m,
        metadata=annotated_metadata,
    )


def annotate_runtime_input_state(
    *,
    source_state: RuntimeInputSourceState,
    frame: RawInputFrame,
    intent: InputIntent,
    motion_command: MotionCommand,
    last_valid_endpoint_m: tuple[float, float, float] | None,
    state: MuJoCoState,
    measurement: PostStepMeasurement,
    annotate_target_position_m: bool,
    safety_result: RuntimeInputSafetyResult,
    authoritative_profile_metadata: Mapping[str, object] | None = None,
) -> MuJoCoState:
    target_rejected = bool(motion_command.metadata.get("target_rejected", False))
    should_publish_target = (
        safety_result.should_update_target_position_m
        and not target_rejected
        and not safety_result.qpos_feasibility_rejected
    )
    metadata = build_diagnostic_metadata(
        state_metadata=state.metadata,
        frame_metadata=frame.metadata,
        intent_metadata=intent.metadata,
        motion_command=motion_command,
        measurement=measurement,
        should_publish_target=should_publish_target,
        target_rejected=target_rejected,
        qpos_rejected=safety_result.qpos_feasibility_rejected,
        authoritative_profile_metadata=authoritative_profile_metadata,
    )
    feedback = annotate_target_feedback(
        state=state,
        motion_command=motion_command,
        metadata=metadata,
        annotate_target_position_m=annotate_target_position_m,
        should_publish_target=should_publish_target,
        last_valid_endpoint_m=last_valid_endpoint_m,
    )
    final_metadata = merge_runtime_metadata(
        feedback.metadata,
        runtime_input_source_state_to_metadata(source_state),
        authoritative_profile_metadata=authoritative_profile_metadata,
    )
    return replace(
        state,
        target_position_m=feedback.target_position_m,
        metadata=final_metadata,
    )


def input_signal_display_projection(frame: RawInputFrame, *, sample_schema: str,
                                    state: MuJoCoState) -> dict[str, object]:
    """取得済みraw値を同じsimulation frameへ結ぶ。写像・校正・単位を推定しない。"""
    from math import isfinite

    if type(sample_schema) is not str or not sample_schema or sample_schema != sample_schema.strip() or "\x00" in sample_schema:
        raise ValueError("input signal sample schema is required")
    if type(frame) is not RawInputFrame or type(state) is not MuJoCoState:
        raise TypeError("input signal display requires typed frame/state")
    if type(frame.source) is not str or not frame.source or frame.source != frame.source.strip() or "\x00" in frame.source:
        raise ValueError("input signal source must be a canonical string")
    if type(state.frame_index) is not int or not 0 <= state.frame_index <= 2**53 - 1:
        raise ValueError("input signal frame index must be a non-negative safe integer")
    values = tuple(frame.values)
    try:
        valid = all(type(value) in (int, float) and isfinite(value)
                    for value in (*values, frame.timestamp_s, state.time_s))
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("input signal display requires finite raw values and timestamps")
    return {
        "schema_version": "input-signal-display/v1",
        "source": frame.source,
        "sample_schema": sample_schema,
        "source_timestamp_s": frame.timestamp_s,
        "frame_index": state.frame_index,
        "simulation_time_s": state.time_s,
        "values": list(values),
    }


__all__ = [
    "PostStepMeasurement",
    "TargetFeedbackAnnotation",
    "annotate_runtime_input_state",
    "annotate_target_feedback",
    "build_diagnostic_metadata",
    "input_signal_display_projection",
    "measure_post_step_tip",
    "measure_post_step_endpoint",
]
