"""R7-G execution factsをexperiment-motion-log/v1へ記録する。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import dist, isclose, isfinite
from pathlib import Path

from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.evaluation.endpoint_progress import (
    calculate_endpoint_progress,
)
from xpotato_sim.runtime.evaluation.manifest import (
    EvaluationConditionPairReadiness,
    EvaluationReadiness,
    SoftwareExecutionIdentity,
    build_evaluation_condition_pair_readiness,
    comparison_parameters_for_readiness,
)
from xpotato_sim.runtime.evaluation.r7_g_free_space import (
    build_r7_g_free_space_manifest_pair,
)
from xpotato_sim.runtime.experiment.contracts import TaskTerminalClassification
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    decode_endpoint_reach_terminal_evidence,
)
from xpotato_sim.runtime.experiment.world_tool_runner import (
    ExperimentConditionExecutionResult,
    ExperimentMotionStepTrace,
    WorldToolExperimentExecutionResult,
    composition_source_kind,
    run_evaluation_condition_pair,
)
from xpotato_sim.schemas.experiment_log import (
    ConfigurationRecord,
    ExperimentMotionLogRecord,
    MotionSampleRecord,
    TrialOutcomeRecord,
    TrialStartRecord,
    decode_jsonl,
    encode_jsonl,
    validate_record_stream,
)


class ExperimentMotionLogRecordingError(RuntimeError):
    """completeなv1 streamを正直に生成または保存できない場合のerror。"""


@dataclass(frozen=True, slots=True)
class TrialProtocolContext:
    """callerが明示するparticipant非依存のtrial protocol identity。"""

    experiment_id: str
    session_id: str
    participant_id: str
    block_id: str
    task_family: str
    practice: bool
    target_direction: str
    direction_order: int
    repetition_index: int
    attempt_index: int
    retry_of_trial_id: str | None
    subjective_response_link_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorldToolTrialProtocolContext:
    """world/toolを独立したtraceable trialへbindするcontext pair。"""

    world: TrialProtocolContext
    tool: TrialProtocolContext


@dataclass(frozen=True, slots=True)
class PreparedExperimentMotionLog:
    """strict round-tripを通過したatomic write入力。"""

    records: tuple[ExperimentMotionLogRecord, ...]
    text: str
    bytes_value: bytes


@dataclass(frozen=True, slots=True)
class RecordedWorldToolExperiment:
    """execution resultと保存済みv1 streamを結ぶruntime result。"""

    execution: WorldToolExperimentExecutionResult
    records: tuple[ExperimentMotionLogRecord, ...]
    jsonl_bytes: bytes
    output_path: Path


def _required_string(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentMotionLogRecordingError(f"{name} must be a non-empty string")
    return value


def _optional_string(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _required_string(name, value)


def _required_bool(name: str, value: object) -> bool:
    if type(value) is not bool:
        raise ExperimentMotionLogRecordingError(f"{name} must be a boolean")
    return value


def _number(name: str, value: object) -> float:
    if type(value) not in (int, float) or not isfinite(float(value)):
        raise ExperimentMotionLogRecordingError(f"{name} must be a finite number")
    return float(value)


def _vector(name: str, value: object, *, length: int | None = None) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ExperimentMotionLogRecordingError(f"{name} must be a numeric sequence")
    result = tuple(_number(f"{name}[{index}]", item) for index, item in enumerate(value))
    if length is not None and len(result) != length:
        raise ExperimentMotionLogRecordingError(f"{name} must contain {length} values")
    if not result:
        raise ExperimentMotionLogRecordingError(f"{name} must not be empty")
    return result


def _optional_vector(
    name: str,
    value: object,
    *,
    length: int | None = None,
) -> tuple[float, ...] | None:
    return None if value is None else _vector(name, value, length=length)


def _trial_id(
    readiness: EvaluationReadiness,
    context: TrialProtocolContext,
) -> str:
    manifest = readiness.manifest
    identity = {
        "attempt_index": context.attempt_index,
        "block_id": context.block_id,
        "condition_order": manifest.condition_order,
        "configuration_id": readiness.freeze_record.identity,
        "direction_order": context.direction_order,
        "experiment_id": context.experiment_id,
        "participant_id": context.participant_id,
        "practice": context.practice,
        "repetition_index": context.repetition_index,
        "retry_of_trial_id": context.retry_of_trial_id,
        "session_id": context.session_id,
        "target_direction": context.target_direction,
        "target_id": manifest.target_id,
        "task_family": context.task_family,
        "task_order": manifest.task_order,
    }
    material = json.dumps(
        identity,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"trial-{sha256(material).hexdigest()}"


def _configuration_record(
    readiness: EvaluationReadiness,
    result: ExperimentConditionExecutionResult,
    context: TrialProtocolContext,
) -> ConfigurationRecord:
    if result.freeze_record != readiness.freeze_record:
        raise ExperimentMotionLogRecordingError("execution/readiness freeze identity mismatch")
    if result.condition_id != readiness.manifest.condition_id:
        raise ExperimentMotionLogRecordingError("execution/readiness condition identity mismatch")
    if result.initial_measured_endpoint_world_m is None:
        raise ExperimentMotionLogRecordingError("initial measured endpoint is unavailable")
    qpos_count = len(readiness.manifest.initial_qpos_rad)
    if len(result.initial_measured_qpos_rad) < qpos_count:
        raise ExperimentMotionLogRecordingError("initial measured qpos is incomplete")
    return ConfigurationRecord(
        experiment_id=context.experiment_id,
        session_id=context.session_id,
        participant_id=context.participant_id,
        configuration_id=readiness.freeze_record.identity,
        software_revision=(
            readiness.software_execution_identity.software_revision_identity
        ),
        initial_qpos_rad=result.initial_measured_qpos_rad[:qpos_count],
        initial_measured_tip_position_m=result.initial_measured_endpoint_world_m,
        initial_tool_orientation_wxyz=result.initial_measured_tool_orientation_wxyz,
        target_world_position_m=readiness.manifest.target_world_position_m,
        target_tolerance_m=readiness.manifest.target_tolerance_m,
        dwell_interval_s=readiness.manifest.dwell_interval_s,
        timeout_s=readiness.manifest.timeout_s,
        source_kind=composition_source_kind(readiness),
        target_id=readiness.manifest.target_id,
        local_endpoint_speed_m_s=readiness.manifest.gain,
        deadzone=readiness.manifest.deadzone,
        local_endpoint_max_delta_m=readiness.manifest.maximum_per_step_delta_m,
        comparison_parameters=comparison_parameters_for_readiness(readiness),
    )


def _trial_start_record(
    readiness: EvaluationReadiness,
    context: TrialProtocolContext,
    *,
    trial_id: str,
) -> TrialStartRecord:
    manifest = readiness.manifest
    return TrialStartRecord(
        experiment_id=context.experiment_id,
        session_id=context.session_id,
        participant_id=context.participant_id,
        configuration_id=readiness.freeze_record.identity,
        trial_id=trial_id,
        block_id=context.block_id,
        task_family=context.task_family,
        target_id=manifest.target_id,
        practice=context.practice,
        control_condition=manifest.requested_control_frame,
        condition_order=manifest.condition_order,
        task_order=manifest.task_order,
        target_direction=context.target_direction,
        direction_order=context.direction_order,
        repetition_index=context.repetition_index,
        attempt_index=context.attempt_index,
        retry_of_trial_id=context.retry_of_trial_id,
        runtime_timestamp_s=0.0,
    )


def _motion_status(trace: ExperimentMotionStepTrace) -> tuple[str, str | None]:
    metadata = trace.safety_result.motion_command.metadata
    if trace.safety_result.qpos_feasibility_rejected:
        return (
            "held",
            _optional_string(
                "qpos_rejection_reason",
                metadata.get("qpos_rejection_reason"),
            )
            or "qpos_feasibility_rejected",
        )
    return (
        _required_string("motion_status", metadata.get("motion_status")),
        _optional_string(
            "motion_rejection_reason",
            metadata.get("motion_rejection_reason"),
        ),
    )


def _sample_record(
    readiness: EvaluationReadiness,
    trace: ExperimentMotionStepTrace,
    context: TrialProtocolContext,
    *,
    trial_id: str,
) -> MotionSampleRecord:
    input_metadata = trace.intent.metadata
    motion_metadata = trace.safety_result.motion_command.metadata
    motion_status, motion_rejection_reason = _motion_status(trace)
    requested_delta = _optional_vector(
        "endpoint_delta_requested_m",
        motion_metadata.get("endpoint_delta_requested_m"),
        length=3,
    )

    measured_before = trace.pre_endpoint_world_m
    measured_after = trace.post_endpoint_world_m
    if measured_before is None or measured_after is None:
        measured_before = None
        measured_after = None
        actual_delta = None
        progress_status = "measurement_unavailable"
        progress_signed = None
        progress_ratio = None
        progress_cosine = None
        progress_requested_norm = None
        progress_measured_norm = None
        progress_available = False
        measurement_reason = (
            trace.task_observation.reason
            or "pre_or_post_endpoint_measurement_unavailable"
        )
    else:
        actual_delta = tuple(
            measured_after[index] - measured_before[index] for index in range(3)
        )
        measurement_reason = None
        progress_available = True
        if requested_delta is None:
            progress_status = "not_requested"
            progress_signed = None
            progress_ratio = None
            progress_cosine = None
            progress_requested_norm = None
            progress_measured_norm = None
        else:
            progress = calculate_endpoint_progress(requested_delta, actual_delta)
            progress_status = progress.status
            progress_signed = progress.signed_progress_m
            progress_ratio = progress.progress_ratio
            progress_cosine = progress.direction_cosine
            progress_requested_norm = progress.requested_norm_m
            progress_measured_norm = progress.measured_norm_m
            progress_available = progress.measurement_available

    source_kind = _required_string(
        "source_kind",
        input_metadata.get("source_kind", trace.intent.source),
    )
    if source_kind != trace.safety_result.source_state.source_kind:
        raise ExperimentMotionLogRecordingError(
            "input intent and runtime source state disagree on source_kind"
        )
    source_timestamp_s = _number(
        "source_timestamp_s",
        input_metadata.get("source_timestamp_s"),
    )
    source_active = _required_bool(
        "source_active",
        input_metadata.get("source_active"),
    )
    stale_reason = _optional_string(
        "stale_reason",
        input_metadata.get("stale_reason"),
    )
    source_state = trace.safety_result.source_state
    if (source_active, stale_reason) != (
        source_state.source_active,
        source_state.stale_reason,
    ):
        raise ExperimentMotionLogRecordingError(
            "input intent and runtime source state disagree on active/stale state"
        )
    requested_control_frame = _required_string(
        "requested_control_frame",
        input_metadata.get("control_frame"),
    )
    if requested_control_frame != motion_metadata.get("requested_control_frame"):
        raise ExperimentMotionLogRecordingError(
            "input intent and motion policy disagree on requested control frame"
        )
    target_rejected_value = motion_metadata.get("target_rejected", False)
    target_rejected = _required_bool("target_rejected", target_rejected_value)
    target_rejection_reason = _optional_string(
        "target_rejection_reason",
        motion_metadata.get("target_rejection_reason"),
    )

    return MotionSampleRecord(
        experiment_id=context.experiment_id,
        session_id=context.session_id,
        participant_id=context.participant_id,
        configuration_id=readiness.freeze_record.identity,
        trial_id=trial_id,
        sample_index=trace.sample_index,
        source_kind=source_kind,
        source_timestamp_s=source_timestamp_s,
        runtime_timestamp_s=trace.runtime_timestamp_s,
        source_active=source_active,
        axis_values=_vector("axis_values", input_metadata.get("axis_values"), length=3),
        zero_input=_required_bool("zero_input", input_metadata.get("zero_input")),
        stale_reason=stale_reason,
        requested_control_frame=requested_control_frame,
        local_endpoint_velocity_m_s=_vector(
            "local_endpoint_velocity_m_s",
            input_metadata.get("local_endpoint_velocity_m_s"),
            length=3,
        ),
        resolved_control_frame=_optional_string(
            "resolved_control_frame",
            motion_metadata.get("resolved_control_frame"),
        ),
        control_frame_resolution_status=_required_string(
            "control_frame_resolution_status",
            motion_metadata.get("control_frame_resolution_status"),
        ),
        control_frame_resolution_reason=_optional_string(
            "control_frame_resolution_reason",
            motion_metadata.get("control_frame_resolution_reason"),
        ),
        resolved_world_endpoint_velocity_m_s=_optional_vector(
            "resolved_world_endpoint_velocity_m_s",
            motion_metadata.get("resolved_world_endpoint_velocity_m_s"),
            length=3,
        ),
        endpoint_delta_requested_m=requested_delta,
        endpoint_delta_achieved_m=_optional_vector(
            "endpoint_delta_achieved_m",
            motion_metadata.get("endpoint_delta_achieved_m"),
            length=3,
        ),
        qpos_before_rad=tuple(trace.pre_state.qpos),
        qpos_after_rad=tuple(trace.post_state.qpos),
        candidate_qpos_rad=_optional_vector(
            "candidate_qpos_rad",
            motion_metadata.get("candidate_qpos_rad"),
        ),
        measured_tip_position_before_m=measured_before,
        measured_tip_position_after_m=measured_after,
        actual_tip_delta_m=actual_delta,
        motion_status=motion_status,
        motion_rejection_reason=motion_rejection_reason,
        target_rejected=target_rejected,
        target_rejection_reason=target_rejection_reason,
        endpoint_progress_status=progress_status,
        endpoint_progress_signed_m=progress_signed,
        endpoint_progress_ratio=progress_ratio,
        endpoint_progress_direction_cosine=progress_cosine,
        endpoint_progress_requested_norm_m=progress_requested_norm,
        endpoint_progress_measured_norm_m=progress_measured_norm,
        endpoint_progress_measurement_available=progress_available,
        measurement_unavailable_reason=measurement_reason,
    )


def _outcome_record(
    readiness: EvaluationReadiness,
    result: ExperimentConditionExecutionResult,
    context: TrialProtocolContext,
    samples: Sequence[MotionSampleRecord],
    *,
    trial_id: str,
) -> TrialOutcomeRecord:
    classification = result.classification
    if classification is TaskTerminalClassification.RUNNING:
        raise ExperimentMotionLogRecordingError(
            "RUNNING task cannot close an experiment-motion-log/v1 trial"
        )
    terminal = decode_endpoint_reach_terminal_evidence(result.transition.evidence)
    if terminal.classification is not classification:
        raise ExperimentMotionLogRecordingError(
            "Task transition and terminal evidence classification disagree"
        )
    if not isclose(
        terminal.elapsed_time_s,
        result.final_elapsed_time_s,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ExperimentMotionLogRecordingError(
            "Task terminal evidence and execution summary elapsed time disagree"
        )
    if classification is TaskTerminalClassification.SUCCESS:
        completion_status = "success"
        success_within_timeout = True
        failure_attribution = "none"
    elif classification is TaskTerminalClassification.FAILURE:
        completion_status = "failed"
        success_within_timeout = False
        failure_attribution = "operator"
    else:
        completion_status = "technical_invalid"
        success_within_timeout = False
        failure_attribution = "technical"

    primary_index: int | None = None
    final_error: float | None = None
    if samples and samples[-1].measured_tip_position_after_m is not None:
        primary_index = samples[-1].sample_index
        final_error = dist(
            samples[-1].measured_tip_position_after_m,
            readiness.manifest.target_world_position_m,
        )
    return TrialOutcomeRecord(
        experiment_id=context.experiment_id,
        session_id=context.session_id,
        participant_id=context.participant_id,
        configuration_id=readiness.freeze_record.identity,
        trial_id=trial_id,
        runtime_timestamp_s=terminal.elapsed_time_s,
        completion_status=completion_status,
        success_within_timeout=success_within_timeout,
        final_measured_endpoint_error_m=final_error,
        failure_attribution=failure_attribution,
        outcome_reason=terminal.reason,
        subjective_response_link_id=context.subjective_response_link_id,
        primary_outcome_sample_index=primary_index,
    )


def build_condition_motion_log_records(
    readiness: EvaluationReadiness,
    result: ExperimentConditionExecutionResult,
    context: TrialProtocolContext,
    *,
    include_configuration: bool = True,
) -> tuple[ExperimentMotionLogRecord, ...]:
    """1 conditionをcomplete v1 trialへprojectionする。

    owner factsが生成される前にTaskがtechnical-invalidへcloseした場合は、必須sample
    fieldを捏造せずzero-sample trialとして記録する。RUNNING partialは拒否する。
    """

    if result.classification is TaskTerminalClassification.RUNNING:
        raise ExperimentMotionLogRecordingError(
            "partial RUNNING trial cannot be recorded as a complete stream"
        )
    configuration = _configuration_record(readiness, result, context)
    trial_id = _trial_id(readiness, context)
    start = _trial_start_record(readiness, context, trial_id=trial_id)
    samples = tuple(
        _sample_record(readiness, trace, context, trial_id=trial_id)
        for trace in result.motion_steps
    )
    outcome = _outcome_record(
        readiness,
        result,
        context,
        samples,
        trial_id=trial_id,
    )
    prefix: tuple[ExperimentMotionLogRecord, ...] = (
        (configuration,) if include_configuration else ()
    )
    return (*prefix, start, *samples, outcome)


def _append_condition(
    records: list[ExperimentMotionLogRecord],
    readiness: EvaluationReadiness,
    result: ExperimentConditionExecutionResult,
    context: TrialProtocolContext,
) -> None:
    configuration = _configuration_record(readiness, result, context)
    key = (
        configuration.experiment_id,
        configuration.session_id,
        configuration.participant_id,
        configuration.configuration_id,
    )
    existing = next(
        (
            record
            for record in records
            if isinstance(record, ConfigurationRecord)
            and (
                record.experiment_id,
                record.session_id,
                record.participant_id,
                record.configuration_id,
            )
            == key
        ),
        None,
    )
    if existing is not None and existing != configuration:
        raise ExperimentMotionLogRecordingError(
            "existing configuration identity has different frozen facts"
        )
    records.extend(
        build_condition_motion_log_records(
            readiness,
            result,
            context,
            include_configuration=existing is None,
        )
    )


def build_world_tool_motion_log_records(
    readiness: EvaluationConditionPairReadiness,
    execution: WorldToolExperimentExecutionResult,
    contexts: WorldToolTrialProtocolContext,
    *,
    prior_records: Iterable[ExperimentMotionLogRecord] = (),
) -> tuple[ExperimentMotionLogRecord, ...]:
    """prior streamを保持しつつworld/tool trialをcondition orderで追加する。"""

    if execution.pair_identity != readiness.pair_identity:
        raise ExperimentMotionLogRecordingError("execution/readiness pair identity mismatch")
    records = list(prior_records)
    if records:
        validate_record_stream(records)
    conditions = sorted(
        (
            (readiness.world, execution.world, contexts.world),
            (readiness.tool, execution.tool, contexts.tool),
        ),
        key=lambda item: item[0].manifest.condition_order,
    )
    for condition_readiness, result, context in conditions:
        _append_condition(records, condition_readiness, result, context)
    validate_record_stream(records)
    return tuple(records)


def prepare_motion_log(
    records: Iterable[ExperimentMotionLogRecord],
) -> PreparedExperimentMotionLog:
    """validate -> encode -> decode -> validate -> encode一致を固定する。"""

    typed = tuple(records)
    validate_record_stream(typed)
    text = encode_jsonl(typed)
    decoded = decode_jsonl(text)
    validate_record_stream(decoded)
    if encode_jsonl(decoded) != text:
        raise ExperimentMotionLogRecordingError(
            "experiment motion log JSONL round-trip is not deterministic"
        )
    try:
        bytes_value = text.encode("utf-8", errors="strict")
        if bytes_value.decode("utf-8", errors="strict") != text:
            raise UnicodeError("UTF-8 strict read-back mismatch")
    except UnicodeError as exc:
        raise ExperimentMotionLogRecordingError(
            "experiment motion log is not strict UTF-8"
        ) from exc
    return PreparedExperimentMotionLog(decoded, text, bytes_value)


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _write_fsynced(path: Path, bytes_value: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(bytes_value)
        stream.flush()
        os.fsync(stream.fileno())


def _restore_target(target_path: Path, previous_bytes: bytes | None) -> None:
    if previous_bytes is None:
        if target_path.exists():
            target_path.unlink()
        return
    descriptor, rollback_name = tempfile.mkstemp(
        dir=target_path.parent,
        prefix=f".{target_path.name}.",
        suffix=".rollback",
    )
    os.close(descriptor)
    rollback_path = Path(rollback_name)
    try:
        _write_fsynced(rollback_path, previous_bytes)
        os.replace(rollback_path, target_path)
    finally:
        if rollback_path.exists():
            rollback_path.unlink()


def write_motion_log_atomic(
    target: str | os.PathLike[str],
    records: Iterable[ExperimentMotionLogRecord],
) -> PreparedExperimentMotionLog:
    """strict read-back後だけsame-directory atomic replaceする。"""

    prepared = prepare_motion_log(records)
    target_path = Path(target)
    parent = target_path.parent
    if not parent.is_dir():
        raise ExperimentMotionLogRecordingError(
            "motion log target directory must already exist"
        )
    previous_bytes = _read_bytes(target_path) if target_path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{target_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    descriptor_open = True
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_open = False
            stream.write(prepared.bytes_value)
            stream.flush()
            os.fsync(stream.fileno())
        if _read_bytes(temporary_path) != prepared.bytes_value:
            raise ExperimentMotionLogRecordingError(
                "temporary motion log strict read-back mismatch"
            )
        os.replace(temporary_path, target_path)
        replaced = True
        if _read_bytes(target_path) != prepared.bytes_value:
            raise ExperimentMotionLogRecordingError(
                "final motion log strict read-back mismatch"
            )
    except (OSError, UnicodeError, ExperimentMotionLogRecordingError) as exc:
        if replaced:
            try:
                _restore_target(target_path, previous_bytes)
            except OSError as rollback_exc:
                raise ExperimentMotionLogRecordingError(
                    f"atomic motion log rollback failed: {rollback_exc}"
                ) from rollback_exc
        if isinstance(exc, ExperimentMotionLogRecordingError):
            raise
        raise ExperimentMotionLogRecordingError(
            f"atomic motion log write failed: {exc}"
        ) from exc
    finally:
        if descriptor_open:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()
    return prepared


def run_r7_g_world_tool_experiment_and_record(
    *,
    manifest_software_revision_identity: str,
    execution_identity: SoftwareExecutionIdentity,
    contexts: WorldToolTrialProtocolContext,
    output_path: str | os.PathLike[str],
    prior_records: Iterable[ExperimentMotionLogRecord] = (),
) -> RecordedWorldToolExperiment:
    """canonical pairを既存runnerで1回だけ実行し、complete v1 streamを保存する。"""

    pair = build_r7_g_free_space_manifest_pair(
        software_revision_identity=manifest_software_revision_identity,
    )
    readiness = build_evaluation_condition_pair_readiness(
        pair,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=execution_identity,
    )
    execution = run_evaluation_condition_pair(readiness)
    records = build_world_tool_motion_log_records(
        readiness,
        execution,
        contexts,
        prior_records=prior_records,
    )
    prepared = write_motion_log_atomic(output_path, records)
    return RecordedWorldToolExperiment(
        execution=execution,
        records=prepared.records,
        jsonl_bytes=prepared.bytes_value,
        output_path=Path(output_path),
    )


__all__ = [
    "ExperimentMotionLogRecordingError",
    "PreparedExperimentMotionLog",
    "RecordedWorldToolExperiment",
    "TrialProtocolContext",
    "WorldToolTrialProtocolContext",
    "build_condition_motion_log_records",
    "build_world_tool_motion_log_records",
    "prepare_motion_log",
    "run_r7_g_world_tool_experiment_and_record",
    "write_motion_log_atomic",
]
