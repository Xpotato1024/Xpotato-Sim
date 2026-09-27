"""experiment-motion-log/v1から評価artifactを再構成する正本。

このmoduleは既存のv1 schemaや#407 recorderを変更せず、strictに検証済みの
record streamからTask-owned endpoint evidenceを再構成し、readinessで解決済みの
Evaluation Pluginへmetric導出を委譲する。metricの数式、plugin ID dispatch、
runnerの再実行は所有しない。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import weakref
from contextlib import contextmanager
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from types import MappingProxyType

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from xpotato_sim.runtime.evaluation.manifest import (
    EvaluationConditionPairReadiness,
    EvaluationReadiness,
    comparison_parameters_for_readiness,
    verify_freeze_identity,
)
from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidence,
    CanonicalEvidenceSet,
    EvidenceStatus,
    PluginAxis,
    PluginSelection,
    TaskTerminalClassification,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    ENDPOINT_REACH_TERMINAL_EVIDENCE,
    ENDPOINT_REACH_TERMINAL_PROVENANCE,
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
    ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
)
from xpotato_sim.runtime.experiment.measured_state import (
    runtime_measurement_matches_frozen,
)
from xpotato_sim.schemas.experiment_log import (
    EXPERIMENT_MOTION_LOG_SCHEMA_VERSION,
    ConfigurationRecord,
    ExperimentMotionLogRecord,
    MotionSampleRecord,
    TrialOutcomeRecord,
    TrialStartRecord,
    decode_jsonl,
    encode_jsonl,
    validate_record_stream,
)


EVALUATION_ARTIFACT_SCHEMA_VERSION = "evaluation-artifact/v1"
EVALUATION_ARTIFACT_KIND = "evaluation_artifact"
_DIGEST_PREFIX = "sha256:"
_INITIAL_POSITION_NUMERICAL_TOLERANCE_M = 1e-6
_VECTOR_TOLERANCE = 1e-12
_METRIC_STATUSES = frozenset(
    {EvidenceStatus.MEASURED, EvidenceStatus.UNAVAILABLE, EvidenceStatus.INVALID}
)
_TERMINAL_CLASSIFICATIONS = frozenset(
    {
        TaskTerminalClassification.SUCCESS,
        TaskTerminalClassification.FAILURE,
        TaskTerminalClassification.TECHNICAL_INVALID,
    }
)
_COMPLETION_TO_TERMINAL = {
    "success": TaskTerminalClassification.SUCCESS,
    "failed": TaskTerminalClassification.FAILURE,
    "technical_invalid": TaskTerminalClassification.TECHNICAL_INVALID,
}
_COMPLETION_TO_FAILURE_ATTRIBUTION = {
    "success": "none",
    "failed": "operator",
    "technical_invalid": "technical",
}
_FAILURE_ATTRIBUTIONS = frozenset({"none", "operator", "technical"})


def _production_evaluator(identity: VersionedIdentity) -> object:
    """production catalogからidentityを解決し、未知または別実装を拒否する。"""

    try:
        return PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES.evaluators.resolve(
            PluginSelection(identity.name, identity.version)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"unknown production evaluator identity: {identity.canonical_id!r}"
        ) from exc
_COMPLETION_STATUSES = frozenset(_COMPLETION_TO_TERMINAL)


class EvaluationArtifactError(RuntimeError):
    """strictな評価artifactを安全に生成または保存できない場合のerror。"""


def _identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _non_negative_count(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _digest(value: bytes) -> str:
    return f"{_DIGEST_PREFIX}{sha256(value).hexdigest()}"


def _digest_string(name: str, value: object) -> str:
    result = _identifier(name, value)
    if not result.startswith(_DIGEST_PREFIX) or len(result) != len(_DIGEST_PREFIX) + 64:
        raise ValueError(f"{name} must be a canonical sha256 digest")
    try:
        int(result.removeprefix(_DIGEST_PREFIX), 16)
    except ValueError as exc:
        raise ValueError(f"{name} must contain hexadecimal digest bytes") from exc
    return result


def _vector_close(left: Sequence[float], right: Sequence[float], tolerance: float = _VECTOR_TOLERANCE) -> bool:
    return len(left) == len(right) and all(
        abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right, strict=True)
    )


def _identity_document(identity: VersionedIdentity) -> dict[str, object]:
    return {"name": identity.name, "version": identity.version}


def _require_object(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def _require_fields(value: object, name: str, expected: frozenset[str]) -> Mapping[str, object]:
    document = _require_object(value, name)
    actual = frozenset(document)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ValueError(f"{name} has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"{name} is missing fields: {missing}")
    return document


def _parse_json_document(document: bytes | str | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(document, Mapping):
        return _require_object(document, "artifact")
    if isinstance(document, bytes):
        try:
            text = document.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("evaluation artifact must be valid UTF-8") from exc
    elif isinstance(document, str):
        text = document
    else:
        raise TypeError("artifact document must be UTF-8 bytes, text, or an object")

    def reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate field in evaluation artifact: {key!r}")
            result[key] = value
        return result

    def reject_non_finite_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON constant is forbidden: {value}")

    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate_fields,
            parse_constant=reject_non_finite_constant,
        )
    except ValueError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("evaluation artifact is not valid JSON") from exc
    return _require_object(value, "artifact")


def _decode_identity(value: object, name: str) -> VersionedIdentity:
    document = _require_fields(value, name, frozenset({"name", "version"}))
    identity_name = _identifier(f"{name}.name", document["name"])
    version = document["version"]
    if type(version) is not int or version < 1:
        raise ValueError(f"{name}.version must be a positive integer")
    return VersionedIdentity(identity_name, version)


@dataclass(frozen=True, slots=True)
class EvaluationArtifactMetric:
    """1つのresolved evaluatorが返したtyped metric result。"""

    evaluator: VersionedIdentity
    status: EvidenceStatus
    value: object | None
    unit: str
    frame: str | None
    provenance: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evaluator, VersionedIdentity):
            raise TypeError("artifact evaluator must use VersionedIdentity")
        production = _production_evaluator(self.evaluator)
        if self.unit != production.unit:
            raise ValueError(
                f"metric unit does not match production evaluator {self.evaluator.canonical_id!r}"
            )
        if self.frame != production.frame:
            raise ValueError(
                f"metric frame does not match production evaluator {self.evaluator.canonical_id!r}"
            )
        if self.provenance != production.provenance:
            raise ValueError(
                f"metric provenance does not match production evaluator {self.evaluator.canonical_id!r}"
            )
        if self.status not in _METRIC_STATUSES:
            raise ValueError("artifact metric status must be measured, unavailable, or invalid")
        _identifier("metric unit", self.unit)
        if self.frame is not None:
            _identifier("metric frame", self.frame)
        _identifier("metric provenance", self.provenance)
        if self.reason is not None:
            _identifier("metric reason", self.reason)
        if self.status is EvidenceStatus.MEASURED:
            if self.value is None:
                raise ValueError("measured artifact metric requires a value")
            if self.reason is not None:
                raise ValueError("measured artifact metric must not carry a reason")
            if isinstance(self.value, bool):
                return
            if not isinstance(self.value, (int, float)) or not isfinite(float(self.value)):
                raise ValueError("measured artifact metric value must be finite JSON scalar")
        elif self.value is not None:
            raise ValueError("unavailable or invalid artifact metric must not carry a value")
        elif self.reason is None:
            raise ValueError("unavailable or invalid artifact metric requires a reason")

    def to_document(self) -> dict[str, object]:
        return {
            "evaluator": _identity_document(self.evaluator),
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "frame": self.frame,
            "provenance": self.provenance,
            "reason": self.reason,
        }


_METRIC_FIELDS = frozenset(
    {"evaluator", "status", "value", "unit", "frame", "provenance", "reason"}
)


def _metric_from_document(value: object, name: str) -> EvaluationArtifactMetric:
    document = _require_fields(value, name, _METRIC_FIELDS)
    status_value = document["status"]
    try:
        status = EvidenceStatus(status_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}.status is not a supported metric status") from exc
    return EvaluationArtifactMetric(
        evaluator=_decode_identity(document["evaluator"], f"{name}.evaluator"),
        status=status,
        value=document["value"],
        unit=_identifier(f"{name}.unit", document["unit"]),
        frame=(None if document["frame"] is None else _identifier(f"{name}.frame", document["frame"])),
        provenance=_identifier(f"{name}.provenance", document["provenance"]),
        reason=(None if document["reason"] is None else _identifier(f"{name}.reason", document["reason"])),
    )


@dataclass(frozen=True, slots=True)
class EvaluationArtifactTrial:
    """v1 trialのterminal classificationとordered metric results。"""

    trial_id: str
    condition_id: str
    requested_control_frame: str
    target_id: str
    condition_order: int
    task_order: int
    target_direction: str
    practice: bool
    repetition_index: int
    attempt_index: int
    retry_of_trial_id: str | None
    runtime_start_s: float
    runtime_end_s: float
    terminal_classification: TaskTerminalClassification
    completion_status: str
    failure_attribution: str
    outcome_reason: str | None
    metrics: tuple[EvaluationArtifactMetric, ...]

    def __post_init__(self) -> None:
        for name in (
            "trial_id",
            "condition_id",
            "requested_control_frame",
            "target_id",
            "target_direction",
        ):
            _identifier(name, getattr(self, name))
        if self.requested_control_frame not in {"world", "tool"}:
            raise ValueError("trial requested_control_frame must be world or tool")
        if type(self.practice) is not bool:
            raise ValueError("trial practice must be a boolean")
        for name in ("condition_order", "task_order", "repetition_index", "attempt_index"):
            _non_negative_count(name, getattr(self, name))
        if self.retry_of_trial_id is not None:
            _identifier("retry_of_trial_id", self.retry_of_trial_id)
            if self.retry_of_trial_id == self.trial_id:
                raise ValueError("trial cannot retry itself")
        start = _finite("runtime_start_s", self.runtime_start_s)
        end = _finite("runtime_end_s", self.runtime_end_s)
        if end < start:
            raise ValueError("trial runtime_end_s must not precede runtime_start_s")
        object.__setattr__(self, "runtime_start_s", start)
        object.__setattr__(self, "runtime_end_s", end)
        if not isinstance(self.terminal_classification, TaskTerminalClassification):
            raise TypeError("trial terminal classification must be typed")
        if self.terminal_classification not in _TERMINAL_CLASSIFICATIONS:
            raise ValueError("artifact trial cannot remain running")
        if self.completion_status not in _COMPLETION_STATUSES:
            raise ValueError("trial completion status is unsupported")
        if _COMPLETION_TO_TERMINAL[self.completion_status] is not self.terminal_classification:
            raise ValueError("trial completion and terminal classifications disagree")
        if self.failure_attribution not in _FAILURE_ATTRIBUTIONS:
            raise ValueError("trial failure attribution is unsupported")
        if self.failure_attribution != _COMPLETION_TO_FAILURE_ATTRIBUTION[self.completion_status]:
            raise ValueError("trial completion status and failure attribution disagree")
        if self.outcome_reason is not None:
            _identifier("trial outcome_reason", self.outcome_reason)
        if self.terminal_classification is TaskTerminalClassification.SUCCESS:
            if self.failure_attribution != "none" or self.outcome_reason is not None:
                raise ValueError("successful trial cannot have failure attribution or reason")
        elif self.failure_attribution == "none" or self.outcome_reason is None:
            raise ValueError("failed or technical-invalid trial requires attribution and reason")
        metrics = tuple(self.metrics)
        if not metrics:
            raise ValueError("trial must contain ordered evaluator metrics")
        if any(not isinstance(item, EvaluationArtifactMetric) for item in metrics):
            raise TypeError("trial metrics must use EvaluationArtifactMetric")
        if len({item.evaluator for item in metrics}) != len(metrics):
            raise ValueError("trial evaluator metrics must be unique")
        object.__setattr__(self, "metrics", metrics)

    def to_document(self) -> dict[str, object]:
        return {
            "trial_id": self.trial_id,
            "condition_id": self.condition_id,
            "requested_control_frame": self.requested_control_frame,
            "target_id": self.target_id,
            "condition_order": self.condition_order,
            "task_order": self.task_order,
            "target_direction": self.target_direction,
            "practice": self.practice,
            "repetition_index": self.repetition_index,
            "attempt_index": self.attempt_index,
            "retry_of_trial_id": self.retry_of_trial_id,
            "runtime_start_s": self.runtime_start_s,
            "runtime_end_s": self.runtime_end_s,
            "terminal_classification": self.terminal_classification.value,
            "completion_status": self.completion_status,
            "failure_attribution": self.failure_attribution,
            "outcome_reason": self.outcome_reason,
            "metrics": [item.to_document() for item in self.metrics],
        }


_TRIAL_FIELDS = frozenset(
    {
        "trial_id",
        "condition_id",
        "requested_control_frame",
        "target_id",
        "condition_order",
        "task_order",
        "target_direction",
        "practice",
        "repetition_index",
        "attempt_index",
        "retry_of_trial_id",
        "runtime_start_s",
        "runtime_end_s",
        "terminal_classification",
        "completion_status",
        "failure_attribution",
        "outcome_reason",
        "metrics",
    }
)


def _trial_from_document(value: object, name: str) -> EvaluationArtifactTrial:
    document = _require_fields(value, name, _TRIAL_FIELDS)
    try:
        classification = TaskTerminalClassification(document["terminal_classification"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}.terminal_classification is unsupported") from exc
    metrics_value = document["metrics"]
    if not isinstance(metrics_value, list):
        raise ValueError(f"{name}.metrics must be an array")
    return EvaluationArtifactTrial(
        trial_id=_identifier(f"{name}.trial_id", document["trial_id"]),
        condition_id=_identifier(f"{name}.condition_id", document["condition_id"]),
        requested_control_frame=_identifier(
            f"{name}.requested_control_frame", document["requested_control_frame"]
        ),
        target_id=_identifier(f"{name}.target_id", document["target_id"]),
        condition_order=_non_negative_count(f"{name}.condition_order", document["condition_order"]),
        task_order=_non_negative_count(f"{name}.task_order", document["task_order"]),
        target_direction=_identifier(f"{name}.target_direction", document["target_direction"]),
        practice=document["practice"],  # type: ignore[arg-type]
        repetition_index=_non_negative_count(f"{name}.repetition_index", document["repetition_index"]),
        attempt_index=_non_negative_count(f"{name}.attempt_index", document["attempt_index"]),
        retry_of_trial_id=(
            None
            if document["retry_of_trial_id"] is None
            else _identifier(f"{name}.retry_of_trial_id", document["retry_of_trial_id"])
        ),
        runtime_start_s=_finite(f"{name}.runtime_start_s", document["runtime_start_s"]),
        runtime_end_s=_finite(f"{name}.runtime_end_s", document["runtime_end_s"]),
        terminal_classification=classification,
        completion_status=_identifier(f"{name}.completion_status", document["completion_status"]),
        failure_attribution=_identifier(
            f"{name}.failure_attribution", document["failure_attribution"]
        ),
        outcome_reason=(
            None
            if document["outcome_reason"] is None
            else _identifier(f"{name}.outcome_reason", document["outcome_reason"])
        ),
        metrics=tuple(
            _metric_from_document(item, f"{name}.metrics[{index}]")
            for index, item in enumerate(metrics_value)
        ),
    )


def _freeze_counts(value: Mapping[str, object], name: str, expected: frozenset[str]) -> Mapping[str, int]:
    if frozenset(value) != expected:
        raise ValueError(f"{name} must contain exactly {sorted(expected)!r}")
    result: dict[str, int] = {}
    for key in sorted(expected):
        result[key] = _non_negative_count(f"{name}.{key}", value[key])
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class EvaluationArtifactConditionSummary:
    """trial resultから作る非推測的なcondition descriptive summary。"""

    trial_count: int
    terminal_classification_counts: Mapping[str, int]
    metric_status_counts: Mapping[str, Mapping[str, int]]

    def __post_init__(self) -> None:
        trial_count = _non_negative_count("condition summary trial_count", self.trial_count)
        object.__setattr__(self, "trial_count", trial_count)
        terminal = _freeze_counts(
            self.terminal_classification_counts,
            "terminal_classification_counts",
            frozenset(item.value for item in _TERMINAL_CLASSIFICATIONS),
        )
        metric: dict[str, Mapping[str, int]] = {}
        for identity, counts in sorted(self.metric_status_counts.items()):
            _identifier("metric status count evaluator", identity)
            metric[identity] = _freeze_counts(
                counts,
                f"metric_status_counts[{identity!r}]",
                frozenset(item.value for item in _METRIC_STATUSES),
            )
        object.__setattr__(self, "terminal_classification_counts", terminal)
        object.__setattr__(self, "metric_status_counts", MappingProxyType(metric))

    def to_document(self) -> dict[str, object]:
        return {
            "trial_count": self.trial_count,
            "terminal_classification_counts": dict(self.terminal_classification_counts),
            "metric_status_counts": {
                identity: dict(counts)
                for identity, counts in sorted(self.metric_status_counts.items())
            },
        }


_SUMMARY_FIELDS = frozenset(
    {"trial_count", "terminal_classification_counts", "metric_status_counts"}
)


def _summary_from_document(value: object) -> EvaluationArtifactConditionSummary:
    document = _require_fields(value, "condition_summary", _SUMMARY_FIELDS)
    terminal = _require_object(
        document["terminal_classification_counts"],
        "condition_summary.terminal_classification_counts",
    )
    status_value = _require_object(
        document["metric_status_counts"],
        "condition_summary.metric_status_counts",
    )
    metric: dict[str, Mapping[str, object]] = {}
    for identity, counts in status_value.items():
        metric[identity] = _require_object(
            counts, f"condition_summary.metric_status_counts[{identity!r}]"
        )
    return EvaluationArtifactConditionSummary(
        trial_count=_non_negative_count("condition_summary.trial_count", document["trial_count"]),
        terminal_classification_counts=terminal,
        metric_status_counts=metric,
    )


@dataclass(frozen=True, slots=True)
class EvaluationArtifact:
    """1 conditionのtrial metricとdescriptive summaryを束ねるcanonical artifact。"""

    schema_version: str
    artifact_kind: str
    source_log_schema_version: str
    source_log_identity: str
    source_log_sha256: str
    source_record_count: int
    software_revision: str
    configuration_id: str
    manifest_digest: str
    resolved_identity_digest: str
    freeze_identity: str
    experiment_id: str
    session_id: str
    participant_id: str
    condition_id: str
    requested_control_frame: str
    target_id: str
    evaluators: tuple[VersionedIdentity, ...]
    trials: tuple[EvaluationArtifactTrial, ...]
    condition_summary: EvaluationArtifactConditionSummary

    def __post_init__(self) -> None:
        if self.schema_version != EVALUATION_ARTIFACT_SCHEMA_VERSION:
            raise ValueError(f"unsupported evaluation artifact schema version: {self.schema_version!r}")
        if self.artifact_kind != EVALUATION_ARTIFACT_KIND:
            raise ValueError(f"unsupported evaluation artifact kind: {self.artifact_kind!r}")
        if self.source_log_schema_version != EXPERIMENT_MOTION_LOG_SCHEMA_VERSION:
            raise ValueError("artifact source log schema version is unsupported")
        _digest_string("source_log_identity", self.source_log_identity)
        _digest_string("source_log_sha256", self.source_log_sha256)
        if self.source_log_identity != self.source_log_sha256:
            raise ValueError("source log identity and sha256 disagree")
        _non_negative_count("source_record_count", self.source_record_count)
        for name in (
            "software_revision",
            "configuration_id",
            "experiment_id",
            "session_id",
            "participant_id",
            "condition_id",
            "requested_control_frame",
            "target_id",
        ):
            _identifier(name, getattr(self, name))
        for name in ("manifest_digest", "resolved_identity_digest", "freeze_identity"):
            _digest_string(name, getattr(self, name))
        if self.requested_control_frame not in {"world", "tool"}:
            raise ValueError("artifact requested_control_frame must be world or tool")
        evaluators = tuple(self.evaluators)
        if any(not isinstance(item, VersionedIdentity) for item in evaluators):
            raise TypeError("artifact evaluators must use VersionedIdentity")
        if not evaluators or len(set(evaluators)) != len(evaluators):
            raise ValueError("artifact evaluator order must be non-empty and unique")
        for evaluator in evaluators:
            _production_evaluator(evaluator)
        object.__setattr__(self, "evaluators", evaluators)
        trials = tuple(self.trials)
        if not trials:
            raise ValueError("evaluation artifact must contain at least one trial")
        if any(not isinstance(item, EvaluationArtifactTrial) for item in trials):
            raise TypeError("artifact trials must use EvaluationArtifactTrial")
        trial_ids = {item.trial_id for item in trials}
        if len(trial_ids) != len(trials):
            raise ValueError("artifact trial IDs must be unique")
        for trial in trials:
            if (
                trial.condition_id,
                trial.requested_control_frame,
                trial.target_id,
            ) != (self.condition_id, self.requested_control_frame, self.target_id):
                raise ValueError("trial condition facts disagree with artifact identity")
            if tuple(item.evaluator for item in trial.metrics) != evaluators:
                raise ValueError("trial metric order must equal resolved evaluator order")
        expected_summary = _condition_summary(trials)
        if self.condition_summary != expected_summary:
            raise ValueError("condition summary does not match trial metrics")
        object.__setattr__(self, "trials", trials)

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_kind": self.artifact_kind,
            "source_log_schema_version": self.source_log_schema_version,
            "source_log_identity": self.source_log_identity,
            "source_log_sha256": self.source_log_sha256,
            "source_record_count": self.source_record_count,
            "software_revision": self.software_revision,
            "configuration_id": self.configuration_id,
            "manifest_digest": self.manifest_digest,
            "resolved_identity_digest": self.resolved_identity_digest,
            "freeze_identity": self.freeze_identity,
            "experiment_id": self.experiment_id,
            "session_id": self.session_id,
            "participant_id": self.participant_id,
            "condition_id": self.condition_id,
            "requested_control_frame": self.requested_control_frame,
            "target_id": self.target_id,
            "evaluators": [_identity_document(item) for item in self.evaluators],
            "trials": [item.to_document() for item in self.trials],
            "condition_summary": self.condition_summary.to_document(),
        }


_ARTIFACT_FIELDS = frozenset(EvaluationArtifact.__dataclass_fields__)


def _condition_summary(trials: Sequence[EvaluationArtifactTrial]) -> EvaluationArtifactConditionSummary:
    terminal_counts = {item.value: 0 for item in _TERMINAL_CLASSIFICATIONS}
    status_counts: dict[str, dict[str, int]] = {}
    for trial in trials:
        terminal_counts[trial.terminal_classification.value] += 1
        for metric in trial.metrics:
            counts = status_counts.setdefault(
                metric.evaluator.canonical_id,
                {item.value: 0 for item in _METRIC_STATUSES},
            )
            counts[metric.status.value] += 1
    return EvaluationArtifactConditionSummary(
        trial_count=len(trials),
        terminal_classification_counts=terminal_counts,
        metric_status_counts=status_counts,
    )


def encode_evaluation_artifact(artifact: EvaluationArtifact) -> bytes:
    """artifactをfinite・sorted・compactなcanonical UTF-8 JSON bytesへencodeする。"""

    if not isinstance(artifact, EvaluationArtifact):
        raise TypeError("encode_evaluation_artifact requires EvaluationArtifact")
    try:
        text = json.dumps(
            artifact.to_document(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvaluationArtifactError("evaluation artifact is not finite JSON") from exc
    return text.encode("utf-8", errors="strict")


def decode_evaluation_artifact(document: bytes | str | Mapping[str, object]) -> EvaluationArtifact:
    """unknown / duplicate fieldを拒否してartifactをstrict decodeする。"""

    try:
        root = _require_fields(_parse_json_document(document), "artifact", _ARTIFACT_FIELDS)
        evaluator_documents = root["evaluators"]
        trial_documents = root["trials"]
        if not isinstance(evaluator_documents, list) or not evaluator_documents:
            raise ValueError("artifact evaluators must be a non-empty array")
        if not isinstance(trial_documents, list):
            raise ValueError("artifact trials must be an array")
        schema_version = _identifier("artifact.schema_version", root["schema_version"])
        artifact_kind = _identifier("artifact.artifact_kind", root["artifact_kind"])
        return EvaluationArtifact(
            schema_version=schema_version,
            artifact_kind=artifact_kind,
            source_log_schema_version=_identifier(
                "artifact.source_log_schema_version", root["source_log_schema_version"]
            ),
            source_log_identity=_digest_string(
                "artifact.source_log_identity", root["source_log_identity"]
            ),
            source_log_sha256=_digest_string(
                "artifact.source_log_sha256", root["source_log_sha256"]
            ),
            source_record_count=_non_negative_count(
                "artifact.source_record_count", root["source_record_count"]
            ),
            software_revision=_identifier("artifact.software_revision", root["software_revision"]),
            configuration_id=_identifier("artifact.configuration_id", root["configuration_id"]),
            manifest_digest=_digest_string("artifact.manifest_digest", root["manifest_digest"]),
            resolved_identity_digest=_digest_string(
                "artifact.resolved_identity_digest", root["resolved_identity_digest"]
            ),
            freeze_identity=_digest_string("artifact.freeze_identity", root["freeze_identity"]),
            experiment_id=_identifier("artifact.experiment_id", root["experiment_id"]),
            session_id=_identifier("artifact.session_id", root["session_id"]),
            participant_id=_identifier("artifact.participant_id", root["participant_id"]),
            condition_id=_identifier("artifact.condition_id", root["condition_id"]),
            requested_control_frame=_identifier(
                "artifact.requested_control_frame", root["requested_control_frame"]
            ),
            target_id=_identifier("artifact.target_id", root["target_id"]),
            evaluators=tuple(
                _decode_identity(item, f"artifact.evaluators[{index}]")
                for index, item in enumerate(evaluator_documents)
            ),
            trials=tuple(
                _trial_from_document(item, f"artifact.trials[{index}]")
                for index, item in enumerate(trial_documents)
            ),
            condition_summary=_summary_from_document(root["condition_summary"]),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, EvaluationArtifactError):
            raise
        raise EvaluationArtifactError(str(exc)) from exc


def prepare_evaluation_artifact(artifact: EvaluationArtifact) -> bytes:
    """encode -> strict decode -> re-encode equalityを検証する。"""

    encoded = encode_evaluation_artifact(artifact)
    decoded = decode_evaluation_artifact(encoded)
    if encode_evaluation_artifact(decoded) != encoded:
        raise EvaluationArtifactError("evaluation artifact JSON round-trip is not deterministic")
    return encoded


def _source_bytes(
    records: tuple[ExperimentMotionLogRecord, ...],
    source_log: bytes | str | None,
) -> bytes:
    try:
        validate_record_stream(records)
        canonical = encode_jsonl(records).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EvaluationArtifactError(f"source motion log validation failed: {exc}") from exc
    if source_log is None:
        return canonical
    provided = source_log.encode("utf-8", errors="strict") if isinstance(source_log, str) else source_log
    if not isinstance(provided, bytes):
        raise TypeError("source_log must use UTF-8 bytes or text")
    if provided != canonical:
        raise EvaluationArtifactError(
            "source log bytes do not equal strict canonical experiment-motion-log/v1 bytes"
        )
    return provided


def _verify_configuration(
    readiness: EvaluationReadiness,
    configuration: ConfigurationRecord,
) -> None:
    manifest = readiness.manifest
    if configuration.configuration_id != readiness.freeze_record.identity:
        raise EvaluationArtifactError("log configuration_id does not match readiness freeze identity")
    expected_revision = readiness.software_execution_identity.software_revision_identity
    if configuration.software_revision != expected_revision or configuration.software_revision != manifest.software_revision_identity:
        raise EvaluationArtifactError("log software_revision does not match manifest/readiness")
    if configuration.source_kind != readiness.composition.input_source.identity.name:
        raise EvaluationArtifactError("log source_kind does not match resolved Input Source")
    if configuration.target_id != manifest.target_identity:
        raise EvaluationArtifactError("log target_id does not match manifest target identity")
    if not _vector_close(configuration.target_world_position_m, manifest.target_world_position_m):
        raise EvaluationArtifactError("log target position does not match manifest")
    for name, actual, expected in (
        ("target_tolerance_m", configuration.target_tolerance_m, manifest.target_tolerance_m),
        ("dwell_interval_s", configuration.dwell_interval_s, manifest.dwell_interval_s),
        ("timeout_s", configuration.timeout_s, manifest.timeout_s),
        ("local_endpoint_speed_m_s", configuration.local_endpoint_speed_m_s, manifest.gain),
        ("deadzone", configuration.deadzone, manifest.deadzone),
        ("local_endpoint_max_delta_m", configuration.local_endpoint_max_delta_m, manifest.maximum_per_step_delta_m),
    ):
        if abs(actual - expected) > _VECTOR_TOLERANCE:
            raise EvaluationArtifactError(f"log {name} does not match manifest")
    if not runtime_measurement_matches_frozen(
        configuration.initial_qpos_rad,
        manifest.initial_qpos_rad,
    ):
        raise EvaluationArtifactError("log initial qpos does not match manifest")
    if not _vector_close(
        configuration.initial_measured_tip_position_m,
        manifest.initial_tip_position_m,
        _INITIAL_POSITION_NUMERICAL_TOLERANCE_M,
    ):
        raise EvaluationArtifactError("log initial measured tip does not match manifest")
    if not runtime_measurement_matches_frozen(
        configuration.initial_tool_orientation_wxyz,
        manifest.initial_tool_orientation_wxyz,
        allow_quaternion_sign_equivalence=True,
    ):
        raise EvaluationArtifactError("log initial tool orientation does not match manifest")
    known_parameters = dict(comparison_parameters_for_readiness(readiness))
    comparison_parameters = dict(configuration.comparison_parameters)
    missing_parameters = sorted(set(known_parameters) - set(comparison_parameters))
    extra_parameters = sorted(set(comparison_parameters) - set(known_parameters))
    if missing_parameters or extra_parameters:
        raise EvaluationArtifactError(
            "log comparison parameters do not exactly match canonical readiness projection: "
            f"missing={missing_parameters}, extra={extra_parameters}"
        )
    for key, expected in known_parameters.items():
        if comparison_parameters[key] != expected:
            raise EvaluationArtifactError(f"log comparison parameter {key!r} does not match readiness")


def _elapsed_from_trial_start(
    trial_start: TrialStartRecord,
    timestamp_s: float,
    name: str,
) -> float:
    elapsed = _finite(name, timestamp_s) - _finite(
        "trial_start.runtime_timestamp_s", trial_start.runtime_timestamp_s
    )
    if elapsed < 0.0:
        raise EvaluationArtifactError(
            f"{name} precedes trial start runtime timestamp"
        )
    return elapsed


def _terminal_evidence(
    trial_start: TrialStartRecord,
    outcome: TrialOutcomeRecord,
) -> CanonicalEvidence:
    try:
        classification = _COMPLETION_TO_TERMINAL[outcome.completion_status]
    except KeyError as exc:
        raise EvaluationArtifactError("log outcome has an unsupported completion status") from exc
    return CanonicalEvidence(
        identity=ENDPOINT_REACH_TERMINAL_EVIDENCE,
        status=EvidenceStatus.MEASURED,
        value={
            "classification": classification.value,
            "elapsed_time_s": _elapsed_from_trial_start(
                trial_start,
                outcome.runtime_timestamp_s,
                "outcome.runtime_timestamp_s",
            ),
            "reason": outcome.outcome_reason,
        },
        provenance=ENDPOINT_REACH_TERMINAL_PROVENANCE,
    )


def _trajectory_evidence(
    configuration: ConfigurationRecord,
    trial_start: TrialStartRecord,
    samples: Sequence[MotionSampleRecord],
    outcome: TrialOutcomeRecord,
) -> CanonicalEvidence:
    if outcome.completion_status == "technical_invalid":
        return CanonicalEvidence(
            identity=ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
            status=EvidenceStatus.INVALID,
            value=None,
            provenance=ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
            reason=outcome.outcome_reason or "technical-invalid trial invalidates endpoint trajectory",
        )
    if not samples:
        status = (
            EvidenceStatus.INVALID
            if outcome.completion_status == "technical_invalid"
            else EvidenceStatus.UNAVAILABLE
        )
        return CanonicalEvidence(
            identity=ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
            status=status,
            value=None,
            provenance=ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
            reason="motion log contains no measured endpoint sample",
        )
    for sample in samples:
        measured = (
            sample.measured_tip_position_before_m,
            sample.measured_tip_position_after_m,
            sample.actual_tip_delta_m,
        )
        if any(value is None for value in measured):
            status = (
                EvidenceStatus.INVALID
                if outcome.completion_status == "technical_invalid"
                else EvidenceStatus.UNAVAILABLE
            )
            return CanonicalEvidence(
                identity=ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
                status=status,
                value=None,
                provenance=ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
                reason=f"motion sample {sample.sample_index} lacks complete measured endpoint evidence",
            )
    first_before = samples[0].measured_tip_position_before_m
    assert first_before is not None
    if not _vector_close(first_before, configuration.initial_measured_tip_position_m):
        raise EvaluationArtifactError("first measured sample does not start at configuration endpoint")
    for previous, current in zip(samples, samples[1:]):
        assert previous.measured_tip_position_after_m is not None
        assert current.measured_tip_position_before_m is not None
        if not _vector_close(previous.measured_tip_position_after_m, current.measured_tip_position_before_m):
            raise EvaluationArtifactError("measured endpoint trajectory is discontinuous")
    sample_values: list[dict[str, object]] = []
    previous_elapsed = 0.0
    for sample in samples:
        elapsed = _elapsed_from_trial_start(
            trial_start,
            sample.runtime_timestamp_s,
            f"motion sample {sample.sample_index}.runtime_timestamp_s",
        )
        if elapsed < previous_elapsed:
            raise EvaluationArtifactError("measured endpoint sample times are not monotonic")
        previous_elapsed = elapsed
        sample_values.append(
            {
                "elapsed_time_s": elapsed,
                "position_world_m": sample.measured_tip_position_after_m,
            }
        )
    values: tuple[dict[str, object], ...] = (
        {
            "elapsed_time_s": 0.0,
            "position_world_m": configuration.initial_measured_tip_position_m,
        },
        *sample_values,
    )
    return CanonicalEvidence(
        identity=ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
        status=EvidenceStatus.MEASURED,
        value={
            "initial_position_world_m": configuration.initial_measured_tip_position_m,
            "target_position_world_m": configuration.target_world_position_m,
            "samples": values,
        },
        provenance=ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
    )


def reconstruct_task_evidence_from_motion_log(
    configuration: ConfigurationRecord,
    trial_start: TrialStartRecord,
    samples: Sequence[MotionSampleRecord],
    outcome: TrialOutcomeRecord,
) -> CanonicalEvidenceSet:
    """validated v1 trial factsをTask-owned terminal/trajectory evidenceへ投影する。

    requested、predicted、またはzeroをmeasured trajectoryへ昇格させない。producer
    provenanceは既存endpoint-reach evidence codecのcanonical boundaryへ固定する。
    """

    if trial_start.trial_id != outcome.trial_id:
        raise EvaluationArtifactError("trial start/outcome identity mismatch")
    if any(sample.trial_id != trial_start.trial_id for sample in samples):
        raise EvaluationArtifactError("motion sample trial identity mismatch")
    return CanonicalEvidenceSet(
        (
            _terminal_evidence(trial_start, outcome),
            _trajectory_evidence(configuration, trial_start, samples, outcome),
        )
    )


def _evaluator_parameters(readiness: EvaluationReadiness, evaluator: VersionedIdentity) -> Mapping[str, object]:
    for item in readiness.manifest.parameters:
        if (
            item.owner.axis is PluginAxis.EVALUATION
            and item.owner.selection.plugin_id == evaluator.name
            and item.owner.selection.contract_version == evaluator.version
        ):
            return MappingProxyType(dict(item.values))
    return MappingProxyType({})


def _verify_selected_evaluators(readiness: EvaluationReadiness) -> None:
    """readinessのordered selectionがproduction catalogのresolved objectであることを確認する。"""

    selections = tuple(readiness.composition.manifest.evaluators)
    evaluators = tuple(readiness.composition.evaluators)
    if len(selections) != len(evaluators) or not evaluators:
        raise EvaluationArtifactError("readiness evaluator selection/resolution length mismatch")
    for selection, evaluator in zip(selections, evaluators, strict=True):
        expected = VersionedIdentity(selection.plugin_id, selection.contract_version)
        if evaluator.identity != expected:
            raise EvaluationArtifactError(
                "readiness evaluator selection does not match resolved evaluator identity"
            )
        try:
            production = _production_evaluator(evaluator.identity)
        except ValueError as exc:
            raise EvaluationArtifactError(str(exc)) from exc
        if evaluator is not production:
            raise EvaluationArtifactError(
                f"readiness evaluator is not the resolved production object: "
                f"{evaluator.identity.canonical_id!r}"
            )


def _metric_results(
    readiness: EvaluationReadiness,
    evidence: CanonicalEvidenceSet,
) -> tuple[EvaluationArtifactMetric, ...]:
    results: list[EvaluationArtifactMetric] = []
    for evaluator in readiness.composition.evaluators:
        try:
            result = evaluator.derive_metric(
                evidence,
                _evaluator_parameters(readiness, evaluator.identity),
            )
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise EvaluationArtifactError(
                f"evaluation plugin {evaluator.identity.canonical_id!r} failed closed: {exc}"
            ) from exc
        if result.metric_id != evaluator.identity:
            raise EvaluationArtifactError(
                f"evaluation plugin {evaluator.identity.canonical_id!r} returned another metric identity"
            )
        results.append(
            EvaluationArtifactMetric(
                evaluator=evaluator.identity,
                status=result.status,
                value=result.value,
                unit=evaluator.unit,
                frame=evaluator.frame,
                provenance=result.provenance,
                reason=result.reason,
            )
        )
    return tuple(results)


def _trial_artifact(
    readiness: EvaluationReadiness,
    configuration: ConfigurationRecord,
    start: TrialStartRecord,
    samples: Sequence[MotionSampleRecord],
    outcome: TrialOutcomeRecord,
) -> EvaluationArtifactTrial:
    evidence = reconstruct_task_evidence_from_motion_log(configuration, start, samples, outcome)
    return EvaluationArtifactTrial(
        trial_id=start.trial_id,
        condition_id=readiness.manifest.condition_id,
        requested_control_frame=start.control_condition,
        target_id=start.target_id,
        condition_order=start.condition_order,
        task_order=start.task_order,
        target_direction=start.target_direction,
        practice=start.practice,
        repetition_index=start.repetition_index,
        attempt_index=start.attempt_index,
        retry_of_trial_id=start.retry_of_trial_id,
        runtime_start_s=start.runtime_timestamp_s,
        runtime_end_s=outcome.runtime_timestamp_s,
        terminal_classification=_COMPLETION_TO_TERMINAL[outcome.completion_status],
        completion_status=outcome.completion_status,
        failure_attribution=outcome.failure_attribution,
        outcome_reason=outcome.outcome_reason,
        metrics=_metric_results(readiness, evidence),
    )


def build_evaluation_artifact(
    readiness: EvaluationReadiness,
    records: Iterable[ExperimentMotionLogRecord],
    *,
    source_log: bytes | str | None = None,
) -> EvaluationArtifact:
    """1 conditionのstrict v1 streamからdeterministic evaluation artifactを構築する。"""

    if not isinstance(readiness, EvaluationReadiness):
        raise TypeError("build_evaluation_artifact requires EvaluationReadiness")
    try:
        verify_freeze_identity(readiness.freeze_record, readiness.manifest, readiness)
    except (TypeError, ValueError) as exc:
        raise EvaluationArtifactError(f"readiness freeze identity verification failed: {exc}") from exc
    _verify_selected_evaluators(readiness)
    typed_records = tuple(records)
    source_bytes = _source_bytes(typed_records, source_log)
    configurations = [
        item
        for item in typed_records
        if isinstance(item, ConfigurationRecord)
        and item.configuration_id == readiness.freeze_record.identity
    ]
    if len(configurations) != 1:
        raise EvaluationArtifactError(
            "source motion log must contain exactly one configuration for the requested freeze identity"
        )
    configuration = configurations[0]
    _verify_configuration(readiness, configuration)
    starts = [
        item
        for item in typed_records
        if isinstance(item, TrialStartRecord)
        and item.configuration_id == configuration.configuration_id
    ]
    if not starts:
        raise EvaluationArtifactError("source motion log contains no trial for the requested condition")
    outcomes_by_trial = {
        item.trial_id: item
        for item in typed_records
        if isinstance(item, TrialOutcomeRecord)
        and item.configuration_id == configuration.configuration_id
    }
    samples_by_trial: dict[str, list[MotionSampleRecord]] = {}
    for item in typed_records:
        if isinstance(item, MotionSampleRecord) and item.configuration_id == configuration.configuration_id:
            samples_by_trial.setdefault(item.trial_id, []).append(item)
    trials: list[EvaluationArtifactTrial] = []
    for start in starts:
        if start.control_condition != readiness.manifest.requested_control_frame:
            raise EvaluationArtifactError("trial requested control frame does not match readiness")
        if start.condition_order != readiness.manifest.condition_order or start.task_order != readiness.manifest.task_order:
            raise EvaluationArtifactError("trial condition/task order does not match readiness")
        if start.target_id != readiness.manifest.target_identity:
            raise EvaluationArtifactError("trial target identity does not match readiness")
        outcome = outcomes_by_trial.get(start.trial_id)
        if outcome is None:
            raise EvaluationArtifactError("source motion log contains an unclosed requested trial")
        samples = tuple(samples_by_trial.get(start.trial_id, ()))
        trials.append(_trial_artifact(readiness, configuration, start, samples, outcome))
    return EvaluationArtifact(
        schema_version=EVALUATION_ARTIFACT_SCHEMA_VERSION,
        artifact_kind=EVALUATION_ARTIFACT_KIND,
        source_log_schema_version=EXPERIMENT_MOTION_LOG_SCHEMA_VERSION,
        source_log_identity=_digest(source_bytes),
        source_log_sha256=_digest(source_bytes),
        source_record_count=len(typed_records),
        software_revision=configuration.software_revision,
        configuration_id=configuration.configuration_id,
        manifest_digest=readiness.manifest_digest,
        resolved_identity_digest=readiness.resolved_identity_digest,
        freeze_identity=readiness.freeze_identity,
        experiment_id=configuration.experiment_id,
        session_id=configuration.session_id,
        participant_id=configuration.participant_id,
        condition_id=readiness.manifest.condition_id,
        requested_control_frame=readiness.manifest.requested_control_frame,
        target_id=readiness.manifest.target_identity,
        evaluators=tuple(item.identity for item in readiness.composition.evaluators),
        trials=tuple(trials),
        condition_summary=_condition_summary(trials),
    )


def build_evaluation_artifact_from_jsonl(
    readiness: EvaluationReadiness,
    source_log: bytes | str,
) -> EvaluationArtifact:
    """strict JSONL sourceをdecodeしてcondition artifactを構築する。"""

    try:
        records = decode_jsonl(source_log.decode("utf-8") if isinstance(source_log, bytes) else source_log)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise EvaluationArtifactError(f"source motion log decode failed: {exc}") from exc
    return build_evaluation_artifact(readiness, records, source_log=source_log)


def build_world_tool_evaluation_artifacts(
    readiness: EvaluationConditionPairReadiness,
    records: Iterable[ExperimentMotionLogRecord],
    *,
    source_log: bytes | str | None = None,
) -> tuple[EvaluationArtifact, EvaluationArtifact]:
    """同一canonical streamからcondition orderどおりworld/tool artifactを作る。"""

    if not isinstance(readiness, EvaluationConditionPairReadiness):
        raise TypeError("build_world_tool_evaluation_artifacts requires pair readiness")
    typed_records = tuple(records)
    prepared_source = _source_bytes(typed_records, source_log)
    artifacts = tuple(
        build_evaluation_artifact(item, typed_records, source_log=prepared_source)
        for item in sorted((readiness.world, readiness.tool), key=lambda item: item.manifest.condition_order)
    )
    if len(artifacts) != 2:
        raise EvaluationArtifactError("world/tool artifact pair must contain two conditions")
    return artifacts  # type: ignore[return-value]


def build_world_tool_evaluation_artifacts_from_jsonl(
    readiness: EvaluationConditionPairReadiness,
    source_log: bytes | str,
) -> tuple[EvaluationArtifact, EvaluationArtifact]:
    try:
        records = decode_jsonl(source_log.decode("utf-8") if isinstance(source_log, bytes) else source_log)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise EvaluationArtifactError(f"source motion log decode failed: {exc}") from exc
    return build_world_tool_evaluation_artifacts(readiness, records, source_log=source_log)


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _target_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


class _ProcessPathLock:
    __slots__ = ("lock", "users", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.users = 0


_PROCESS_PATH_LOCKS: weakref.WeakValueDictionary[str, _ProcessPathLock] = (
    weakref.WeakValueDictionary()
)
_PROCESS_PATH_LOCKS_GUARD = threading.Lock()


def _target_lock_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


@contextmanager
def _process_local_target_lock(target_path: Path):
    """同一process内のthread再入を防ぐboundedなnon-blocking lock。"""

    key = _target_lock_key(target_path)
    with _PROCESS_PATH_LOCKS_GUARD:
        holder = _PROCESS_PATH_LOCKS.get(key)
        if holder is None:
            holder = _ProcessPathLock()
            _PROCESS_PATH_LOCKS[key] = holder
        holder.users += 1
    if not holder.lock.acquire(blocking=False):
        with _PROCESS_PATH_LOCKS_GUARD:
            holder.users -= 1
            if holder.users == 0 and _PROCESS_PATH_LOCKS.get(key) is holder:
                del _PROCESS_PATH_LOCKS[key]
        raise EvaluationArtifactError(
            f"evaluation artifact target lock is already held in process: {target_path.name}"
        )
    try:
        yield
    finally:
        holder.lock.release()
        with _PROCESS_PATH_LOCKS_GUARD:
            holder.users -= 1
            if holder.users == 0 and _PROCESS_PATH_LOCKS.get(key) is holder:
                del _PROCESS_PATH_LOCKS[key]


def _open_kernel_target_lock(target_path: Path) -> int:
    """persistent sidecarをkernel advisory lockでnon-blocking取得する。"""

    lock_path = _target_lock_path(target_path)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise EvaluationArtifactError(
            f"evaluation artifact target lock is already held or unavailable: {lock_path.name}"
        ) from exc
    return descriptor


def _close_kernel_target_lock(descriptor: int) -> None:
    """lockをreleaseし、sidecarはpersistent operational stateとして残す。"""

    try:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            # closeがkernel lockを解放する。sidecar unlinkは行わない。
            pass
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


@contextmanager
def _exclusive_target_lock(target_path: Path):
    """persistent sidecarのkernel lockをcritical section全体で保持する。"""

    with _process_local_target_lock(target_path):
        descriptor = _open_kernel_target_lock(target_path)
        try:
            yield
        finally:
            _close_kernel_target_lock(descriptor)


def _write_fsynced(path: Path, value: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _restore_target(path: Path, previous: bytes | None) -> None:
    if previous is None:
        if path.exists():
            path.unlink()
        return
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".rollback")
    os.close(descriptor)
    rollback = Path(name)
    try:
        _write_fsynced(rollback, previous)
        os.replace(rollback, path)
    finally:
        if rollback.exists():
            rollback.unlink()


def _write_evaluation_artifact_locked(target_path: Path, encoded: bytes) -> bytes:
    previous = _read_bytes(target_path) if target_path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target_path.parent,
        prefix=f".{target_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    descriptor_open = True
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_open = False
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if _read_bytes(temporary_path) != encoded:
            raise EvaluationArtifactError("temporary evaluation artifact read-back mismatch")
        current = _read_bytes(target_path) if target_path.exists() else None
        if current != previous:
            raise EvaluationArtifactError("evaluation artifact target changed before atomic replace")
        os.replace(temporary_path, target_path)
        replaced = True
        if _read_bytes(target_path) != encoded:
            raise EvaluationArtifactError("final evaluation artifact read-back mismatch")
    except (OSError, UnicodeError, EvaluationArtifactError) as exc:
        if replaced:
            try:
                _restore_target(target_path, previous)
            except OSError as rollback_exc:
                raise EvaluationArtifactError(
                    f"evaluation artifact rollback failed: {rollback_exc}"
                ) from rollback_exc
        if isinstance(exc, EvaluationArtifactError):
            raise
        raise EvaluationArtifactError(f"atomic evaluation artifact write failed: {exc}") from exc
    finally:
        if descriptor_open:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()
    return encoded


def write_evaluation_artifact_atomic(
    target: str | os.PathLike[str],
    artifact: EvaluationArtifact,
) -> bytes:
    """strict round-trip済みartifactをlock内でsame-directory atomic replaceする。"""

    encoded = prepare_evaluation_artifact(artifact)
    target_path = Path(target)
    if not target_path.parent.is_dir():
        raise EvaluationArtifactError("evaluation artifact target directory must already exist")
    with _exclusive_target_lock(target_path):
        return _write_evaluation_artifact_locked(target_path, encoded)


__all__ = [
    "EVALUATION_ARTIFACT_KIND",
    "EVALUATION_ARTIFACT_SCHEMA_VERSION",
    "EvaluationArtifact",
    "EvaluationArtifactConditionSummary",
    "EvaluationArtifactError",
    "EvaluationArtifactMetric",
    "EvaluationArtifactTrial",
    "build_evaluation_artifact",
    "build_evaluation_artifact_from_jsonl",
    "build_world_tool_evaluation_artifacts",
    "build_world_tool_evaluation_artifacts_from_jsonl",
    "decode_evaluation_artifact",
    "encode_evaluation_artifact",
    "prepare_evaluation_artifact",
    "reconstruct_task_evidence_from_motion_log",
    "write_evaluation_artifact_atomic",
]
