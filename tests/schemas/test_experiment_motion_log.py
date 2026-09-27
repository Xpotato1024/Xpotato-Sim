from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import sqrt

import pytest

from xpotato_sim.schemas.experiment_log import (
    ConfigurationRecord,
    MotionSampleRecord,
    TrialOutcomeRecord,
    TrialStartRecord,
    decode_jsonl,
    encode_jsonl,
    parse_record,
    record_to_json_value,
    validate_record_stream,
)


FINAL_ERROR = sqrt(0.0002**2 + 0.0001**2)


def configuration(**changes: object) -> ConfigurationRecord:
    values = dict(experiment_id="experiment-1", session_id="session-1", participant_id="pseudonym-1", configuration_id="config-1", software_revision="abc123", initial_qpos_rad=(0.0, 0.1), initial_measured_tip_position_m=(0.0, 0.0, 0.0), initial_tool_orientation_wxyz=(1.0, 0.0, 0.0, 0.0), target_world_position_m=(0.001, 0.0, 0.0), target_tolerance_m=0.0005, dwell_interval_s=0.1, timeout_s=5.0, source_kind="keyboard", target_id="target-x", local_endpoint_speed_m_s=0.1, deadzone=0.05, local_endpoint_max_delta_m=0.002, comparison_parameters=(("gain", 1.0), ("schedule", "balanced")))
    values.update(changes)
    return ConfigurationRecord(**values)  # type: ignore[arg-type]


def start(trial_id: str = "trial-1", **changes: object) -> TrialStartRecord:
    values = dict(experiment_id="experiment-1", session_id="session-1", participant_id="pseudonym-1", configuration_id="config-1", trial_id=trial_id, block_id="block-1", task_family="translation", target_id="target-x", practice=False, control_condition="world", condition_order=0, task_order=0, target_direction="positive", direction_order=0, repetition_index=0, attempt_index=0, retry_of_trial_id=None, runtime_timestamp_s=1.0)
    values.update(changes)
    return TrialStartRecord(**values)  # type: ignore[arg-type]


def sample(index: int = 0, **changes: object) -> MotionSampleRecord:
    before = (0.0, 0.0, 0.0) if index == 0 else (0.0007, 0.0001, 0.0)
    after = (0.0007, 0.0001, 0.0) if index == 0 else (0.0008, 0.0001, 0.0)
    delta = tuple(after[i] - before[i] for i in range(3))
    qpos_before = (0.0, 0.1) if index == 0 else (0.01, 0.1)
    qpos_after = (0.01, 0.1) if index == 0 else (0.02, 0.1)
    values = dict(experiment_id="experiment-1", session_id="session-1", participant_id="pseudonym-1", configuration_id="config-1", trial_id="trial-1", sample_index=index, source_kind="keyboard", source_timestamp_s=1.0 + index * 0.1, runtime_timestamp_s=1.1 + index * 0.1, source_active=True, axis_values=(1.0, 0.0, 0.0), zero_input=False, stale_reason=None, requested_control_frame="world", local_endpoint_velocity_m_s=(0.1, 0.0, 0.0), resolved_control_frame="mujoco_world", control_frame_resolution_status="world_passthrough", control_frame_resolution_reason=None, resolved_world_endpoint_velocity_m_s=(0.1, 0.0, 0.0), endpoint_delta_requested_m=(0.001, 0.0, 0.0), endpoint_delta_achieved_m=(0.0008, 0.0, 0.0), qpos_before_rad=qpos_before, qpos_after_rad=qpos_after, candidate_qpos_rad=qpos_after, measured_tip_position_before_m=before, measured_tip_position_after_m=after, actual_tip_delta_m=delta, motion_status="accepted", motion_rejection_reason=None, target_rejected=False, target_rejection_reason=None, endpoint_progress_status="progressing", endpoint_progress_signed_m=delta[0], endpoint_progress_ratio=0.7, endpoint_progress_direction_cosine=0.99, endpoint_progress_requested_norm_m=0.001, endpoint_progress_measured_norm_m=sqrt(sum(component**2 for component in delta)), endpoint_progress_measurement_available=True, measurement_unavailable_reason=None)
    values.update(changes)
    if "local_endpoint_velocity_m_s" in changes and "resolved_world_endpoint_velocity_m_s" not in changes:
        values["resolved_world_endpoint_velocity_m_s"] = changes["local_endpoint_velocity_m_s"]
    return MotionSampleRecord(**values)  # type: ignore[arg-type]


def unavailable_sample(index: int = 0, **changes: object) -> MotionSampleRecord:
    values = dict(measured_tip_position_before_m=None, measured_tip_position_after_m=None, actual_tip_delta_m=None, endpoint_progress_status="measurement_unavailable", endpoint_progress_signed_m=None, endpoint_progress_ratio=None, endpoint_progress_direction_cosine=None, endpoint_progress_requested_norm_m=None, endpoint_progress_measured_norm_m=None, endpoint_progress_measurement_available=False, measurement_unavailable_reason="tip_site_missing")
    values.update(changes)
    return sample(index, **values)


def outcome(**changes: object) -> TrialOutcomeRecord:
    values = dict(experiment_id="experiment-1", session_id="session-1", participant_id="pseudonym-1", configuration_id="config-1", trial_id="trial-1", runtime_timestamp_s=2.0, completion_status="success", success_within_timeout=True, final_measured_endpoint_error_m=FINAL_ERROR, failure_attribution="none", outcome_reason=None, subjective_response_link_id="response-1", primary_outcome_sample_index=1)
    values.update(changes)
    return TrialOutcomeRecord(**values)  # type: ignore[arg-type]


def valid_stream() -> list[object]:
    return [configuration(), start(), sample(0), sample(1), outcome()]


def invalid_outcome(**changes: object) -> TrialOutcomeRecord:
    values = dict(completion_status="technical_invalid", success_within_timeout=False, final_measured_endpoint_error_m=None, failure_attribution="technical", outcome_reason="measurement_unavailable", primary_outcome_sample_index=None)
    values.update(changes)
    return outcome(**values)


def test_valid_world_stream_is_immutable_and_round_trips_deterministically() -> None:
    records = valid_stream()
    validate_record_stream(records)  # type: ignore[arg-type]
    encoded = encode_jsonl(records)  # type: ignore[arg-type]
    assert encode_jsonl(decode_jsonl(encoded)) == encoded
    assert isinstance(record_to_json_value(records[0])["initial_qpos_rad"], list)
    with pytest.raises(FrozenInstanceError):
        records[0].experiment_id = "changed"  # type: ignore[attr-defined]


def test_exact_canonical_names_are_serialized_without_old_aliases() -> None:
    value = record_to_json_value(sample())
    for field in ("source_kind", "source_active", "axis_values", "zero_input", "stale_reason", "control_frame_resolution_reason", "motion_rejection_reason"):
        assert field in value
    for old_alias in ("requested_axis", "resolution_reason", "rejection_reason", "hold_reason"):
        assert old_alias not in value


def test_tool_resolution_unavailable_does_not_fabricate_world_motion() -> None:
    record = sample(requested_control_frame="tool", resolved_control_frame=None, control_frame_resolution_status="tool_orientation_unavailable", control_frame_resolution_reason="tip_orientation_missing", resolved_world_endpoint_velocity_m_s=None, endpoint_delta_requested_m=None, endpoint_delta_achieved_m=(0.0, 0.0, 0.0), qpos_after_rad=(0.0, 0.1), candidate_qpos_rad=(0.0, 0.1), measured_tip_position_after_m=(0.0, 0.0, 0.0), actual_tip_delta_m=(0.0, 0.0, 0.0), motion_status="held", motion_rejection_reason="control_frame_resolution_failed")
    assert record.resolved_world_endpoint_velocity_m_s is None
    with pytest.raises(ValueError, match="resolved world motion"):
        sample(requested_control_frame="tool", resolved_control_frame=None, control_frame_resolution_status="tool_orientation_unavailable", control_frame_resolution_reason="tip_orientation_missing")


def test_motion_target_source_and_measurement_status_axes_are_independent() -> None:
    rejected = sample(motion_status="held", motion_rejection_reason="application_held", target_rejected=True, target_rejection_reason="workspace")
    stale = sample(source_active=False, stale_reason="source_stale", motion_status="accepted")
    unavailable = unavailable_sample(motion_status="accepted")
    assert rejected.motion_status == "held" and rejected.target_rejected
    assert stale.stale_reason == "source_stale" and stale.motion_status == "accepted"
    assert unavailable.endpoint_progress_status == "measurement_unavailable" and unavailable.motion_status == "accepted"


def test_active_zero_inactive_and_stale_source_lifecycle() -> None:
    active_zero = sample(source_active=True, axis_values=(0.0, 0.0, 0.0), zero_input=True, local_endpoint_velocity_m_s=(0.0, 0.0, 0.0))
    inactive = sample(source_active=False)
    stale = sample(source_active=False, stale_reason="disconnect")
    assert active_zero.zero_input and active_zero.source_active
    assert not inactive.source_active and inactive.stale_reason is None
    assert not stale.source_active and stale.stale_reason == "disconnect"
    with pytest.raises(ValueError, match="active source"):
        sample(source_active=True, stale_reason="stale")


@pytest.mark.parametrize(("factory", "changes"), [(sample, {"requested_control_frame": "camera"}), (sample, {"resolved_control_frame": "tool"}), (sample, {"control_frame_resolution_status": "resolved"}), (sample, {"motion_status": "rejected"}), (sample, {"endpoint_progress_status": "unknown"}), (outcome, {"completion_status": "complete"}), (outcome, {"failure_attribution": "system"}), (start, {"control_condition": "camera"})])
def test_invalid_vocabularies_are_rejected(factory: object, changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        factory(**changes)  # type: ignore[operator]


@pytest.mark.parametrize(("factory", "changes"), [(start, {"practice": 1}), (sample, {"source_active": 1}), (sample, {"zero_input": 0}), (outcome, {"success_within_timeout": 1}), (sample, {"runtime_timestamp_s": "1.0"}), (sample, {"axis_values": ("1", 0, 0)}), (configuration, {"timeout_s": True})])
def test_boolean_and_numeric_string_coercion_is_rejected(factory: object, changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        factory(**changes)  # type: ignore[operator]


def test_comparison_parameters_reject_nested_values_and_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="JSON scalars"):
        configuration(comparison_parameters=(("nested", {"value": 1}),))
    with pytest.raises(ValueError, match="finite"):
        configuration(comparison_parameters=(("gain", float("nan")),))


@pytest.mark.parametrize("changed", [{"participant_id": "other"}, {"configuration_id": "config-2"}, {"experiment_id": "other"}, {"session_id": "other"}])
def test_sample_cross_context_is_rejected(changed: dict[str, object]) -> None:
    configs = [configuration()]
    if changed.get("configuration_id") == "config-2":
        configs.append(configuration(configuration_id="config-2"))
    with pytest.raises(ValueError):
        validate_record_stream([*configs, start(), sample(**changed)])


def test_outcome_cross_context_is_rejected() -> None:
    with pytest.raises(ValueError, match="context"):
        validate_record_stream([configuration(), start(), sample(0), sample(1), outcome(participant_id="other")])


@pytest.mark.parametrize("changed", [{"target_id": "other"}, {"control_condition": "tool"}, {"block_id": "other"}, {"practice": True}, {"task_family": "other"}, {"configuration_id": "config-2"}])
def test_retry_must_preserve_protocol_context(changed: dict[str, object]) -> None:
    records: list[object] = [configuration()]
    if changed.get("configuration_id") == "config-2":
        records.append(configuration(configuration_id="config-2"))
    records.extend([start(), unavailable_sample(), invalid_outcome(), start("trial-2", attempt_index=1, retry_of_trial_id="trial-1", runtime_timestamp_s=3.0, **changed)])
    with pytest.raises(ValueError, match="protocol identity|configuration manifest"):
        validate_record_stream(records)  # type: ignore[arg-type]


def test_success_referencing_unavailable_sample_is_rejected() -> None:
    with pytest.raises(ValueError, match="complete measured"):
        validate_record_stream([configuration(dwell_interval_s=0.0), start(), unavailable_sample(), outcome(primary_outcome_sample_index=0)])


@pytest.mark.parametrize("changes", [
    {"requested_control_frame": "tool", "control_frame_resolution_status": "world_passthrough"},
    {"requested_control_frame": "world", "control_frame_resolution_status": "tool_orientation_resolved"},
    {"requested_control_frame": "tool", "control_frame_resolution_status": "invalid_control_frame_defaulted"},
])
def test_resolution_status_requires_canonical_requested_frame(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="requested frame"):
        sample(**changes)


def test_unavailable_tool_resolution_requires_complete_hold_tuple() -> None:
    base = dict(requested_control_frame="tool", resolved_control_frame=None, control_frame_resolution_status="tool_orientation_unavailable", control_frame_resolution_reason="tip_orientation_missing", resolved_world_endpoint_velocity_m_s=None, endpoint_delta_requested_m=None, endpoint_delta_achieved_m=(0.0, 0.0, 0.0), qpos_after_rad=(0.0, 0.1), candidate_qpos_rad=(0.0, 0.1), measured_tip_position_after_m=(0.0, 0.0, 0.0), actual_tip_delta_m=(0.0, 0.0, 0.0), motion_status="held", motion_rejection_reason="resolution_failed")
    for changed in ({"motion_status": "accepted"}, {"motion_rejection_reason": None}, {"candidate_qpos_rad": (0.01, 0.1)}, {"endpoint_delta_achieved_m": (0.1, 0.0, 0.0)}, {"qpos_after_rad": (0.01, 0.1)}, {"actual_tip_delta_m": (0.1, 0.0, 0.0), "measured_tip_position_after_m": (0.1, 0.0, 0.0)}):
        with pytest.raises(ValueError):
            sample(**{**base, **changed})


@pytest.mark.parametrize("bad_sample", [
    sample(0, motion_status="held", motion_rejection_reason="held"),
    sample(0, target_rejected=True, target_rejection_reason="workspace"),
    sample(0, source_active=False, stale_reason="stale"),
    unavailable_sample(0),
])
def test_success_rejects_any_non_success_sample_axis(bad_sample: MotionSampleRecord) -> None:
    with pytest.raises(ValueError, match="cannot contain|complete measured"):
        validate_record_stream([configuration(dwell_interval_s=0.0), start(), bad_sample, outcome(primary_outcome_sample_index=0, final_measured_endpoint_error_m=sqrt(0.0003**2 + 0.0001**2))])


def test_success_primary_sample_must_be_final() -> None:
    with pytest.raises(ValueError, match="final motion sample"):
        validate_record_stream([configuration(), start(), sample(0), sample(1), outcome(primary_outcome_sample_index=0, final_measured_endpoint_error_m=sqrt(0.0003**2 + 0.0001**2))])


def test_trial_condition_must_match_sample_request_frame() -> None:
    tool = sample(requested_control_frame="tool", control_frame_resolution_status="tool_orientation_resolved")
    with pytest.raises(ValueError, match="control condition"):
        validate_record_stream([configuration(), start(), tool])


def test_initial_state_and_trajectory_continuity_are_enforced() -> None:
    with pytest.raises(ValueError, match="initial qpos"):
        validate_record_stream([configuration(), start(), sample(0, qpos_before_rad=(0.2, 0.1))])
    with pytest.raises(ValueError, match="initial tip"):
        validate_record_stream([configuration(), start(), sample(0, measured_tip_position_before_m=(0.2, 0.0, 0.0), actual_tip_delta_m=(-0.1993, 0.0001, 0.0))])
    with pytest.raises(ValueError, match="qpos trajectory"):
        validate_record_stream([configuration(), start(), sample(0), sample(1, qpos_before_rad=(0.5, 0.1))])
    with pytest.raises(ValueError, match="tip trajectory"):
        validate_record_stream([configuration(), start(), sample(0), sample(1, measured_tip_position_before_m=(0.5, 0.0, 0.0), actual_tip_delta_m=(-0.4992, 0.0001, 0.0))])


@pytest.mark.parametrize("changes", [
    {"completion_status": "success", "failure_attribution": "operator", "outcome_reason": "bad", "success_within_timeout": False},
    {"completion_status": "failed", "failure_attribution": "none", "outcome_reason": None, "success_within_timeout": False},
    {"completion_status": "failed", "failure_attribution": "technical", "outcome_reason": "bad", "success_within_timeout": False},
    {"completion_status": "technical_invalid", "failure_attribution": "operator", "outcome_reason": "bad", "success_within_timeout": False},
])
def test_outcome_classification_is_closed(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        outcome(**changes)


def test_p16_axis_norm_velocity_and_zero_speed_semantics() -> None:
    with pytest.raises(ValueError, match="norm"):
        validate_record_stream([configuration(), start(), sample(0, axis_values=(1.0, 1.0, 0.0), local_endpoint_velocity_m_s=(0.1, 0.1, 0.0))])
    with pytest.raises(ValueError, match="speed times"):
        validate_record_stream([configuration(), start(), sample(0, local_endpoint_velocity_m_s=(0.2, 0.0, 0.0))])
    validate_record_stream([configuration(local_endpoint_speed_m_s=0.0, dwell_interval_s=0.0), start(), sample(0, local_endpoint_velocity_m_s=(0.0, 0.0, 0.0)), invalid_outcome()])


def test_source_and_target_manifest_identity_are_enforced() -> None:
    with pytest.raises(ValueError, match="source_kind"):
        validate_record_stream([configuration(), start(), sample(0, source_kind="gamepad")])
    with pytest.raises(ValueError, match="target_id"):
        validate_record_stream([configuration(target_id="manifest-target"), start()])


def operator_failure(**changes: object) -> TrialOutcomeRecord:
    values = dict(completion_status="failed", success_within_timeout=False, failure_attribution="operator", outcome_reason="timeout")
    values.update(changes)
    return outcome(**values)


def test_failed_outcome_rejects_missing_sample_and_error_mismatch() -> None:
    with pytest.raises(ValueError, match="final motion sample"):
        validate_record_stream([configuration(), start(), sample(0), operator_failure(primary_outcome_sample_index=5)])
    with pytest.raises(ValueError, match="disagrees"):
        validate_record_stream([configuration(), start(), sample(0), operator_failure(primary_outcome_sample_index=0, final_measured_endpoint_error_m=0.0)])


def test_non_success_measured_reference_fields_are_all_or_none() -> None:
    with pytest.raises(ValueError, match="both present or both null"):
        validate_record_stream([configuration(), start(), sample(0), operator_failure(primary_outcome_sample_index=None)])
    with pytest.raises(ValueError, match="both present or both null"):
        validate_record_stream([configuration(), start(), sample(0), operator_failure(final_measured_endpoint_error_m=None)])


def test_measurement_unavailable_technical_invalid_cannot_claim_measured_error() -> None:
    with pytest.raises(ValueError, match="complete measured sample"):
        validate_record_stream([configuration(), start(), unavailable_sample(), invalid_outcome(primary_outcome_sample_index=0, final_measured_endpoint_error_m=0.1)])


def test_non_success_outcome_may_explicitly_omit_final_measured_evidence() -> None:
    validate_record_stream([configuration(), start(), unavailable_sample(), invalid_outcome()])
    validate_record_stream([configuration(), start(), sample(0), operator_failure(primary_outcome_sample_index=None, final_measured_endpoint_error_m=None)])


def _invalid_attempt(trial_id: str, attempt_index: int, retry_of: str | None, start_time: float) -> list[object]:
    return [
        start(trial_id, attempt_index=attempt_index, retry_of_trial_id=retry_of, runtime_timestamp_s=start_time),
        unavailable_sample(0, trial_id=trial_id, source_timestamp_s=start_time + 0.1, runtime_timestamp_s=start_time + 0.1),
        invalid_outcome(trial_id=trial_id, runtime_timestamp_s=start_time + 0.2),
    ]


def test_retry_sibling_duplicate_attempt_and_duplicate_initial_are_rejected() -> None:
    original = _invalid_attempt("trial-1", 0, None, 1.0)
    first_retry = _invalid_attempt("trial-2", 1, "trial-1", 2.0)
    with pytest.raises(ValueError, match="unique|direct retry"):
        validate_record_stream([configuration(), *original, *first_retry, start("trial-3", attempt_index=1, retry_of_trial_id="trial-1", runtime_timestamp_s=3.0)])
    with pytest.raises(ValueError, match="unique"):
        validate_record_stream([configuration(), *original, *first_retry, start("trial-3", attempt_index=1, retry_of_trial_id="trial-2", runtime_timestamp_s=3.0)])
    with pytest.raises(ValueError, match="unique|initial attempt"):
        validate_record_stream([configuration(), *original, start("trial-2", runtime_timestamp_s=2.0)])


def test_valid_retry_chain_is_linear() -> None:
    records = [configuration(), *_invalid_attempt("trial-1", 0, None, 1.0), *_invalid_attempt("trial-2", 1, "trial-1", 2.0), *_invalid_attempt("trial-3", 2, "trial-2", 3.0)]
    validate_record_stream(records)


@pytest.mark.parametrize("status", ["world_passthrough", "invalid_control_frame_defaulted"])
def test_world_resolved_passthrough_velocity_must_match_local_velocity(status: str) -> None:
    with pytest.raises(ValueError, match="passthrough velocity"):
        sample(control_frame_resolution_status=status, control_frame_resolution_reason="invalid_default" if status == "invalid_control_frame_defaulted" else None, resolved_world_endpoint_velocity_m_s=(0.0, 0.1, 0.0))


def test_tool_resolution_may_rotate_velocity() -> None:
    record = sample(requested_control_frame="tool", control_frame_resolution_status="tool_orientation_resolved", resolved_world_endpoint_velocity_m_s=(0.0, 0.1, 0.0))
    assert record.resolved_world_endpoint_velocity_m_s != record.local_endpoint_velocity_m_s


@pytest.mark.parametrize(("config_changes", "outcome_changes", "message"), [({}, {"final_measured_endpoint_error_m": 0.0}, "disagrees"), ({"target_tolerance_m": 0.0001}, {}, "target tolerance"), ({"timeout_s": 0.15}, {}, "timeout"), ({"dwell_interval_s": 0.2}, {}, "dwell")])
def test_success_requires_consistent_error_tolerance_timeout_and_dwell(config_changes: dict[str, object], outcome_changes: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_record_stream([configuration(**config_changes), start(), sample(0), sample(1), outcome(**outcome_changes)])


def test_measured_delta_must_equal_after_minus_before() -> None:
    with pytest.raises(ValueError, match="after minus before"):
        sample(actual_tip_delta_m=(0.0, 0.0, 0.0))


def test_unavailable_measurement_rejects_fabricated_progress_and_available_rejects_reason() -> None:
    with pytest.raises(ValueError, match="progress values"):
        unavailable_sample(endpoint_progress_ratio=0.0)
    with pytest.raises(ValueError, match="cannot have"):
        sample(measurement_unavailable_reason="missing")
    with pytest.raises(ValueError, match="measurement_unavailable progress"):
        sample(endpoint_progress_status="measurement_unavailable")


@pytest.mark.parametrize("orientation", [(0.0, 0.0, 0.0, 0.0), (2.0, 0.0, 0.0, 0.0), (float("nan"), 0.0, 0.0, 0.0)])
def test_initial_orientation_requires_finite_unit_quaternion(orientation: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        configuration(initial_tool_orientation_wxyz=orientation)


def test_unsupported_version_unknown_fields_and_kind_are_rejected() -> None:
    value = record_to_json_value(configuration())
    with pytest.raises(ValueError, match="unsupported schema_version"):
        parse_record({**value, "schema_version": "experiment-motion-log/v2"})
    with pytest.raises(ValueError, match="unknown fields"):
        parse_record({**value, "future_field": 1})
    with pytest.raises(ValueError, match="unsupported record_kind"):
        parse_record({**value, "record_kind": "future"})
