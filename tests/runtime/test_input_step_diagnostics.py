from __future__ import annotations

from copy import deepcopy

from xpotato_sim.runtime.control.input_step_diagnostics import (
    PostStepMeasurement,
    annotate_runtime_input_state,
    build_diagnostic_metadata,
    measure_post_step_tip,
)
from xpotato_sim.runtime.safety.input_safety import RuntimeInputSafetyResult
from xpotato_sim.runtime.control.input_source_state import RuntimeInputSourceState
from xpotato_sim.runtime.composition.robot_profile import robot_profile_runtime_metadata
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE
from xpotato_sim.schemas import InputIntent, MotionCommand, MuJoCoState, RawInputFrame, SiteTransform


def _state(*, tip_position_m=None, metadata=None, target_position_m=None) -> MuJoCoState:
    sites = ()
    if tip_position_m is not None:
        sites = (
            SiteTransform(
                name="tip",
                position_m=tip_position_m,
                quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
            ),
        )
    return MuJoCoState(
        frame_index=1,
        time_s=0.1,
        sites=sites,
        target_position_m=target_position_m,
        metadata={} if metadata is None else metadata,
    )


def test_measurement_uses_only_pre_and_post_tip_samples_without_mutation() -> None:
    pre = _state(tip_position_m=(1.0, 2.0, 3.0), metadata={"keep": [1]})
    post = _state(tip_position_m=(1.2, 1.5, 4.0), metadata={"keep": [2]})
    before = (deepcopy(pre), deepcopy(post))

    result = measure_post_step_tip(pre, post)

    assert result.pre_step_tip_position_m == (1.0, 2.0, 3.0)
    assert result.post_step_tip_position_m == (1.2, 1.5, 4.0)
    assert result.actual_tip_delta_m == (0.19999999999999996, -0.5, 1.0)
    assert result.available is True
    assert (pre, post) == before


def test_missing_tip_measurement_is_unavailable_and_not_fabricated() -> None:
    result = measure_post_step_tip(_state(tip_position_m=(0.0, 0.0, 0.0)), _state())

    assert result.post_step_tip_position_m is None
    assert result.actual_tip_delta_m is None
    assert result.available is False


def test_metadata_precedence_stale_removal_and_missing_progress_are_deterministic() -> None:
    state_metadata = {"precedence": "state", "endpoint_delta_achieved_m": (9.0, 9.0, 9.0)}
    frame_metadata = {"precedence": "frame", "resolved_world_endpoint_velocity_m_s": (9.0, 0.0, 0.0)}
    intent_metadata = {"precedence": "intent", "endpoint_velocity_frame": "stale"}
    command = MotionCommand(
        timestamp_s=0.0,
        metadata={
            "precedence": "command",
            "local_motion_policy": "finite_difference_jacobian",
            "control_frame_resolution_status": "tool_orientation_unavailable",
            "endpoint_delta_requested_m": (0.001, 0.0, 0.0),
            "endpoint_delta_m": (0.0, 0.0, 0.0),
        },
    )
    originals = deepcopy((state_metadata, frame_metadata, intent_metadata, command.metadata))

    result = build_diagnostic_metadata(
        state_metadata=state_metadata,
        frame_metadata=frame_metadata,
        intent_metadata=intent_metadata,
        motion_command=command,
        measurement=PostStepMeasurement(None, None, None),
        should_publish_target=True,
        target_rejected=False,
    )

    assert result["precedence"] == "command"
    assert result["endpoint_progress_status"] == "measurement_unavailable"
    assert result["endpoint_progress_measurement_available"] is False
    assert "actual_tip_delta_m" not in result
    for key in (
        "resolved_world_endpoint_velocity_m_s",
        "endpoint_velocity_frame",
        "endpoint_delta_m",
        "endpoint_delta_requested_m",
        "endpoint_delta_achieved_m",
    ):
        assert key not in result
    assert (state_metadata, frame_metadata, intent_metadata, command.metadata) == originals


def test_diagnostic_metadata_uses_typed_qpos_rejection_without_command_metadata() -> None:
    result = build_diagnostic_metadata(
        state_metadata={},
        frame_metadata={},
        intent_metadata={},
        motion_command=MotionCommand(
            timestamp_s=0.0,
            metadata={"desired_endpoint_m": (1.0, 2.0, 3.0)},
        ),
        measurement=PostStepMeasurement(None, None, None),
        should_publish_target=True,
        target_rejected=False,
        qpos_rejected=True,
    )

    assert result["endpoint_evaluation"] is None
    assert "desired_endpoint_m" not in result
    assert "qpos_feasibility_rejected" not in result


def test_frame_intent_and_command_cannot_spoof_authoritative_profile_metadata() -> None:
    spoofed = {
        "robot_profile_id": "spoofed",
        "model_contract_version": "spoofed/v9",
        "robot_joint_names": ("wrong",),
        "robot_qpos_dimension": 999,
    }
    authoritative = robot_profile_runtime_metadata(FAST_ARM_ROBOT_PROFILE)
    result = build_diagnostic_metadata(
        state_metadata=spoofed,
        frame_metadata=spoofed,
        intent_metadata=spoofed,
        motion_command=MotionCommand(timestamp_s=0.0, metadata=spoofed),
        measurement=PostStepMeasurement(None, None, None),
        should_publish_target=True,
        target_rejected=False,
        authoritative_profile_metadata=authoritative,
    )

    assert {key: result[key] for key in authoritative} == authoritative


def test_safety_hold_suppresses_target_and_source_state_has_final_precedence() -> None:
    source_state = RuntimeInputSourceState(
        source_kind="viewer_keyboard",
        source_active=False,
        command_age_ms=300,
        stale_reason="source_inactive",
    )
    command = MotionCommand(
        timestamp_s=0.0,
        metadata={"desired_endpoint_m": (1.0, 2.0, 3.0), "target_position_m": (1.0, 2.0, 3.0)},
    )
    safety = RuntimeInputSafetyResult(
        motion_command=command,
        source_state=source_state,
        is_stale=True,
        should_update_target_position_m=False,
        stale_reason="source_inactive",
        command_age_ms=300,
    )
    state = _state(metadata={"source_kind": "stale_state"}, target_position_m=(9.0, 9.0, 9.0))

    annotated = annotate_runtime_input_state(
        source_state=source_state,
        frame=RawInputFrame(source="viewer", timestamp_s=0.0, metadata={"source_kind": "frame"}),
        intent=InputIntent(source="viewer", timestamp_s=0.0, metadata={"source_kind": "intent"}),
        motion_command=command,
        last_valid_endpoint_m=(0.1, 0.2, 0.3),
        state=state,
        measurement=PostStepMeasurement(None, None, None),
        annotate_target_position_m=True,
        safety_result=safety,
    )

    assert annotated is not state
    assert annotated.target_position_m == (0.1, 0.2, 0.3)
    assert "desired_endpoint_m" not in annotated.metadata
    assert "target_position_m" not in annotated.metadata
    assert annotated.metadata["runtime_input_safety_applied"] is True
    assert annotated.metadata["source_kind"] == "viewer_keyboard"
    assert state.target_position_m == (9.0, 9.0, 9.0)
