from __future__ import annotations

from pathlib import Path

import pytest

from xpotato_sim.runtime.experiment import motion_log_recorder
from xpotato_sim.runtime.experiment.motion_log_recorder import (
    ExperimentMotionLogRecordingError,
    prepare_motion_log,
    write_motion_log_atomic,
)
from xpotato_sim.schemas.experiment_log import (
    ConfigurationRecord,
    TrialOutcomeRecord,
    TrialStartRecord,
    decode_jsonl,
    validate_record_stream,
)


def _technical_invalid_stream() -> tuple[object, ...]:
    configuration = ConfigurationRecord(
        experiment_id="experiment-407",
        session_id="session-407",
        participant_id="opaque-participant-407",
        configuration_id="sha256:configuration-407",
        software_revision="test-revision:issue-407",
        initial_qpos_rad=(0.0, 0.0),
        initial_measured_tip_position_m=(0.0, 0.0, 0.0),
        initial_tool_orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
        target_world_position_m=(0.1, 0.0, 0.0),
        target_tolerance_m=0.001,
        dwell_interval_s=0.1,
        timeout_s=5.0,
        source_kind="analog_fixture",
        target_id="target-407",
        local_endpoint_speed_m_s=0.1,
        deadzone=0.0,
        local_endpoint_max_delta_m=0.002,
    )
    start = TrialStartRecord(
        experiment_id=configuration.experiment_id,
        session_id=configuration.session_id,
        participant_id=configuration.participant_id,
        configuration_id=configuration.configuration_id,
        trial_id="trial-407",
        block_id="block-0",
        task_family="endpoint-reach",
        target_id=configuration.target_id,
        practice=False,
        control_condition="world",
        condition_order=0,
        task_order=0,
        target_direction="positive-y",
        direction_order=0,
        repetition_index=0,
        attempt_index=0,
        retry_of_trial_id=None,
        runtime_timestamp_s=0.0,
    )
    outcome = TrialOutcomeRecord(
        experiment_id=configuration.experiment_id,
        session_id=configuration.session_id,
        participant_id=configuration.participant_id,
        configuration_id=configuration.configuration_id,
        trial_id=start.trial_id,
        runtime_timestamp_s=0.02,
        completion_status="technical_invalid",
        success_within_timeout=False,
        final_measured_endpoint_error_m=None,
        failure_attribution="technical",
        outcome_reason="input source read failed",
        subjective_response_link_id=None,
        primary_outcome_sample_index=None,
    )
    return configuration, start, outcome


def test_prepare_and_atomic_write_are_strict_and_deterministic(tmp_path: Path) -> None:
    records = _technical_invalid_stream()
    first = prepare_motion_log(records)  # type: ignore[arg-type]
    second = prepare_motion_log(decode_jsonl(first.text))

    assert first.bytes_value == second.bytes_value
    assert first.bytes_value.startswith(b"{")
    assert not first.bytes_value.startswith(b"\xef\xbb\xbf")

    target = tmp_path / "motion.jsonl"
    written = write_motion_log_atomic(target, records)  # type: ignore[arg-type]
    assert target.read_bytes() == written.bytes_value == first.bytes_value
    validate_record_stream(decode_jsonl(target.read_text(encoding="utf-8")))


def test_invalid_or_partial_stream_preserves_existing_artifact(tmp_path: Path) -> None:
    target = tmp_path / "motion.jsonl"
    original = b"previous-valid-artifact\n"
    target.write_bytes(original)
    configuration, start, _ = _technical_invalid_stream()

    with pytest.raises(ValueError, match="unclosed trials"):
        write_motion_log_atomic(target, (configuration, start))

    assert target.read_bytes() == original


def test_temporary_readback_failure_preserves_existing_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "motion.jsonl"
    original = b"previous-valid-artifact\n"
    target.write_bytes(original)
    real_read = motion_log_recorder._read_bytes

    def corrupt_temporary(path: Path) -> bytes:
        if path != target:
            return b"corrupt"
        return real_read(path)

    monkeypatch.setattr(motion_log_recorder, "_read_bytes", corrupt_temporary)

    with pytest.raises(
        ExperimentMotionLogRecordingError,
        match="temporary motion log strict read-back mismatch",
    ):
        write_motion_log_atomic(target, _technical_invalid_stream())  # type: ignore[arg-type]

    assert target.read_bytes() == original
    assert not tuple(tmp_path.glob("*.tmp"))


def test_final_readback_failure_rolls_back_existing_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "motion.jsonl"
    original = b"previous-valid-artifact\n"
    target.write_bytes(original)
    real_read = motion_log_recorder._read_bytes
    target_reads = 0

    def corrupt_final(path: Path) -> bytes:
        nonlocal target_reads
        if path == target:
            target_reads += 1
            if target_reads == 2:
                return b"corrupt"
        return real_read(path)

    monkeypatch.setattr(motion_log_recorder, "_read_bytes", corrupt_final)

    with pytest.raises(
        ExperimentMotionLogRecordingError,
        match="final motion log strict read-back mismatch",
    ):
        write_motion_log_atomic(target, _technical_invalid_stream())  # type: ignore[arg-type]

    assert target.read_bytes() == original
