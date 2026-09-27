from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from math import sqrt
import os
from pathlib import Path
import stat
import subprocess
import sys
from threading import Event, Thread

import pytest

import xpotato_sim.runtime.evaluation.artifact as artifact_module
from xpotato_sim.runtime.composition.production_experiment import (
    PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
)
from xpotato_sim.runtime.evaluation.artifact import (
    EvaluationArtifactError,
    build_evaluation_artifact,
    build_world_tool_evaluation_artifacts,
    decode_evaluation_artifact,
    encode_evaluation_artifact,
    prepare_evaluation_artifact,
    reconstruct_task_evidence_from_motion_log,
    write_evaluation_artifact_atomic,
)
from xpotato_sim.runtime.evaluation.manifest import (
    SoftwareExecutionIdentity,
    build_evaluation_condition_pair_readiness,
    comparison_parameters_for_readiness,
)
from xpotato_sim.runtime.evaluation.r7_g_free_space import (
    build_r7_g_free_space_manifest_pair,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    ENDPOINT_REACH_TERMINAL_EVIDENCE,
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
    decode_endpoint_reach_terminal_evidence,
    decode_endpoint_reach_trajectory_evidence,
)
from xpotato_sim.runtime.experiment.contracts import EvidenceStatus
from xpotato_sim.runtime.experiment.motion_log_recorder import (
    TrialProtocolContext,
    WorldToolTrialProtocolContext,
    build_world_tool_motion_log_records,
)
from xpotato_sim.runtime.experiment.world_tool_runner import (
    run_evaluation_condition_pair,
)
from xpotato_sim.schemas.experiment_log import (
    ConfigurationRecord,
    MotionSampleRecord,
    TrialOutcomeRecord,
    TrialStartRecord,
    encode_jsonl,
)


REVISION = "test-revision:issue-408-artifact"
EXECUTION_IDENTITY = SoftwareExecutionIdentity(
    repository_identity="Xpotato1024/Xpotato-Sim",
    software_revision_identity=REVISION,
)


def _canonical_records(*, runtime_offset_s: float = 0.0):
    pair = build_r7_g_free_space_manifest_pair(
        software_revision_identity=REVISION,
    )
    readiness = build_evaluation_condition_pair_readiness(
        pair,
        PRODUCTION_EXPERIMENT_PLUGIN_REGISTRIES,
        execution_identity=EXECUTION_IDENTITY,
    )
    execution = run_evaluation_condition_pair(readiness)
    common = dict(
        experiment_id="experiment-408",
        session_id="session-408",
        participant_id="opaque-participant-408",
        block_id="block-0",
        task_family="endpoint-reach",
        practice=False,
        target_direction="positive-y",
        repetition_index=0,
        attempt_index=0,
        retry_of_trial_id=None,
    )
    contexts = WorldToolTrialProtocolContext(
        world=TrialProtocolContext(**common, direction_order=0),
        tool=TrialProtocolContext(**common, direction_order=1),
    )
    records = build_world_tool_motion_log_records(readiness, execution, contexts)
    if runtime_offset_s:
        records = tuple(
            replace(item, runtime_timestamp_s=item.runtime_timestamp_s + runtime_offset_s)
            if isinstance(item, (TrialStartRecord, MotionSampleRecord, TrialOutcomeRecord))
            else item
            for item in records
        )
    return readiness, execution, records


def _trial_records(records, condition: str):
    start = next(
        item
        for item in records
        if isinstance(item, TrialStartRecord)
        and item.control_condition == condition
    )
    configuration = next(
        item
        for item in records
        if isinstance(item, ConfigurationRecord)
        and item.configuration_id == start.configuration_id
    )
    samples = tuple(
        item
        for item in records
        if isinstance(item, MotionSampleRecord) and item.trial_id == start.trial_id
    )
    outcome = next(
        item
        for item in records
        if isinstance(item, TrialOutcomeRecord) and item.trial_id == start.trial_id
    )
    return configuration, start, samples, outcome


def _unit_quaternion_with_component_delta(
    expected: tuple[float, float, float, float],
    delta: float,
) -> tuple[float, float, float, float]:
    index = min(range(len(expected)), key=lambda item: abs(expected[item]))
    values = list(expected)
    values[index] += delta
    norm = sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values)  # type: ignore[return-value]


def _shift_world_qpos_measurements(
    records,
    configuration: ConfigurationRecord,
    delta: float,
):
    shifted_qpos = lambda values: tuple(value + delta for value in values)
    return tuple(
        replace(
            item,
            initial_qpos_rad=shifted_qpos(item.initial_qpos_rad),
        )
        if isinstance(item, ConfigurationRecord)
        and item.configuration_id == configuration.configuration_id
        else replace(
            item,
            qpos_before_rad=shifted_qpos(item.qpos_before_rad),
            qpos_after_rad=shifted_qpos(item.qpos_after_rad),
            candidate_qpos_rad=(
                None
                if item.candidate_qpos_rad is None
                else shifted_qpos(item.candidate_qpos_rad)
            ),
        )
        if isinstance(item, MotionSampleRecord)
        and item.configuration_id == configuration.configuration_id
        else item
        for item in records
    )


def _shift_world_orientation_measurement(
    records,
    configuration: ConfigurationRecord,
    delta: float,
):
    expected = configuration.initial_tool_orientation_wxyz
    shifted = tuple(
        -value for value in _unit_quaternion_with_component_delta(expected, delta)
    )
    return tuple(
        replace(item, initial_tool_orientation_wxyz=shifted)
        if isinstance(item, ConfigurationRecord)
        and item.configuration_id == configuration.configuration_id
        else item
        for item in records
    )


def test_canonical_world_tool_artifacts_are_deterministic_and_measured_only() -> None:
    readiness, execution, records = _canonical_records()
    artifacts = build_world_tool_evaluation_artifacts(readiness, records)
    assert tuple(item.condition_id for item in artifacts) == ("world", "tool")
    world, tool = artifacts
    assert [item.evaluator.canonical_id for item in world.trials[0].metrics] == [
        "success_within_timeout/v1",
        "off_axis_drift/v1",
        "completion_time/v1",
        "final_endpoint_error/v1",
    ]
    assert world.trials[0].metrics[0].value is True
    assert world.trials[0].metrics[0].status.value == "measured"
    assert tool.trials[0].metrics[0].value is False
    assert tool.trials[0].metrics[2].value is None
    assert tool.trials[0].metrics[2].status.value == "unavailable"
    assert tool.trials[0].metrics[3].value is not None
    assert world.freeze_identity == readiness.world.freeze_identity
    assert tool.freeze_identity == readiness.tool.freeze_identity
    assert execution.world.step_count == 57
    assert execution.tool.step_count == 250
    assert prepare_evaluation_artifact(world) == encode_evaluation_artifact(world)
    assert decode_evaluation_artifact(encode_evaluation_artifact(world)) == world


def test_reconstructed_task_evidence_is_semantically_equivalent_to_runner_evidence() -> None:
    readiness, execution, records = _canonical_records()
    configuration, start, samples, outcome = _trial_records(records, "world")
    reconstructed = reconstruct_task_evidence_from_motion_log(
        configuration,
        start,
        samples,
        outcome,
    )
    expected = execution.world.transition.evidence
    assert decode_endpoint_reach_terminal_evidence(reconstructed) == decode_endpoint_reach_terminal_evidence(expected)
    assert decode_endpoint_reach_trajectory_evidence(reconstructed) == decode_endpoint_reach_trajectory_evidence(expected)


def test_parallel_shifted_trial_rebases_terminal_and_trajectory_elapsed_time() -> None:
    readiness, execution, records = _canonical_records(runtime_offset_s=37.5)
    configuration, start, samples, outcome = _trial_records(records, "world")
    evidence = reconstruct_task_evidence_from_motion_log(
        configuration,
        start,
        samples,
        outcome,
    )
    terminal = decode_endpoint_reach_terminal_evidence(evidence)
    trajectory = decode_endpoint_reach_trajectory_evidence(evidence)
    duration = outcome.runtime_timestamp_s - start.runtime_timestamp_s
    assert terminal.elapsed_time_s == pytest.approx(duration)
    assert terminal.elapsed_time_s == pytest.approx(execution.world.final_elapsed_time_s)
    assert trajectory.samples[0].elapsed_time_s == 0.0
    assert trajectory.samples[-1].elapsed_time_s == pytest.approx(duration)
    artifact = build_evaluation_artifact(readiness.world, records)
    assert artifact.trials[0].metrics[2].value == pytest.approx(duration)


@pytest.mark.parametrize(
    ("measurement", "delta", "accepted"),
    (
        ("qpos", 5e-10, True),
        ("qpos", 2e-9, False),
        ("orientation", 5e-10, True),
        ("orientation", 2e-9, False),
    ),
)
def test_artifact_runtime_initial_measurements_use_bounded_tolerance(
    measurement: str,
    delta: float,
    accepted: bool,
) -> None:
    readiness, _, records = _canonical_records()
    configuration, _, _, _ = _trial_records(records, "world")
    if measurement == "qpos":
        changed_records = _shift_world_qpos_measurements(
            records,
            configuration,
            delta,
        )
        expected_error = "initial qpos"
    else:
        changed_records = _shift_world_orientation_measurement(
            records,
            configuration,
            delta,
        )
        expected_error = "initial tool orientation"

    if accepted:
        artifact = build_evaluation_artifact(readiness.world, changed_records)
        assert artifact.condition_id == "world"
    else:
        with pytest.raises(EvaluationArtifactError, match=expected_error):
            build_evaluation_artifact(readiness.world, changed_records)


def test_artifact_static_target_and_scalar_comparisons_remain_one_e12_strict() -> None:
    readiness, _, records = _canonical_records()
    configuration, _, _, _ = _trial_records(records, "world")

    changed_target = replace(
        configuration,
        target_world_position_m=(
            configuration.target_world_position_m[0] + 2e-12,
            *configuration.target_world_position_m[1:],
        ),
    )
    changed_target_records = tuple(
        changed_target if item is configuration else item for item in records
    )
    with pytest.raises(EvaluationArtifactError, match="target position"):
        build_evaluation_artifact(readiness.world, changed_target_records)

    changed_scalar = replace(
        configuration,
        target_tolerance_m=configuration.target_tolerance_m + 2e-12,
    )
    changed_scalar_records = tuple(
        changed_scalar if item is configuration else item for item in records
    )
    with pytest.raises(EvaluationArtifactError, match="target_tolerance_m"):
        build_evaluation_artifact(readiness.world, changed_scalar_records)


def test_failed_and_technical_invalid_trials_keep_unavailable_policy() -> None:
    readiness, _, records = _canonical_records()
    configuration, start, _, outcome = _trial_records(records, "world")

    failed = replace(
        outcome,
        runtime_timestamp_s=start.runtime_timestamp_s,
        completion_status="failed",
        success_within_timeout=False,
        final_measured_endpoint_error_m=None,
        failure_attribution="operator",
        outcome_reason="bounded failure",
        primary_outcome_sample_index=None,
    )
    failed_artifact = build_evaluation_artifact(
        readiness.world,
        (configuration, start, failed),
    )
    failed_metrics = {
        item.evaluator.canonical_id: item for item in failed_artifact.trials[0].metrics
    }
    assert failed_metrics["success_within_timeout/v1"].value is False
    assert failed_metrics["success_within_timeout/v1"].status.value == "measured"
    assert failed_metrics["completion_time/v1"].status.value == "unavailable"
    assert failed_metrics["off_axis_drift/v1"].status.value == "unavailable"
    assert failed_metrics["final_endpoint_error/v1"].status.value == "unavailable"
    assert all(item.value is None for item in failed_metrics.values() if item.status.value != "measured")

    technical_invalid = replace(
        failed,
        completion_status="technical_invalid",
        failure_attribution="technical",
        outcome_reason="source read failed",
    )
    technical_artifact = build_evaluation_artifact(
        readiness.world,
        (configuration, start, technical_invalid),
    )
    technical_metrics = {
        item.evaluator.canonical_id: item for item in technical_artifact.trials[0].metrics
    }
    assert {item.status.value for item in technical_metrics.values()} == {"invalid"}
    assert all(item.value is None for item in technical_metrics.values())


def test_technical_invalid_after_measured_samples_invalidates_trajectory_and_all_metrics() -> None:
    readiness, _, records = _canonical_records()
    configuration, start, samples, outcome = _trial_records(records, "world")
    technical_invalid = replace(
        outcome,
        completion_status="technical_invalid",
        success_within_timeout=False,
        final_measured_endpoint_error_m=None,
        failure_attribution="technical",
        outcome_reason="late technical invalidation",
        primary_outcome_sample_index=None,
    )
    evidence = reconstruct_task_evidence_from_motion_log(
        configuration,
        start,
        samples,
        technical_invalid,
    )
    assert evidence.require(ENDPOINT_REACH_TERMINAL_EVIDENCE).status is EvidenceStatus.MEASURED
    trajectory = evidence.require(ENDPOINT_REACH_TRAJECTORY_EVIDENCE)
    assert trajectory.status is EvidenceStatus.INVALID
    assert trajectory.value is None
    assert trajectory.reason == "late technical invalidation"
    changed_records = tuple(
        technical_invalid if item is outcome else item
        for item in records
    )
    artifact = build_evaluation_artifact(readiness.world, changed_records)
    assert {item.status for item in artifact.trials[0].metrics} == {EvidenceStatus.INVALID}
    assert all(item.value is None for item in artifact.trials[0].metrics)


def test_artifact_rejects_identity_or_schema_tampering_and_preserves_atomic_target(
    tmp_path: Path,
) -> None:
    readiness, _, records = _canonical_records()
    world = build_world_tool_evaluation_artifacts(readiness, records)[0]
    document = world.to_document()
    document["unexpected"] = True
    with pytest.raises(EvaluationArtifactError, match="unknown fields"):
        decode_evaluation_artifact(document)

    unknown_evaluator = world.to_document()
    unknown_evaluator["evaluators"][0]["name"] = "unknown-production-evaluator"
    with pytest.raises(EvaluationArtifactError, match="unknown production evaluator"):
        decode_evaluation_artifact(unknown_evaluator)

    for field, value in (
        ("unit", "tampered-unit"),
        ("frame", "tampered-frame"),
        ("provenance", "tampered-provenance"),
    ):
        changed_metric = world.to_document()
        changed_metric["trials"][0]["metrics"][0][field] = value
        with pytest.raises(EvaluationArtifactError, match="does not match production evaluator"):
            decode_evaluation_artifact(changed_metric)

    changed_reason = world.to_document()
    changed_reason["trials"][0]["metrics"][0]["reason"] = "tampered measured reason"
    with pytest.raises(EvaluationArtifactError, match="must not carry a reason"):
        decode_evaluation_artifact(changed_reason)

    for completion_status, terminal_classification, attribution in (
        ("failed", "failure", "technical"),
        ("technical_invalid", "technical_invalid", "operator"),
    ):
        changed_terminal = world.to_document()
        trial = changed_terminal["trials"][0]
        trial["completion_status"] = completion_status
        trial["terminal_classification"] = terminal_classification
        trial["failure_attribution"] = attribution
        trial["outcome_reason"] = "tampered terminal mapping"
        with pytest.raises(EvaluationArtifactError, match="failure attribution"):
            decode_evaluation_artifact(changed_terminal)

    configuration, _, _, _ = _trial_records(records, "world")
    changed = replace(configuration, software_revision="test-revision:tampered")
    changed_records = tuple(changed if item is configuration else item for item in records)
    with pytest.raises(EvaluationArtifactError, match="software_revision"):
        build_world_tool_evaluation_artifacts(readiness, changed_records)

    missing_identity = replace(
        configuration,
        comparison_parameters=tuple(
            item
            for item in configuration.comparison_parameters
            if item[0] != "manifest_digest"
        ),
    )
    missing_records = tuple(
        missing_identity if item is configuration else item for item in records
    )
    with pytest.raises(EvaluationArtifactError, match="canonical readiness projection"):
        build_world_tool_evaluation_artifacts(readiness, missing_records)

    expected_parameters = dict(comparison_parameters_for_readiness(readiness.world))
    for key, expected in expected_parameters.items():
        tampered_parameters = dict(expected_parameters)
        if isinstance(expected, str):
            tampered_parameters[key] = expected + ":tampered"
        elif isinstance(expected, bool):
            tampered_parameters[key] = not expected
        else:
            tampered_parameters[key] = expected + 1
        changed_comparison = replace(
            configuration,
            comparison_parameters=tuple(tampered_parameters.items()),
        )
        changed_records = tuple(
            changed_comparison if item is configuration else item
            for item in records
        )
        with pytest.raises(EvaluationArtifactError, match="comparison parameter"):
            build_world_tool_evaluation_artifacts(readiness, changed_records)

    target = tmp_path / "evaluation.json"
    previous = b"previous-valid-artifact"
    target.write_bytes(previous)
    written = write_evaluation_artifact_atomic(target, world)
    assert target.read_bytes() == written
    assert decode_evaluation_artifact(written) == world


def test_atomic_writer_uses_persistent_sidecar_without_owner_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness, _, records = _canonical_records()
    world = build_world_tool_evaluation_artifacts(readiness, records)[0]
    target = tmp_path / "evaluation.json"
    lock = target.with_name(f".{target.name}.lock")
    sidecar_bytes = b'{"pid":1'
    lock.write_bytes(sidecar_bytes)
    inode_before = lock.stat().st_ino

    def forbidden_process_probe(*_: object) -> None:
        raise AssertionError("artifact lock must not probe or signal a process")

    monkeypatch.setattr(artifact_module.os, "kill", forbidden_process_probe)
    write_evaluation_artifact_atomic(target, world)
    assert target.exists()
    assert lock.read_bytes() == sidecar_bytes
    assert lock.stat().st_ino == inode_before


def test_atomic_writer_keeps_empty_sidecar_empty_and_persistent(
    tmp_path: Path,
) -> None:
    readiness, _, records = _canonical_records()
    world = build_world_tool_evaluation_artifacts(readiness, records)[0]
    target = tmp_path / "evaluation.json"
    lock = target.with_name(f".{target.name}.lock")
    lock.write_bytes(b"")
    existing_inode = lock.stat().st_ino
    write_evaluation_artifact_atomic(target, world)
    assert lock.read_bytes() == b""
    assert lock.stat().st_ino == existing_inode

    new_target = tmp_path / "new-evaluation.json"
    new_lock = new_target.with_name(f".{new_target.name}.lock")
    assert not new_lock.exists()
    write_evaluation_artifact_atomic(new_target, world)
    assert new_lock.exists()
    assert new_lock.read_bytes() == b""
    if os.name != "nt":
        assert stat.S_IMODE(new_lock.stat().st_mode) == 0o600


def test_atomic_writer_serializes_cooperative_writers_and_keeps_sidecar_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness, _, records = _canonical_records()
    world = build_world_tool_evaluation_artifacts(readiness, records)[0]
    target = tmp_path / "evaluation.json"
    target.write_bytes(b"previous")
    lock = target.with_name(f".{target.name}.lock")
    first_read_started = Event()
    release_first_read = Event()
    original_read = artifact_module._read_bytes
    blocked = False

    def blocking_read(path: Path) -> bytes:
        nonlocal blocked
        if path == target and not blocked:
            blocked = True
            first_read_started.set()
            assert release_first_read.wait(timeout=5.0)
        return original_read(path)

    monkeypatch.setattr(artifact_module, "_read_bytes", blocking_read)
    first_result: list[object] = []

    def first_writer() -> None:
        try:
            first_result.append(write_evaluation_artifact_atomic(target, world))
        except BaseException as exc:  # pragma: no cover - surfaced below
            first_result.append(exc)

    worker = Thread(target=first_writer)
    worker.start()
    assert first_read_started.wait(timeout=5.0)
    with pytest.raises(EvaluationArtifactError, match="target lock"):
        write_evaluation_artifact_atomic(target, world)
    release_first_read.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert len(first_result) == 1
    assert isinstance(first_result[0], bytes)
    assert lock.exists()

    previous = target.read_bytes()
    monkeypatch.setattr(
        artifact_module.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failure")),
    )
    with pytest.raises(EvaluationArtifactError, match="replace failure"):
        write_evaluation_artifact_atomic(target, world)
    assert target.read_bytes() == previous
    assert lock.exists()
    assert not artifact_module._PROCESS_PATH_LOCKS


def test_atomic_writer_excludes_concurrent_process_and_recovers_after_crash(
    tmp_path: Path,
) -> None:
    target = tmp_path / "evaluation.json"
    script = """
import os
from pathlib import Path
import sys
import time

from xpotato_sim.runtime.evaluation.artifact import _exclusive_target_lock

target = Path(sys.argv[1])
with _exclusive_target_lock(target):
    print("ready", flush=True)
    time.sleep(0.5)
    os._exit(0)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(EvaluationArtifactError, match="target lock"):
            with artifact_module._exclusive_target_lock(target):
                pass
        assert process.wait(timeout=5.0) == 0
        with artifact_module._exclusive_target_lock(target):
            pass
        assert target.with_name(f".{target.name}.lock").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def test_canonical_artifact_bytes_have_independent_reproducible_hashes() -> None:
    readiness, _, records = _canonical_records()
    artifacts = build_world_tool_evaluation_artifacts(readiness, records)
    source_digest = sha256(encode_jsonl(records).encode("utf-8")).hexdigest()
    expected_source_identity = f"sha256:{source_digest}"
    assert all(
        item.source_log_identity == expected_source_identity
        and item.source_log_sha256 == expected_source_identity
        for item in artifacts
    )
    encoded = tuple(prepare_evaluation_artifact(item) for item in artifacts)
    assert tuple(sha256(item).hexdigest() for item in encoded) == tuple(
        sha256(encode_evaluation_artifact(item)).hexdigest() for item in artifacts
    )
    assert tuple(
        encode_evaluation_artifact(decode_evaluation_artifact(item))
        for item in encoded
    ) == encoded
    rebuilt = build_world_tool_evaluation_artifacts(readiness, records)
    assert tuple(prepare_evaluation_artifact(item) for item in rebuilt) == encoded
