from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.control.input_source_state import RuntimeInputSourceState
from xpotato_sim.runtime.evaluation.manifest import (
    SoftwareExecutionIdentity,
    build_evaluation_condition_pair_readiness,
)
from xpotato_sim.runtime.evaluation.r7_g_free_space import (
    build_r7_g_free_space_manifest_pair,
)
from xpotato_sim.runtime.experiment.contracts import TaskTerminalClassification
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    decode_endpoint_reach_terminal_evidence,
    decode_endpoint_reach_trajectory_evidence,
)
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
    InputSourcePlugin,
)
from xpotato_sim.runtime.experiment.motion_log_recorder import (
    ExperimentMotionLogRecordingError,
    TrialProtocolContext,
    WorldToolTrialProtocolContext,
    build_condition_motion_log_records,
    build_world_tool_motion_log_records,
    prepare_motion_log,
    run_r7_g_world_tool_experiment_and_record,
)
from xpotato_sim.runtime.experiment.world_tool_runner import (
    ExperimentStopReason,
    run_evaluation_condition_pair,
    run_experiment_condition,
    run_r7_g_world_tool_experiment,
)
from xpotato_sim.schemas.experiment_log import (
    ConfigurationRecord,
    MotionSampleRecord,
    TrialOutcomeRecord,
    TrialStartRecord,
    decode_jsonl,
    validate_record_stream,
)


REVISION = "test-revision:issue-407-canonical-recorder"
EXECUTION_IDENTITY = SoftwareExecutionIdentity(
    repository_identity="Xpotato1024/Xpotato-Sim",
    software_revision_identity=REVISION,
)


def _contexts() -> WorldToolTrialProtocolContext:
    common = dict(
        experiment_id="experiment-407",
        session_id="session-407",
        participant_id="opaque-participant-407",
        block_id="block-0",
        task_family="endpoint-reach",
        practice=False,
        target_direction="positive-y",
        repetition_index=0,
        attempt_index=0,
        retry_of_trial_id=None,
    )
    return WorldToolTrialProtocolContext(
        world=TrialProtocolContext(**common, direction_order=0),
        tool=TrialProtocolContext(**common, direction_order=1),
    )


@pytest.fixture(scope="module")
def canonical_pair():
    pair = build_r7_g_free_space_manifest_pair(
        software_revision_identity=REVISION
    )
    readiness = build_evaluation_condition_pair_readiness(
        pair,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=EXECUTION_IDENTITY,
    )
    return readiness, run_evaluation_condition_pair(readiness)


def _trial_records(records: tuple[object, ...], control_condition: str):
    start = next(
        record
        for record in records
        if isinstance(record, TrialStartRecord)
        and record.control_condition == control_condition
    )
    configuration = next(
        record
        for record in records
        if isinstance(record, ConfigurationRecord)
        and record.configuration_id == start.configuration_id
    )
    samples = tuple(
        record
        for record in records
        if isinstance(record, MotionSampleRecord)
        and record.trial_id == start.trial_id
    )
    outcome = next(
        record
        for record in records
        if isinstance(record, TrialOutcomeRecord)
        and record.trial_id == start.trial_id
    )
    return configuration, start, samples, outcome


def _execution_signature(result):
    return (
        result.condition_id,
        result.requested_control_frame,
        result.freeze_record,
        result.transition.classification,
        decode_endpoint_reach_terminal_evidence(result.transition.evidence),
        decode_endpoint_reach_trajectory_evidence(result.transition.evidence),
        result.step_count,
        result.final_elapsed_time_s,
        result.stop_reason,
        result.initial_measured_qpos_rad,
        result.initial_measured_endpoint_world_m,
        result.initial_measured_tool_orientation_wxyz,
        result.final_measured_endpoint_world_m,
        tuple(
            (
                trace.sample_index,
                trace.runtime_timestamp_s,
                trace.pre_state.qpos,
                trace.post_state.qpos,
                trace.pre_endpoint_world_m,
                trace.post_endpoint_world_m,
                trace.task_observation,
            )
            for trace in result.motion_steps
        ),
    )


def test_canonical_pair_records_complete_deterministic_v1_lifecycle(
    tmp_path: Path,
) -> None:
    first = run_r7_g_world_tool_experiment_and_record(
        manifest_software_revision_identity=REVISION,
        execution_identity=EXECUTION_IDENTITY,
        contexts=_contexts(),
        output_path=tmp_path / "first.jsonl",
    )
    second = run_r7_g_world_tool_experiment_and_record(
        manifest_software_revision_identity=REVISION,
        execution_identity=EXECUTION_IDENTITY,
        contexts=_contexts(),
        output_path=tmp_path / "second.jsonl",
    )
    plain = run_r7_g_world_tool_experiment(
        manifest_software_revision_identity=REVISION,
        execution_identity=EXECUTION_IDENTITY,
    )

    assert first.jsonl_bytes == second.jsonl_bytes
    assert first.execution.pair_identity == second.execution.pair_identity == plain.pair_identity
    for logged_first, logged_second, unlogged in (
        (first.execution.world, second.execution.world, plain.world),
        (first.execution.tool, second.execution.tool, plain.tool),
    ):
        assert (
            _execution_signature(logged_first)
            == _execution_signature(logged_second)
            == _execution_signature(unlogged)
        )
    assert first.records == second.records == decode_jsonl(first.jsonl_bytes.decode())
    assert len(first.records) == 313
    validate_record_stream(first.records)
    assert prepare_motion_log(first.records).bytes_value == first.jsonl_bytes

    assert (
        first.execution.world.classification,
        first.execution.world.step_count,
        first.execution.world.final_elapsed_time_s,
        first.execution.world.stop_reason,
    ) == (
        TaskTerminalClassification.SUCCESS,
        57,
        pytest.approx(1.14),
        ExperimentStopReason.TASK_TERMINAL,
    )
    assert (
        first.execution.tool.classification,
        first.execution.tool.step_count,
        first.execution.tool.final_elapsed_time_s,
        first.execution.tool.stop_reason,
    ) == (
        TaskTerminalClassification.FAILURE,
        250,
        pytest.approx(5.0),
        ExperimentStopReason.TASK_TERMINAL,
    )

    world = _trial_records(first.records, "world")
    tool = _trial_records(first.records, "tool")
    assert len(world[2]) == 57 and len(world[2]) + 3 == 60
    assert len(tool[2]) == 250 and len(tool[2]) + 3 == 253
    assert world[3].completion_status == "success"
    assert world[3].success_within_timeout is True
    assert world[3].failure_attribution == "none"
    assert tool[3].completion_status == "failed"
    assert tool[3].success_within_timeout is False
    assert tool[3].failure_attribution == "operator"
    assert [sample.sample_index for sample in world[2]] == list(range(57))
    assert [sample.sample_index for sample in tool[2]] == list(range(250))
    for samples in (world[2], tool[2]):
        for before, after in zip(samples, samples[1:], strict=False):
            assert before.qpos_after_rad == after.qpos_before_rad
            assert (
                before.measured_tip_position_after_m
                == after.measured_tip_position_before_m
            )


def test_projection_keeps_owner_truth_levels_distinct(canonical_pair) -> None:
    readiness, execution = canonical_pair
    records = build_condition_motion_log_records(
        readiness.world,
        execution.world,
        _contexts().world,
    )
    configuration, _, samples, _ = _trial_records(records, "world")
    trace = execution.world.motion_steps[10]
    sample = samples[10]
    input_metadata = trace.intent.metadata
    motion_metadata = trace.safety_result.motion_command.metadata

    assert configuration.configuration_id == readiness.world.freeze_record.identity
    assert configuration.initial_qpos_rad == execution.world.initial_measured_qpos_rad
    assert (
        configuration.initial_measured_tip_position_m
        == execution.world.initial_measured_endpoint_world_m
    )
    assert sample.source_timestamp_s == input_metadata["source_timestamp_s"]
    assert sample.runtime_timestamp_s == trace.runtime_timestamp_s
    assert sample.axis_values == input_metadata["axis_values"]
    assert (
        sample.local_endpoint_velocity_m_s
        == input_metadata["local_endpoint_velocity_m_s"]
    )
    assert sample.endpoint_delta_requested_m == motion_metadata["endpoint_delta_requested_m"]
    assert sample.endpoint_delta_achieved_m == motion_metadata["endpoint_delta_achieved_m"]
    assert sample.candidate_qpos_rad == motion_metadata["candidate_qpos_rad"]
    assert sample.qpos_before_rad == trace.pre_state.qpos
    assert sample.qpos_after_rad == trace.post_state.qpos
    assert sample.measured_tip_position_before_m == trace.pre_endpoint_world_m
    assert sample.measured_tip_position_after_m == trace.post_endpoint_world_m
    assert sample.actual_tip_delta_m == tuple(
        trace.post_endpoint_world_m[index] - trace.pre_endpoint_world_m[index]
        for index in range(3)
    )


def test_projection_preserves_stale_held_rejected_and_unavailable_axes(
    canonical_pair,
) -> None:
    readiness, execution = canonical_pair
    base = execution.world.motion_steps[0]
    source_state = RuntimeInputSourceState(
        source_kind=base.safety_result.source_state.source_kind,
        source_active=False,
        stale_reason="fixture-stale",
    )
    metadata = dict(base.safety_result.motion_command.metadata)
    metadata.update(
        motion_status="accepted",
        qpos_rejection_reason="joint-limit",
        target_rejected=True,
        target_rejection_reason="workspace-limit",
    )
    safety = replace(
        base.safety_result,
        motion_command=replace(base.safety_result.motion_command, metadata=metadata),
        source_state=source_state,
        is_stale=True,
        stale_reason="fixture-stale",
        should_update_target_position_m=False,
        qpos_feasibility_rejected=True,
    )
    input_metadata = dict(base.intent.metadata)
    input_metadata.update(source_active=False, stale_reason="fixture-stale")
    changed = replace(
        base,
        intent=replace(base.intent, metadata=input_metadata),
        safety_result=safety,
        pre_endpoint_world_m=None,
        post_endpoint_world_m=None,
    )
    changed_execution = replace(
        execution.world,
        motion_steps=(changed, *execution.world.motion_steps[1:]),
    )
    records = build_condition_motion_log_records(
        readiness.world,
        changed_execution,
        _contexts().world,
    )
    sample = next(record for record in records if isinstance(record, MotionSampleRecord))

    assert sample.source_active is False
    assert sample.stale_reason == "fixture-stale"
    assert sample.motion_status == "held"
    assert sample.motion_rejection_reason == "joint-limit"
    assert sample.target_rejected is True
    assert sample.target_rejection_reason == "workspace-limit"
    assert sample.measured_tip_position_before_m is None
    assert sample.measured_tip_position_after_m is None
    assert sample.actual_tip_delta_m is None
    assert sample.endpoint_progress_status == "measurement_unavailable"
    assert sample.endpoint_progress_measurement_available is False
    assert sample.measurement_unavailable_reason is not None


def test_running_partial_trial_cannot_be_prepared_or_committed(
    canonical_pair,
    tmp_path: Path,
) -> None:
    readiness, execution = canonical_pair
    partial = replace(
        execution.world,
        transition=replace(
            execution.world.transition,
            classification=TaskTerminalClassification.RUNNING,
        ),
        stop_reason=ExperimentStopReason.BOUNDED_STEP_LIMIT,
    )
    target = tmp_path / "existing.jsonl"
    target.write_bytes(b"previous-valid-artifact\n")

    with pytest.raises(ExperimentMotionLogRecordingError, match="partial RUNNING"):
        build_condition_motion_log_records(
            readiness.world,
            partial,
            _contexts().world,
        )

    assert target.read_bytes() == b"previous-valid-artifact\n"


def test_outcome_timestamp_must_match_task_terminal_evidence(canonical_pair) -> None:
    readiness, execution = canonical_pair
    inconsistent = replace(execution.world, final_elapsed_time_s=999.0)

    with pytest.raises(
        ExperimentMotionLogRecordingError,
        match="terminal evidence and execution summary elapsed time disagree",
    ):
        build_condition_motion_log_records(
            readiness.world,
            inconsistent,
            _contexts().world,
        )


class _FailingReader:
    def read_frame(self):
        raise RuntimeError("fixture read failed")

    def current_health(self) -> InputSourceHealth:
        return InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0)


def _failing_reader_factory(parameters, *, runtime_dependencies=None):
    _ = (parameters, runtime_dependencies)
    return _FailingReader()


def test_technical_invalid_zero_sample_trial_can_retry_once(canonical_pair) -> None:
    readiness, execution = canonical_pair
    plugin = readiness.world.composition.input_source
    failing_plugin = InputSourcePlugin(
        identity=plugin.identity,
        produced_sample_schema=plugin.produced_sample_schema,
        mode=plugin.mode,
        factory=_failing_reader_factory,
        parameter_contract=plugin.parameter_contract,
        initial_health=plugin.initial_health,
        initial_metadata=plugin.initial_metadata,
        produced_evidence=plugin.produced_evidence,
        mapping_input_adapter=plugin.mapping_input_adapter,
    )
    failing_readiness = replace(
        readiness.world,
        composition=replace(
            readiness.world.composition,
            input_source=failing_plugin,
        ),
    )
    invalid_execution = run_experiment_condition(failing_readiness)
    prior = build_condition_motion_log_records(
        failing_readiness,
        invalid_execution,
        _contexts().world,
    )
    validate_record_stream(prior)
    previous_start = next(
        record for record in prior if isinstance(record, TrialStartRecord)
    )
    previous_outcome = next(
        record for record in prior if isinstance(record, TrialOutcomeRecord)
    )

    assert not any(isinstance(record, MotionSampleRecord) for record in prior)
    assert previous_outcome.completion_status == "technical_invalid"
    assert previous_outcome.failure_attribution == "technical"
    retry_contexts = replace(
        _contexts(),
        world=replace(
            _contexts().world,
            attempt_index=1,
            retry_of_trial_id=previous_start.trial_id,
        ),
    )
    combined = build_world_tool_motion_log_records(
        readiness,
        execution,
        retry_contexts,
        prior_records=prior,
    )
    validate_record_stream(combined)
    starts = [record for record in combined if isinstance(record, TrialStartRecord)]
    retry = next(record for record in starts if record.attempt_index == 1)
    assert retry.retry_of_trial_id == previous_start.trial_id

    with pytest.raises(ValueError, match="duplicate trial_id|direct retry child"):
        build_world_tool_motion_log_records(
            readiness,
            execution,
            retry_contexts,
            prior_records=combined,
        )
