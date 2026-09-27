"""R7-Gのcanonical software-only E2E orchestration。

このmoduleは#405〜#408の既存ownerを順番に接続するthin entry pointである。
manifest、runner、experiment-motion-log/v1、Task evidence reconstruction、
production Evaluation Plugin、evaluation-artifact/v1の計算を複製しない。
wall-clock、temporary path、UUID、process identityはcanonical outputへ含めない。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
import sys
from typing import TypeAlias

from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.evaluation.artifact import (
    EvaluationArtifact,
    EvaluationArtifactError,
    build_world_tool_evaluation_artifacts_from_jsonl,
    decode_evaluation_artifact,
    encode_evaluation_artifact,
    reconstruct_task_evidence_from_motion_log,
    write_evaluation_artifact_atomic,
)
from xpotato_sim.runtime.evaluation.manifest import (
    EvaluationConditionPair,
    EvaluationConditionPairReadiness,
    SoftwareExecutionIdentity,
    build_evaluation_condition_pair_readiness,
)
from xpotato_sim.runtime.evaluation.r7_g_free_space import (
    build_r7_g_free_space_manifest_pair,
)
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidenceSet,
    TaskTerminalClassification,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.motion_log_recorder import (
    TrialProtocolContext,
    WorldToolTrialProtocolContext,
    build_world_tool_motion_log_records,
    prepare_motion_log,
    write_motion_log_atomic,
)
from xpotato_sim.runtime.experiment.world_tool_runner import (
    ExperimentConditionExecutionResult,
    WorldToolExperimentExecutionResult,
    run_evaluation_condition_pair,
)
from xpotato_sim.schemas.experiment_log import (
    ConfigurationRecord,
    ExperimentMotionLogRecord,
    MotionSampleRecord,
    TrialOutcomeRecord,
    TrialStartRecord,
    decode_jsonl,
)


R7_G_E2E_REPOSITORY_IDENTITY = "Xpotato1024/Xpotato-Sim"
R7_G_E2E_EXPERIMENT_ID = "experiment-408"
R7_G_E2E_SESSION_ID = "session-408"
R7_G_E2E_PARTICIPANT_ID = "opaque-participant-408"
R7_G_E2E_BLOCK_ID = "block-0"
R7_G_E2E_TASK_FAMILY = "endpoint-reach"
R7_G_E2E_TARGET_DIRECTION = "positive-y"

R7_G_E2E_MOTION_LOG_NAME = "r7-g-e2e.motion-log.jsonl"
R7_G_E2E_WORLD_ARTIFACT_NAME = "r7-g-e2e-world.evaluation-artifact.json"
R7_G_E2E_TOOL_ARTIFACT_NAME = "r7-g-e2e-tool.evaluation-artifact.json"

_EXPECTED_CONDITION_FACTS: Mapping[str, tuple[TaskTerminalClassification, int, float]] = {
    "world": (TaskTerminalClassification.SUCCESS, 57, 1.14),
    "tool": (TaskTerminalClassification.FAILURE, 250, 5.0),
}


class R7GE2EError(RuntimeError):
    """R7-G E2Eのorchestrationまたはstrict completion checkの失敗。"""


ExperimentEvidence: TypeAlias = CanonicalEvidenceSet


@dataclass(frozen=True, slots=True)
class R7GE2EConditionResult:
    """1 conditionの実行、再構成、metric、artifactを束ねる結果。"""

    condition_id: str
    execution: ExperimentConditionExecutionResult
    evidence: ExperimentEvidence
    artifact: EvaluationArtifact
    artifact_bytes: bytes

    @property
    def metric_results(self) -> tuple[tuple[str, str, object | None], ...]:
        return tuple(
            (
                metric.evaluator.canonical_id,
                metric.status.value,
                metric.value,
            )
            for metric in self.artifact.trials[0].metrics
        )


@dataclass(frozen=True, slots=True)
class R7GE2ERun:
    """一回分のcanonical E2E（output pathを含まない）。"""

    readiness: EvaluationConditionPairReadiness
    execution: WorldToolExperimentExecutionResult
    records: tuple[ExperimentMotionLogRecord, ...]
    motion_log_bytes: bytes
    conditions: tuple[R7GE2EConditionResult, ...]

    def condition(self, condition_id: str) -> R7GE2EConditionResult:
        for item in self.conditions:
            if item.condition_id == condition_id:
                return item
        raise KeyError(condition_id)


@dataclass(frozen=True, slots=True)
class R7GE2EResult:
    """determinismとnegative controlを通過したE2Eの最終結果。"""

    run: R7GE2ERun
    negative_controls: tuple[str, ...]
    output_names: tuple[str, ...]


def _canonical_contexts() -> WorldToolTrialProtocolContext:
    common = dict(
        experiment_id=R7_G_E2E_EXPERIMENT_ID,
        session_id=R7_G_E2E_SESSION_ID,
        participant_id=R7_G_E2E_PARTICIPANT_ID,
        block_id=R7_G_E2E_BLOCK_ID,
        task_family=R7_G_E2E_TASK_FAMILY,
        practice=False,
        target_direction=R7_G_E2E_TARGET_DIRECTION,
        repetition_index=0,
        attempt_index=0,
        retry_of_trial_id=None,
    )
    return WorldToolTrialProtocolContext(
        world=TrialProtocolContext(**common, direction_order=0),
        tool=TrialProtocolContext(**common, direction_order=1),
    )


def _normalise(value: object) -> object:
    """determinism比較用のside-effect-free JSON-compatible projection。"""

    if isinstance(value, VersionedIdentity):
        return value.canonical_id
    if hasattr(value, "value") and type(value).__module__ != "builtins":
        enum_value = getattr(value, "value")
        if isinstance(enum_value, (str, int, float, bool)) or enum_value is None:
            return enum_value
    if isinstance(value, Mapping):
        return {
            str(key): _normalise(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _evidence_signature(evidence: CanonicalEvidenceSet) -> tuple[object, ...]:
    entries = []
    for identity in sorted(evidence.identities):
        entry = evidence.require(identity)
        entries.append(
            (
                identity.canonical_id,
                entry.status.value,
                _normalise(entry.value),
                entry.provenance,
                entry.reason,
            )
        )
    return tuple(entries)


def _condition_records(
    records: Sequence[ExperimentMotionLogRecord],
    condition_id: str,
) -> tuple[ConfigurationRecord, TrialStartRecord, tuple[MotionSampleRecord, ...], TrialOutcomeRecord]:
    starts = tuple(
        item
        for item in records
        if isinstance(item, TrialStartRecord) and item.control_condition == condition_id
    )
    if len(starts) != 1:
        raise R7GE2EError(
            f"canonical E2E requires one {condition_id} trial start, got {len(starts)}"
        )
    start = starts[0]
    configurations = tuple(
        item
        for item in records
        if isinstance(item, ConfigurationRecord)
        and item.configuration_id == start.configuration_id
    )
    outcomes = tuple(
        item
        for item in records
        if isinstance(item, TrialOutcomeRecord) and item.trial_id == start.trial_id
    )
    if len(configurations) != 1 or len(outcomes) != 1:
        raise R7GE2EError(
            f"canonical E2E requires one configuration/outcome for {condition_id}"
        )
    samples = tuple(
        item
        for item in records
        if isinstance(item, MotionSampleRecord) and item.trial_id == start.trial_id
    )
    return configurations[0], start, samples, outcomes[0]


def _assert_expected_execution(execution: WorldToolExperimentExecutionResult) -> None:
    for condition in (execution.world, execution.tool):
        expected = _EXPECTED_CONDITION_FACTS.get(condition.condition_id)
        if expected is None:
            raise R7GE2EError(f"unexpected canonical condition: {condition.condition_id!r}")
        classification, step_count, elapsed = expected
        if condition.classification is not classification:
            raise R7GE2EError(
                f"{condition.condition_id} classification drifted: "
                f"expected={classification.value!r}, actual={condition.classification.value!r}"
            )
        if condition.step_count != step_count:
            raise R7GE2EError(
                f"{condition.condition_id} step count drifted: "
                f"expected={step_count}, actual={condition.step_count}"
            )
        if not isclose(condition.final_elapsed_time_s, elapsed, rel_tol=0.0, abs_tol=1e-12):
            raise R7GE2EError(
                f"{condition.condition_id} simulation time drifted: "
                f"expected={elapsed}, actual={condition.final_elapsed_time_s}"
            )


def _build_run(
    *,
    manifest_software_revision_identity: str,
    execution_identity: SoftwareExecutionIdentity,
) -> R7GE2ERun:
    pair: EvaluationConditionPair = build_r7_g_free_space_manifest_pair(
        software_revision_identity=manifest_software_revision_identity,
    )
    readiness = build_evaluation_condition_pair_readiness(
        pair,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=execution_identity,
    )
    execution = run_evaluation_condition_pair(readiness)
    _assert_expected_execution(execution)

    records = build_world_tool_motion_log_records(
        readiness,
        execution,
        _canonical_contexts(),
    )
    prepared = prepare_motion_log(records)
    source_bytes = prepared.bytes_value
    decoded_records = decode_jsonl(source_bytes.decode("utf-8"))
    artifacts = build_world_tool_evaluation_artifacts_from_jsonl(
        readiness,
        source_bytes,
    )

    conditions: list[R7GE2EConditionResult] = []
    executions = {"world": execution.world, "tool": execution.tool}
    for artifact in artifacts:
        condition_id = artifact.condition_id
        configuration, start, samples, outcome = _condition_records(
            decoded_records,
            condition_id,
        )
        evidence = reconstruct_task_evidence_from_motion_log(
            configuration,
            start,
            samples,
            outcome,
        )
        expected_evidence = executions[condition_id].transition.evidence
        if _evidence_signature(evidence) != _evidence_signature(expected_evidence):
            raise R7GE2EError(
                f"{condition_id} reconstructed canonical evidence disagrees with runner evidence"
            )
        artifact_bytes = encode_evaluation_artifact(artifact)
        if decode_evaluation_artifact(artifact_bytes) != artifact:
            raise R7GE2EError(f"{condition_id} evaluation artifact strict read-back mismatch")
        conditions.append(
            R7GE2EConditionResult(
                condition_id=condition_id,
                execution=executions[condition_id],
                evidence=evidence,
                artifact=artifact,
                artifact_bytes=artifact_bytes,
            )
        )
    if tuple(item.condition_id for item in conditions) != ("world", "tool"):
        raise R7GE2EError("canonical artifacts must be ordered world then tool")
    return R7GE2ERun(
        readiness=readiness,
        execution=execution,
        records=prepared.records,
        motion_log_bytes=source_bytes,
        conditions=tuple(conditions),
    )


def _run_signature(run: R7GE2ERun) -> tuple[object, ...]:
    return (
        (
            run.readiness.pair_identity,
            run.readiness.world.manifest_digest,
            run.readiness.world.resolved_identity_digest,
            run.readiness.world.freeze_identity,
            run.readiness.tool.manifest_digest,
            run.readiness.tool.resolved_identity_digest,
            run.readiness.tool.freeze_identity,
        ),
        run.motion_log_bytes,
        tuple(
            (
                item.condition_id,
                item.execution.classification.value,
                item.execution.step_count,
                item.execution.final_elapsed_time_s,
                _evidence_signature(item.evidence),
                item.metric_results,
                item.artifact_bytes,
            )
            for item in run.conditions
        ),
    )


def _assert_deterministic(first: R7GE2ERun, second: R7GE2ERun) -> None:
    if _run_signature(first) != _run_signature(second):
        raise R7GE2EError(
            "canonical E2E repeated run changed execution, evidence, metric, or artifact bytes"
        )


def _failed_outcome(outcome: TrialOutcomeRecord, reason: str) -> TrialOutcomeRecord:
    return replace(
        outcome,
        runtime_timestamp_s=outcome.runtime_timestamp_s,
        completion_status="failed",
        success_within_timeout=False,
        final_measured_endpoint_error_m=None,
        failure_attribution="operator",
        outcome_reason=reason,
        primary_outcome_sample_index=None,
    )


def _mutated_world_records(
    run: R7GE2ERun,
    sample_mutator: Callable[[MotionSampleRecord], MotionSampleRecord],
) -> tuple[ExperimentMotionLogRecord, ...]:
    _, _, samples, _ = _condition_records(run.records, "world")
    if not samples:
        raise R7GE2EError("negative control requires a canonical world sample")
    first_sample = samples[0]
    mutated = sample_mutator(first_sample)
    return tuple(
        mutated if item is first_sample else item
        for item in run.records
    )


def _assert_artifact_rejected(
    run: R7GE2ERun,
    records: Iterable[ExperimentMotionLogRecord],
    control_name: str,
) -> None:
    try:
        build_world_tool_evaluation_artifacts_from_jsonl(
            run.readiness,
            prepare_motion_log(records).bytes_value,
        )
    except (EvaluationArtifactError, TypeError, ValueError, UnicodeError):
        return
    raise R7GE2EError(f"negative control {control_name!r} was accepted")


def _derived_negative_revision_identity(revision: str) -> str:
    """現在のdeclared revisionから、syntaxを保った異なるnegative identityを導出する。"""

    if revision.startswith("test-revision:"):
        # suffixが既に付いていてもさらにappendするため、常に入力と異なる。
        candidate = f"{revision}-r7-g-negative-control"
    elif revision.startswith(("git-sha1:", "git-sha256:")):
        # git schemeは固定長のため、末尾hexを別の有効値へ反転する。
        replacement = "1" if revision[-1] == "0" else "0"
        candidate = f"{revision[:-1]}{replacement}"
    else:  # pragma: no cover - manifest readiness already validates this boundary
        raise R7GE2EError(f"unsupported software revision identity: {revision!r}")
    if candidate == revision:
        raise R7GE2EError("negative revision identity must differ from its declared input")
    return candidate


def _assert_not_successful_artifact(
    run: R7GE2ERun,
    records: Iterable[ExperimentMotionLogRecord],
    control_name: str,
) -> None:
    try:
        artifact = build_world_tool_evaluation_artifacts_from_jsonl(
            run.readiness,
            prepare_motion_log(records).bytes_value,
        )[0]
    except (EvaluationArtifactError, TypeError, ValueError):
        return
    metric = next(
        item
        for item in artifact.trials[0].metrics
        if item.evaluator.canonical_id == "success_within_timeout/v1"
    )
    if metric.value is True:
        raise R7GE2EError(f"negative control {control_name!r} was accepted as success")
    if artifact.trials[0].terminal_classification is TaskTerminalClassification.SUCCESS:
        raise R7GE2EError(
            f"negative control {control_name!r} retained a successful terminal classification"
        )
    if control_name == "technical_invalid" and not any(
        item.status.value == "invalid" for item in artifact.trials[0].metrics
    ):
        raise R7GE2EError(
            "technical-invalid negative control did not produce an invalid metric"
        )


def _run_negative_controls(run: R7GE2ERun) -> tuple[str, ...]:
    names: list[str] = []

    pair = build_r7_g_free_space_manifest_pair(
        software_revision_identity=run.readiness.world.manifest.software_revision_identity,
    )
    tampered_revision = _derived_negative_revision_identity(
        run.readiness.world.manifest.software_revision_identity,
    )
    mismatched_identity = replace(
        run.readiness.world.software_execution_identity,
        software_revision_identity=tampered_revision,
    )
    try:
        build_evaluation_condition_pair_readiness(
            pair,
            PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
            execution_identity=mismatched_identity,
        )
    except (TypeError, ValueError, RuntimeError):
        names.append("readiness_mismatch")
    else:
        raise R7GE2EError("readiness mismatch negative control was accepted")

    malformed = run.motion_log_bytes[:-1]
    try:
        build_world_tool_evaluation_artifacts_from_jsonl(run.readiness, malformed)
    except (EvaluationArtifactError, TypeError, ValueError, UnicodeError):
        names.append("malformed_log")
    else:
        raise R7GE2EError("malformed log negative control was accepted")

    def held(sample: MotionSampleRecord) -> MotionSampleRecord:
        return replace(
            sample,
            motion_status="held",
            motion_rejection_reason="negative-control:held",
        )

    def rejected(sample: MotionSampleRecord) -> MotionSampleRecord:
        return replace(
            sample,
            motion_status="held",
            motion_rejection_reason="negative-control:rejected",
            target_rejected=True,
            target_rejection_reason="negative-control:rejected",
        )

    def stale(sample: MotionSampleRecord) -> MotionSampleRecord:
        return replace(
            sample,
            source_active=False,
            stale_reason="negative-control:stale",
            motion_status="held",
            motion_rejection_reason="negative-control:stale",
        )

    for name, mutator in (("held", held), ("rejected", rejected), ("stale", stale)):
        _assert_artifact_rejected(
            run,
            _mutated_world_records(run, mutator),
            name,
        )
        names.append(name)

    def unavailable(sample: MotionSampleRecord) -> MotionSampleRecord:
        return replace(
            sample,
            measured_tip_position_before_m=None,
            measured_tip_position_after_m=None,
            actual_tip_delta_m=None,
            endpoint_progress_status="measurement_unavailable",
            endpoint_progress_signed_m=None,
            endpoint_progress_ratio=None,
            endpoint_progress_direction_cosine=None,
            endpoint_progress_requested_norm_m=None,
            endpoint_progress_measured_norm_m=None,
            endpoint_progress_measurement_available=False,
            measurement_unavailable_reason="negative-control:measurement-unavailable",
        )

    _assert_artifact_rejected(
        run,
        _mutated_world_records(run, unavailable),
        "measurement_unavailable",
    )
    names.append("measurement_unavailable")

    _, _, _, outcome = _condition_records(run.records, "world")
    technical_outcome = replace(
        _failed_outcome(outcome, "negative-control:technical-invalid"),
        completion_status="technical_invalid",
        failure_attribution="technical",
    )
    technical_records = tuple(
        technical_outcome if item is outcome else item for item in run.records
    )
    _assert_not_successful_artifact(run, technical_records, "technical_invalid")
    names.append("technical_invalid")

    configuration, _, _, _ = _condition_records(run.records, "world")
    tampered_configuration = replace(
        configuration,
        software_revision=_derived_negative_revision_identity(
            configuration.software_revision,
        ),
    )
    tampered_records = tuple(
        tampered_configuration if item is configuration else item
        for item in run.records
    )
    try:
        build_world_tool_evaluation_artifacts_from_jsonl(run.readiness, prepare_motion_log(tampered_records).bytes_value)
    except (EvaluationArtifactError, TypeError, ValueError):
        names.append("artifact_identity_mismatch")
    else:
        raise R7GE2EError("artifact identity mismatch negative control was accepted")

    return tuple(names)


def _write_outputs(run: R7GE2ERun, output_dir: Path) -> None:
    if not output_dir.is_absolute():
        raise R7GE2EError("E2E output directory must be an absolute path")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise R7GE2EError("E2E output directory is not a directory")
    log_path = output_dir / R7_G_E2E_MOTION_LOG_NAME
    prepared_log = write_motion_log_atomic(log_path, run.records)
    if prepared_log.bytes_value != run.motion_log_bytes or log_path.read_bytes() != run.motion_log_bytes:
        raise R7GE2EError("canonical motion log output read-back mismatch")
    artifact_names = {
        "world": R7_G_E2E_WORLD_ARTIFACT_NAME,
        "tool": R7_G_E2E_TOOL_ARTIFACT_NAME,
    }
    for condition in run.conditions:
        path = output_dir / artifact_names[condition.condition_id]
        written = write_evaluation_artifact_atomic(path, condition.artifact)
        if written != condition.artifact_bytes or path.read_bytes() != condition.artifact_bytes:
            raise R7GE2EError(
                f"{condition.condition_id} canonical artifact output read-back mismatch"
            )
        if encode_evaluation_artifact(decode_evaluation_artifact(path.read_bytes())) != condition.artifact_bytes:
            raise R7GE2EError(
                f"{condition.condition_id} canonical artifact strict round-trip mismatch"
            )


def _summary(result: R7GE2EResult) -> dict[str, object]:
    world_readiness = result.run.readiness.world
    tool_readiness = result.run.readiness.tool
    return {
        "manifest_software_revision": world_readiness.manifest.software_revision_identity,
        "execution_repository_identity": world_readiness.software_execution_identity.repository_identity,
        "execution_software_revision": world_readiness.software_execution_identity.software_revision_identity,
        "manifest_pair_identity": result.run.readiness.pair_identity,
        "world_manifest_digest": world_readiness.manifest_digest,
        "tool_manifest_digest": tool_readiness.manifest_digest,
        "motion_log": {
            "name": R7_G_E2E_MOTION_LOG_NAME,
            "bytes": len(result.run.motion_log_bytes),
            "sha256": sha256(result.run.motion_log_bytes).hexdigest(),
        },
        "conditions": [
            {
                "condition": item.condition_id,
                "classification": item.execution.classification.value,
                "step_count": item.execution.step_count,
                "simulation_time_s": item.execution.final_elapsed_time_s,
                "evidence_sha256": sha256(
                    json.dumps(
                        _evidence_signature(item.evidence),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
                "metrics": [
                    {
                        "evaluator": evaluator,
                        "status": status,
                        "value": _normalise(value),
                    }
                    for evaluator, status, value in item.metric_results
                ],
                "artifact": {
                    "name": (
                        R7_G_E2E_WORLD_ARTIFACT_NAME
                        if item.condition_id == "world"
                        else R7_G_E2E_TOOL_ARTIFACT_NAME
                    ),
                    "bytes": len(item.artifact_bytes),
                    "sha256": sha256(item.artifact_bytes).hexdigest(),
                },
            }
            for item in result.run.conditions
        ],
        "negative_controls": list(result.negative_controls),
        "outputs": list(result.output_names),
    }


def run_r7_g_deterministic_e2e(
    *,
    manifest_software_revision_identity: str,
    execution_identity: SoftwareExecutionIdentity,
    output_dir: str | Path | None = None,
) -> R7GE2EResult:
    """独立したmanifest/actual revision入力でcanonical E2Eを2回実行する。"""

    first = _build_run(
        manifest_software_revision_identity=manifest_software_revision_identity,
        execution_identity=execution_identity,
    )
    second = _build_run(
        manifest_software_revision_identity=manifest_software_revision_identity,
        execution_identity=execution_identity,
    )
    _assert_deterministic(first, second)
    negative_controls = _run_negative_controls(first)
    output_names = (
        R7_G_E2E_MOTION_LOG_NAME,
        R7_G_E2E_WORLD_ARTIFACT_NAME,
        R7_G_E2E_TOOL_ARTIFACT_NAME,
    )
    result = R7GE2EResult(first, negative_controls, output_names)
    if output_dir is not None:
        _write_outputs(first, Path(output_dir))
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xpotato-sim-r7-g-e2e",
        description="Run the finite deterministic R7-G software-only E2E.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="absolute directory for canonical JSONL/JSON outputs",
    )
    parser.add_argument(
        "--manifest-software-revision",
        required=True,
        help="manifest-declared git-sha1/git-sha256 or explicit test-revision identity",
    )
    parser.add_argument(
        "--execution-software-revision",
        required=True,
        help="caller-observed actual execution revision; never inferred from the manifest",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run_r7_g_deterministic_e2e(
            manifest_software_revision_identity=args.manifest_software_revision,
            execution_identity=SoftwareExecutionIdentity(
                repository_identity=R7_G_E2E_REPOSITORY_IDENTITY,
                software_revision_identity=args.execution_software_revision,
            ),
            output_dir=args.output_dir,
        )
    except (OSError, R7GE2EError, RuntimeError, TypeError, ValueError) as exc:
        print(f"xpotato-sim-r7-g-e2e: error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(_summary(result), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the installable script
    raise SystemExit(main())


__all__ = [
    "R7GE2EConditionResult",
    "R7GE2EError",
    "R7GE2EResult",
    "R7GE2ERun",
    "R7_G_E2E_EXPERIMENT_ID",
    "R7_G_E2E_MOTION_LOG_NAME",
    "R7_G_E2E_REPOSITORY_IDENTITY",
    "R7_G_E2E_SESSION_ID",
    "R7_G_E2E_TOOL_ARTIFACT_NAME",
    "R7_G_E2E_WORLD_ARTIFACT_NAME",
    "main",
    "run_r7_g_deterministic_e2e",
]
