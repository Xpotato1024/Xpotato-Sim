"""experiment motion logのversioned JSONL record schema。

configuration / trial / sample / outcomeのordering、m/rad/s単位、measured evidence
整合をencode前とdecode後に検証する。runtimeを実行せず、optional fieldの欠落を
測定済みとは扱わない。EvaluationManifest、EvaluationReadiness、FreezeRecordは
``runtime/evaluation/manifest.py`` が所有する別contractである。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from math import isfinite, sqrt
from typing import Literal, TypeAlias

from xpotato_sim.schemas.endpoint import (
    ControlFrameResolutionStatus,
    EndpointControlFrame,
    EndpointProgressStatus,
    MotionStatus,
    ResolvedEndpointFrame,
)


EXPERIMENT_MOTION_LOG_SCHEMA_VERSION = "experiment-motion-log/v1"
MEASURED_EVIDENCE_TOLERANCE = 1e-12
RecordKind = Literal["configuration", "trial_start", "motion_sample", "trial_outcome"]
CompletionStatus = Literal["success", "failed", "technical_invalid"]
FailureAttribution = Literal["none", "operator", "technical"]
ExperimentControlCondition = Literal["world", "tool"]
ScalarParameter: TypeAlias = str | int | float | bool | None
Vector3 = tuple[float, float, float]

_CONTROL_FRAMES = {"world", "tool"}
_RESOLVED_FRAMES = {"mujoco_world"}
_RESOLUTION_STATUSES = {
    "world_passthrough",
    "tool_orientation_resolved",
    "tool_orientation_unavailable",
    "invalid_control_frame_defaulted",
}
_MOTION_STATUSES = {"accepted", "scaled", "held"}
_PROGRESS_STATUSES = {
    "not_requested",
    "measurement_unavailable",
    "insufficient_progress",
    "misaligned",
    "progressing",
}
_COMPLETION_STATUSES = {"success", "failed", "technical_invalid"}
_FAILURE_ATTRIBUTIONS = {"none", "operator", "technical"}


def _identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _enum(name: str, value: object, supported: set[str]) -> str:
    if not isinstance(value, str) or value not in supported:
        raise ValueError(f"{name} must be one of {sorted(supported)!r}")
    return value


def _bool(name: str, value: object) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a JSON boolean")
    return value


def _finite(name: str, value: object, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a JSON number")
    result = float(value)
    if not isfinite(result) or (non_negative and result < 0.0):
        suffix = " and non-negative" if non_negative else ""
        raise ValueError(f"{name} must be finite{suffix}")
    return result


def _index(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative JSON integer")
    return value


def _vector(name: str, value: object, *, length: int | None = None) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a numeric array")
    result = tuple(_finite(name, item) for item in value)
    if length is not None and len(result) != length:
        raise ValueError(f"{name} must contain exactly {length} values")
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _optional_vector3(name: str, value: object | None) -> Vector3 | None:
    if value is None:
        return None
    return _vector(name, value, length=3)  # type: ignore[return-value]


def _optional_reason(name: str, value: object | None) -> None:
    if value is not None:
        _identifier(name, value)


def _discriminants(schema_version: str, record_kind: str, expected_kind: RecordKind) -> None:
    if schema_version != EXPERIMENT_MOTION_LOG_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {schema_version!r}")
    if record_kind != expected_kind:
        raise ValueError(f"record_kind must be {expected_kind!r}")


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def _vectors_close(left: Sequence[float], right: Sequence[float]) -> bool:
    return len(left) == len(right) and sqrt(sum((left[index] - right[index]) ** 2 for index in range(len(left)))) <= MEASURED_EVIDENCE_TOLERANCE


@dataclass(frozen=True, slots=True)
class ConfigurationRecord:
    """trial群に先行するexperiment motion logのconfiguration snapshot。

    configuration identity、software revision、initial state、target、source、
    comparison parametersを記録する。EvaluationManifest v3、EvaluationReadiness、
    FreezeRecordそのものではなく、それらのmanifest / freeze identityを保持しない。
    """

    experiment_id: str
    session_id: str
    participant_id: str
    configuration_id: str
    software_revision: str
    initial_qpos_rad: tuple[float, ...]
    initial_measured_tip_position_m: Vector3
    initial_tool_orientation_wxyz: tuple[float, float, float, float]
    target_world_position_m: Vector3
    target_tolerance_m: float
    dwell_interval_s: float
    timeout_s: float
    source_kind: str
    target_id: str
    local_endpoint_speed_m_s: float
    deadzone: float
    local_endpoint_max_delta_m: float
    comparison_parameters: tuple[tuple[str, ScalarParameter], ...] = ()
    schema_version: str = EXPERIMENT_MOTION_LOG_SCHEMA_VERSION
    record_kind: Literal["configuration"] = "configuration"

    def __post_init__(self) -> None:
        _discriminants(self.schema_version, self.record_kind, "configuration")
        for name in ("experiment_id", "session_id", "participant_id", "configuration_id", "software_revision", "source_kind", "target_id"):
            _identifier(name, getattr(self, name))
        object.__setattr__(self, "initial_qpos_rad", _vector("initial_qpos_rad", self.initial_qpos_rad))
        object.__setattr__(self, "initial_measured_tip_position_m", _vector("initial_measured_tip_position_m", self.initial_measured_tip_position_m, length=3))
        orientation = _vector("initial_tool_orientation_wxyz", self.initial_tool_orientation_wxyz, length=4)
        norm = sqrt(sum(component * component for component in orientation))
        if abs(norm - 1.0) > MEASURED_EVIDENCE_TOLERANCE:
            raise ValueError("initial_tool_orientation_wxyz must be a unit quaternion")
        object.__setattr__(self, "initial_tool_orientation_wxyz", orientation)
        object.__setattr__(self, "target_world_position_m", _vector("target_world_position_m", self.target_world_position_m, length=3))
        for name in ("target_tolerance_m", "dwell_interval_s", "timeout_s", "local_endpoint_speed_m_s", "deadzone", "local_endpoint_max_delta_m"):
            object.__setattr__(self, name, _finite(name, getattr(self, name), non_negative=True))
        frozen: list[tuple[str, ScalarParameter]] = []
        for item in self.comparison_parameters:
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
                raise ValueError("comparison_parameters must contain key/value pairs")
            key, value = item
            _identifier("comparison_parameters key", key)
            if value is not None and type(value) not in (str, int, float, bool):
                raise ValueError("comparison_parameters values must be JSON scalars")
            if isinstance(value, float) and not isfinite(value):
                raise ValueError("comparison_parameters numeric values must be finite")
            frozen.append((key, value))
        if len({key for key, _ in frozen}) != len(frozen):
            raise ValueError("comparison_parameters keys must be unique")
        object.__setattr__(self, "comparison_parameters", tuple(sorted(frozen)))


@dataclass(frozen=True, slots=True)
class TrialStartRecord:
    """1 trial開始時のrequested/resolved conditionとinitial state記録。"""

    experiment_id: str
    session_id: str
    participant_id: str
    configuration_id: str
    trial_id: str
    block_id: str
    task_family: str
    target_id: str
    practice: bool
    control_condition: ExperimentControlCondition
    condition_order: int
    task_order: int
    target_direction: str
    direction_order: int
    repetition_index: int
    attempt_index: int
    retry_of_trial_id: str | None
    runtime_timestamp_s: float
    schema_version: str = EXPERIMENT_MOTION_LOG_SCHEMA_VERSION
    record_kind: Literal["trial_start"] = "trial_start"

    def __post_init__(self) -> None:
        _discriminants(self.schema_version, self.record_kind, "trial_start")
        for name in ("experiment_id", "session_id", "participant_id", "configuration_id", "trial_id", "block_id", "task_family", "target_id", "target_direction"):
            _identifier(name, getattr(self, name))
        _bool("practice", self.practice)
        _enum("control_condition", self.control_condition, _CONTROL_FRAMES)
        for name in ("condition_order", "task_order", "direction_order", "repetition_index", "attempt_index"):
            _index(name, getattr(self, name))
        if self.retry_of_trial_id is not None:
            _identifier("retry_of_trial_id", self.retry_of_trial_id)
            if self.retry_of_trial_id == self.trial_id:
                raise ValueError("a trial cannot retry itself")
        if self.attempt_index == 0 and self.retry_of_trial_id is not None:
            raise ValueError("attempt_index 0 cannot have retry_of_trial_id")
        if self.attempt_index > 0 and self.retry_of_trial_id is None:
            raise ValueError("retry attempts require retry_of_trial_id")
        object.__setattr__(self, "runtime_timestamp_s", _finite("runtime_timestamp_s", self.runtime_timestamp_s))


@dataclass(frozen=True, slots=True)
class MotionSampleRecord:
    """trial内の時系列command/state/evidence sample。

    sample indexとtimeは単調増加、position/delta/errorはm、joint angleはradである。
    measured fieldはMuJoCo観測がある場合だけ設定し、command intentで代用しない。
    """

    experiment_id: str
    session_id: str
    participant_id: str
    configuration_id: str
    trial_id: str
    sample_index: int
    source_kind: str
    source_timestamp_s: float
    runtime_timestamp_s: float
    source_active: bool
    axis_values: Vector3
    zero_input: bool
    stale_reason: str | None
    requested_control_frame: EndpointControlFrame
    local_endpoint_velocity_m_s: Vector3
    resolved_control_frame: ResolvedEndpointFrame | None
    control_frame_resolution_status: ControlFrameResolutionStatus
    control_frame_resolution_reason: str | None
    resolved_world_endpoint_velocity_m_s: Vector3 | None
    endpoint_delta_requested_m: Vector3 | None
    endpoint_delta_achieved_m: Vector3 | None
    qpos_before_rad: tuple[float, ...]
    qpos_after_rad: tuple[float, ...]
    candidate_qpos_rad: tuple[float, ...] | None
    measured_tip_position_before_m: Vector3 | None
    measured_tip_position_after_m: Vector3 | None
    actual_tip_delta_m: Vector3 | None
    motion_status: MotionStatus
    motion_rejection_reason: str | None
    target_rejected: bool
    target_rejection_reason: str | None
    endpoint_progress_status: EndpointProgressStatus
    endpoint_progress_signed_m: float | None = None
    endpoint_progress_ratio: float | None = None
    endpoint_progress_direction_cosine: float | None = None
    endpoint_progress_requested_norm_m: float | None = None
    endpoint_progress_measured_norm_m: float | None = None
    endpoint_progress_measurement_available: bool = False
    measurement_unavailable_reason: str | None = None
    schema_version: str = EXPERIMENT_MOTION_LOG_SCHEMA_VERSION
    record_kind: Literal["motion_sample"] = "motion_sample"

    def __post_init__(self) -> None:
        _discriminants(self.schema_version, self.record_kind, "motion_sample")
        for name in ("experiment_id", "session_id", "participant_id", "configuration_id", "trial_id", "source_kind"):
            _identifier(name, getattr(self, name))
        _index("sample_index", self.sample_index)
        for name in ("source_timestamp_s", "runtime_timestamp_s"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        _bool("source_active", self.source_active)
        _bool("zero_input", self.zero_input)
        _bool("target_rejected", self.target_rejected)
        _bool("endpoint_progress_measurement_available", self.endpoint_progress_measurement_available)
        _enum("requested_control_frame", self.requested_control_frame, _CONTROL_FRAMES)
        if self.resolved_control_frame is not None:
            _enum("resolved_control_frame", self.resolved_control_frame, _RESOLVED_FRAMES)
        _enum("control_frame_resolution_status", self.control_frame_resolution_status, _RESOLUTION_STATUSES)
        _enum("motion_status", self.motion_status, _MOTION_STATUSES)
        _enum("endpoint_progress_status", self.endpoint_progress_status, _PROGRESS_STATUSES)
        object.__setattr__(self, "axis_values", _vector("axis_values", self.axis_values, length=3))
        object.__setattr__(self, "local_endpoint_velocity_m_s", _vector("local_endpoint_velocity_m_s", self.local_endpoint_velocity_m_s, length=3))
        if self.zero_input != all(component == 0.0 for component in self.axis_values):
            raise ValueError("zero_input must agree with axis_values")
        if self.source_active and self.stale_reason is not None:
            raise ValueError("an active source cannot have stale_reason")
        for name in ("stale_reason", "control_frame_resolution_reason", "motion_rejection_reason", "target_rejection_reason", "measurement_unavailable_reason"):
            _optional_reason(name, getattr(self, name))
        if self.target_rejected != (self.target_rejection_reason is not None):
            raise ValueError("target_rejected and target_rejection_reason must agree")
        for name in ("resolved_world_endpoint_velocity_m_s", "endpoint_delta_requested_m", "endpoint_delta_achieved_m", "measured_tip_position_before_m", "measured_tip_position_after_m", "actual_tip_delta_m"):
            object.__setattr__(self, name, _optional_vector3(name, getattr(self, name)))
        q_before = _vector("qpos_before_rad", self.qpos_before_rad)
        q_after = _vector("qpos_after_rad", self.qpos_after_rad)
        if len(q_before) != len(q_after):
            raise ValueError("qpos_before_rad and qpos_after_rad must have equal length")
        object.__setattr__(self, "qpos_before_rad", q_before)
        object.__setattr__(self, "qpos_after_rad", q_after)
        if self.candidate_qpos_rad is not None:
            candidate = _vector("candidate_qpos_rad", self.candidate_qpos_rad)
            if len(candidate) != len(q_before):
                raise ValueError("candidate_qpos_rad must match qpos structure")
            object.__setattr__(self, "candidate_qpos_rad", candidate)
        progress_metrics = ("endpoint_progress_signed_m", "endpoint_progress_ratio", "endpoint_progress_direction_cosine", "endpoint_progress_requested_norm_m", "endpoint_progress_measured_norm_m")
        for name in progress_metrics:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(name, value))
        if self.control_frame_resolution_status == "tool_orientation_unavailable":
            if self.requested_control_frame != "tool" or self.resolved_control_frame is not None or self.resolved_world_endpoint_velocity_m_s is not None or self.endpoint_delta_requested_m is not None:
                raise ValueError("unavailable tool resolution cannot contain resolved world motion")
            if self.control_frame_resolution_reason is None:
                raise ValueError("unavailable tool resolution requires control_frame_resolution_reason")
            if self.motion_status != "held" or self.motion_rejection_reason is None:
                raise ValueError("unavailable tool resolution requires held motion and rejection reason")
            if self.candidate_qpos_rad is None or not _vectors_close(self.candidate_qpos_rad, q_before) or not _vectors_close(q_after, q_before):
                raise ValueError("unavailable tool resolution must hold candidate and qpos")
            if self.endpoint_delta_achieved_m is None or not _vectors_close(self.endpoint_delta_achieved_m, (0.0, 0.0, 0.0)):
                raise ValueError("unavailable tool resolution requires zero achieved policy delta")
            if self.actual_tip_delta_m is not None and not _vectors_close(self.actual_tip_delta_m, (0.0, 0.0, 0.0)):
                raise ValueError("unavailable tool resolution requires zero measured tip delta")
        else:
            expected_request = "tool" if self.control_frame_resolution_status == "tool_orientation_resolved" else "world"
            if self.requested_control_frame != expected_request:
                raise ValueError("resolution status and requested frame are inconsistent")
            if self.resolved_control_frame != "mujoco_world" or self.resolved_world_endpoint_velocity_m_s is None:
                raise ValueError("successful frame resolution requires resolved world frame and velocity")
            if self.control_frame_resolution_status in {"world_passthrough", "invalid_control_frame_defaulted"} and not _vectors_close(self.resolved_world_endpoint_velocity_m_s, self.local_endpoint_velocity_m_s):
                raise ValueError("world-resolved passthrough velocity must equal local requested velocity")
        if self.resolved_control_frame is None and self.resolved_world_endpoint_velocity_m_s is not None:
            raise ValueError("resolved world velocity requires resolved_control_frame")
        measured = (self.measured_tip_position_before_m, self.measured_tip_position_after_m, self.actual_tip_delta_m)
        if any(value is None for value in measured):
            if any(value is not None for value in measured):
                raise ValueError("measured position and delta fields are all-or-none")
            if self.endpoint_progress_measurement_available:
                raise ValueError("measurement availability cannot be true without measured evidence")
            if self.measurement_unavailable_reason is None:
                raise ValueError("missing measured evidence requires measurement_unavailable_reason")
            if self.endpoint_progress_status != "measurement_unavailable" or any(getattr(self, name) is not None for name in progress_metrics):
                raise ValueError("unavailable measurement cannot contain measurement-dependent progress values")
        else:
            if not self.endpoint_progress_measurement_available:
                raise ValueError("complete measured evidence requires measurement availability")
            if self.measurement_unavailable_reason is not None:
                raise ValueError("available measurement cannot have measurement_unavailable_reason")
            if self.endpoint_progress_status == "measurement_unavailable":
                raise ValueError("available measurement cannot have measurement_unavailable progress status")
            expected_delta = tuple(self.measured_tip_position_after_m[index] - self.measured_tip_position_before_m[index] for index in range(3))  # type: ignore[index]
            if _distance(expected_delta, self.actual_tip_delta_m) > MEASURED_EVIDENCE_TOLERANCE:  # type: ignore[arg-type]
                raise ValueError("actual_tip_delta_m must equal measured after minus before")
            if self.endpoint_progress_status == "not_requested":
                if self.endpoint_progress_signed_m is not None or self.endpoint_progress_ratio is not None or self.endpoint_progress_direction_cosine is not None:
                    raise ValueError("not_requested progress cannot contain derived progress metrics")
            elif self.endpoint_progress_status == "insufficient_progress":
                required = (self.endpoint_progress_signed_m, self.endpoint_progress_ratio, self.endpoint_progress_requested_norm_m, self.endpoint_progress_measured_norm_m)
                if any(value is None for value in required):
                    raise ValueError("insufficient_progress requires P10 norm and progress metrics")
            else:
                required = (self.endpoint_progress_signed_m, self.endpoint_progress_ratio, self.endpoint_progress_direction_cosine, self.endpoint_progress_requested_norm_m, self.endpoint_progress_measured_norm_m)
                if any(value is None for value in required):
                    raise ValueError("measured progress status requires complete P10 metrics")


@dataclass(frozen=True, slots=True)
class TrialOutcomeRecord:
    """終端classificationとprimary measured outcomeを固定するrecord。"""

    experiment_id: str
    session_id: str
    participant_id: str
    configuration_id: str
    trial_id: str
    runtime_timestamp_s: float
    completion_status: CompletionStatus
    success_within_timeout: bool
    final_measured_endpoint_error_m: float | None
    failure_attribution: FailureAttribution
    outcome_reason: str | None
    subjective_response_link_id: str | None
    primary_outcome_sample_index: int | None
    schema_version: str = EXPERIMENT_MOTION_LOG_SCHEMA_VERSION
    record_kind: Literal["trial_outcome"] = "trial_outcome"

    def __post_init__(self) -> None:
        _discriminants(self.schema_version, self.record_kind, "trial_outcome")
        for name in ("experiment_id", "session_id", "participant_id", "configuration_id", "trial_id"):
            _identifier(name, getattr(self, name))
        object.__setattr__(self, "runtime_timestamp_s", _finite("runtime_timestamp_s", self.runtime_timestamp_s))
        _enum("completion_status", self.completion_status, _COMPLETION_STATUSES)
        _enum("failure_attribution", self.failure_attribution, _FAILURE_ATTRIBUTIONS)
        _bool("success_within_timeout", self.success_within_timeout)
        if self.final_measured_endpoint_error_m is not None:
            object.__setattr__(self, "final_measured_endpoint_error_m", _finite("final_measured_endpoint_error_m", self.final_measured_endpoint_error_m, non_negative=True))
        if self.subjective_response_link_id is not None:
            _identifier("subjective_response_link_id", self.subjective_response_link_id)
        if self.primary_outcome_sample_index is not None:
            _index("primary_outcome_sample_index", self.primary_outcome_sample_index)
        _optional_reason("outcome_reason", self.outcome_reason)
        if self.success_within_timeout:
            if self.completion_status != "success" or self.failure_attribution != "none" or self.final_measured_endpoint_error_m is None or self.primary_outcome_sample_index is None:
                raise ValueError("successful outcome requires measured primary evidence and no failure attribution")
        elif self.completion_status == "success":
            raise ValueError("success completion requires success_within_timeout")
        if self.completion_status == "technical_invalid" and self.failure_attribution != "technical":
            raise ValueError("technical_invalid requires technical failure attribution")
        valid_classification = (
            (self.completion_status == "success" and self.failure_attribution == "none" and self.outcome_reason is None)
            or (self.completion_status == "failed" and self.failure_attribution == "operator" and self.outcome_reason is not None)
            or (self.completion_status == "technical_invalid" and self.failure_attribution == "technical" and self.outcome_reason is not None)
        )
        if not valid_classification:
            raise ValueError("completion status, failure attribution, and outcome reason are inconsistent")


ExperimentMotionLogRecord: TypeAlias = ConfigurationRecord | TrialStartRecord | MotionSampleRecord | TrialOutcomeRecord
_RECORD_TYPES = {"configuration": ConfigurationRecord, "trial_start": TrialStartRecord, "motion_sample": MotionSampleRecord, "trial_outcome": TrialOutcomeRecord}


def record_to_json_value(record: ExperimentMotionLogRecord) -> dict[str, object]:
    """typed recordをversion/discriminantを保ったJSON-compatible objectへ変換する。"""

    def json_value(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): json_value(value) for key, value in item.items()}
        if isinstance(item, (tuple, list)):
            return [json_value(value) for value in item]
        return item

    value = json_value(asdict(record))
    assert isinstance(value, dict)
    if isinstance(record, ConfigurationRecord):
        value["comparison_parameters"] = dict(record.comparison_parameters)
    else:
        value.pop("comparison_parameters", None)
    return value


def parse_record(value: Mapping[str, object]) -> ExperimentMotionLogRecord:
    """record discriminantでstrict型を選び、unknown/inconsistent fieldを拒否する。"""

    if value.get("schema_version") != EXPERIMENT_MOTION_LOG_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {value.get('schema_version')!r}")
    kind = value.get("record_kind")
    record_type = _RECORD_TYPES.get(kind) if isinstance(kind, str) else None
    if record_type is None:
        raise ValueError(f"unsupported record_kind: {kind!r}")
    allowed = {field.name for field in fields(record_type)}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown fields for {kind}: {sorted(unknown)!r}")
    kwargs = dict(value)
    if record_type is ConfigurationRecord:
        parameters = kwargs.get("comparison_parameters", {})
        if not isinstance(parameters, Mapping):
            raise ValueError("comparison_parameters must be an object")
        kwargs["comparison_parameters"] = tuple(parameters.items())
    return record_type(**kwargs)  # type: ignore[arg-type,return-value]


def encode_jsonl(records: Iterable[ExperimentMotionLogRecord]) -> str:
    """validation済みrecord orderingをUTF-8向けJSONL textへencodeする。"""

    return "".join(json.dumps(record_to_json_value(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n" for record in records)


def decode_jsonl(text: str) -> tuple[ExperimentMotionLogRecord, ...]:
    """JSONLをtyped record列へdecodeし、stream全体のorderingも検証する。"""

    result = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise ValueError(f"blank JSONL line at {line_number}")
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"JSONL line {line_number} must be an object")
        result.append(parse_record(value))
    return tuple(result)


def _trial_context(record: TrialStartRecord | MotionSampleRecord | TrialOutcomeRecord) -> tuple[str, ...]:
    return (record.experiment_id, record.session_id, record.participant_id, record.configuration_id, record.trial_id)


def _retry_protocol(record: TrialStartRecord) -> tuple[object, ...]:
    return (record.experiment_id, record.session_id, record.participant_id, record.configuration_id, record.block_id, record.task_family, record.target_id, record.practice, record.control_condition, record.condition_order, record.task_order, record.target_direction, record.direction_order, record.repetition_index)


def _validate_success(configuration: ConfigurationRecord, start: TrialStartRecord, samples: Mapping[int, MotionSampleRecord], outcome: TrialOutcomeRecord) -> None:
    if not outcome.success_within_timeout:
        return
    if not samples or outcome.primary_outcome_sample_index != len(samples) - 1:
        raise ValueError("successful outcome primary evidence must be the final motion sample")
    for sample in samples.values():
        if sample.motion_status == "held" or sample.target_rejected or sample.stale_reason is not None or not sample.endpoint_progress_measurement_available or sample.resolved_control_frame is None:
            raise ValueError("successful outcome cannot contain held, rejected, stale, unavailable, or unresolved samples")
    primary = samples.get(outcome.primary_outcome_sample_index)  # type: ignore[arg-type]
    if primary is None or primary.measured_tip_position_after_m is None or not primary.endpoint_progress_measurement_available:
        raise ValueError("successful outcome must reference complete measured evidence")
    if primary.runtime_timestamp_s - start.runtime_timestamp_s > configuration.timeout_s + MEASURED_EVIDENCE_TOLERANCE:
        raise ValueError("successful outcome primary evidence exceeds timeout")
    measured_error = _distance(primary.measured_tip_position_after_m, configuration.target_world_position_m)
    if abs(measured_error - outcome.final_measured_endpoint_error_m) > MEASURED_EVIDENCE_TOLERANCE:  # type: ignore[operator]
        raise ValueError("final measured endpoint error disagrees with primary sample")
    if measured_error > configuration.target_tolerance_m + MEASURED_EVIDENCE_TOLERANCE:
        raise ValueError("successful outcome does not satisfy target tolerance")
    dwell_start: float | None = None
    for index in sorted(samples):
        if index > primary.sample_index:
            break
        sample = samples[index]
        position = sample.measured_tip_position_after_m
        inside = position is not None and sample.endpoint_progress_measurement_available and _distance(position, configuration.target_world_position_m) <= configuration.target_tolerance_m + MEASURED_EVIDENCE_TOLERANCE
        if inside:
            if dwell_start is None:
                dwell_start = sample.runtime_timestamp_s
        else:
            dwell_start = None
    if dwell_start is None or primary.runtime_timestamp_s - dwell_start + MEASURED_EVIDENCE_TOLERANCE < configuration.dwell_interval_s:
        raise ValueError("successful outcome lacks continuous measured dwell evidence")


def _validate_outcome_evidence(configuration: ConfigurationRecord, samples: Mapping[int, MotionSampleRecord], outcome: TrialOutcomeRecord) -> None:
    has_index = outcome.primary_outcome_sample_index is not None
    has_error = outcome.final_measured_endpoint_error_m is not None
    if has_index != has_error:
        raise ValueError("final measured error and sample index must be both present or both null")
    if not has_index:
        return
    if not samples or outcome.primary_outcome_sample_index != len(samples) - 1:
        raise ValueError("outcome measured evidence must reference the final motion sample")
    sample = samples.get(outcome.primary_outcome_sample_index)  # type: ignore[arg-type]
    if sample is None or sample.measured_tip_position_after_m is None or not sample.endpoint_progress_measurement_available:
        raise ValueError("outcome measured evidence must reference a complete measured sample")
    measured_error = _distance(sample.measured_tip_position_after_m, configuration.target_world_position_m)
    if abs(measured_error - outcome.final_measured_endpoint_error_m) > MEASURED_EVIDENCE_TOLERANCE:  # type: ignore[operator]
        raise ValueError("outcome final measured error disagrees with referenced sample")


def validate_record_stream(records: Iterable[ExperimentMotionLogRecord]) -> None:
    """configuration/trial/sample/outcomeのorderingとcross-record identityを検証する。"""

    configurations: dict[tuple[str, str, str, str], ConfigurationRecord] = {}
    starts: dict[str, TrialStartRecord] = {}
    samples: dict[str, dict[int, MotionSampleRecord]] = {}
    outcomes: dict[str, TrialOutcomeRecord] = {}
    last_timestamp: dict[str, float] = {}
    attempts_by_protocol: dict[tuple[object, ...], set[int]] = {}
    retry_children: set[str] = set()
    for position, record in enumerate(records):
        configuration_key = (record.experiment_id, record.session_id, record.participant_id, record.configuration_id)
        if isinstance(record, ConfigurationRecord):
            if configuration_key in configurations:
                raise ValueError(f"duplicate configuration identity at record {position}")
            configurations[configuration_key] = record
            continue
        configuration = configurations.get(configuration_key)
        if configuration is None:
            raise ValueError(f"unresolved configuration context at record {position}")
        if isinstance(record, TrialStartRecord):
            if record.trial_id in starts:
                raise ValueError(f"duplicate trial_id at record {position}")
            if record.target_id != configuration.target_id:
                raise ValueError("trial target_id must match configuration manifest")
            protocol = _retry_protocol(record)
            attempts = attempts_by_protocol.setdefault(protocol, set())
            if record.attempt_index in attempts:
                raise ValueError("attempt_index must be unique within one protocol repetition")
            if record.retry_of_trial_id is None and attempts:
                raise ValueError("one protocol repetition can have only one initial attempt")
            if record.retry_of_trial_id is not None:
                original = starts.get(record.retry_of_trial_id)
                original_outcome = outcomes.get(record.retry_of_trial_id)
                if original is None or original_outcome is None:
                    raise ValueError("retry must reference an earlier completed trial")
                if original_outcome.completion_status != "technical_invalid":
                    raise ValueError("retry must reference a technical-invalid trial")
                if _retry_protocol(record) != _retry_protocol(original):
                    raise ValueError("retry must preserve the original protocol identity")
                if record.attempt_index != original.attempt_index + 1:
                    raise ValueError("retry attempt_index must increment by one")
                if record.retry_of_trial_id in retry_children:
                    raise ValueError("a trial can have at most one direct retry child")
                retry_children.add(record.retry_of_trial_id)
            attempts.add(record.attempt_index)
            starts[record.trial_id] = record
            samples[record.trial_id] = {}
            last_timestamp[record.trial_id] = record.runtime_timestamp_s
            continue
        start = starts.get(record.trial_id)
        if start is None or record.trial_id in outcomes:
            raise ValueError("sample/outcome must occur within an open trial")
        if _trial_context(record) != _trial_context(start):
            raise ValueError("sample/outcome context must match trial_start")
        if record.runtime_timestamp_s < last_timestamp[record.trial_id]:
            raise ValueError("runtime timestamps must be non-decreasing within a trial")
        if isinstance(record, MotionSampleRecord):
            expected = len(samples[record.trial_id])
            if record.sample_index != expected:
                raise ValueError("sample_index must be contiguous from zero")
            if record.requested_control_frame != start.control_condition:
                raise ValueError("sample requested frame must match trial control condition")
            if record.source_kind != configuration.source_kind:
                raise ValueError("sample source_kind must match configuration manifest")
            axis_norm = sqrt(sum(component * component for component in record.axis_values))
            if axis_norm > 1.0 + MEASURED_EVIDENCE_TOLERANCE:
                raise ValueError("axis_values norm must not exceed one")
            expected_velocity = tuple(configuration.local_endpoint_speed_m_s * component for component in record.axis_values)
            if not _vectors_close(record.local_endpoint_velocity_m_s, expected_velocity):
                raise ValueError("local endpoint velocity must equal configured speed times axis_values")
            previous = samples[record.trial_id].get(record.sample_index - 1)
            if previous is None:
                if not _vectors_close(record.qpos_before_rad, configuration.initial_qpos_rad):
                    raise ValueError("first sample qpos must match configuration initial qpos")
                if record.measured_tip_position_before_m is not None and not _vectors_close(record.measured_tip_position_before_m, configuration.initial_measured_tip_position_m):
                    raise ValueError("first measured tip must match configuration initial tip")
            else:
                if not _vectors_close(previous.qpos_after_rad, record.qpos_before_rad):
                    raise ValueError("adjacent sample qpos trajectory is discontinuous")
                if previous.measured_tip_position_after_m is not None and record.measured_tip_position_before_m is not None and not _vectors_close(previous.measured_tip_position_after_m, record.measured_tip_position_before_m):
                    raise ValueError("adjacent measured tip trajectory is discontinuous")
            samples[record.trial_id][record.sample_index] = record
            last_timestamp[record.trial_id] = record.runtime_timestamp_s
        else:
            _validate_outcome_evidence(configuration, samples[record.trial_id], record)
            _validate_success(configuration, start, samples[record.trial_id], record)
            outcomes[record.trial_id] = record
    unclosed = set(starts) - set(outcomes)
    if unclosed:
        raise ValueError(f"unclosed trials: {sorted(unclosed)!r}")


__all__ = [
    "EXPERIMENT_MOTION_LOG_SCHEMA_VERSION", "MEASURED_EVIDENCE_TOLERANCE",
    "ConfigurationRecord", "TrialStartRecord", "MotionSampleRecord", "TrialOutcomeRecord",
    "ExperimentMotionLogRecord", "record_to_json_value", "parse_record", "encode_jsonl",
    "decode_jsonl", "validate_record_stream",
]
