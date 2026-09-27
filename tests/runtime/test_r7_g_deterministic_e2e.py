from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import xpotato_sim.runtime.experiment.r7_g_e2e as e2e
from xpotato_sim.runtime.evaluation.manifest import SoftwareExecutionIdentity
from xpotato_sim.runtime.experiment.r7_g_e2e import (
    R7_G_E2E_MOTION_LOG_NAME,
    R7_G_E2E_REPOSITORY_IDENTITY,
    R7_G_E2E_TOOL_ARTIFACT_NAME,
    R7_G_E2E_WORLD_ARTIFACT_NAME,
    R7GE2EError,
    _summary,
    main,
    run_r7_g_deterministic_e2e,
)


FIXTURE_REVISION = "test-revision:issue-409-fixture"
FIXTURE_EXECUTION_IDENTITY = SoftwareExecutionIdentity(
    repository_identity=R7_G_E2E_REPOSITORY_IDENTITY,
    software_revision_identity=FIXTURE_REVISION,
)


def _run_kwargs() -> dict[str, object]:
    return {
        "manifest_software_revision_identity": FIXTURE_REVISION,
        "execution_identity": FIXTURE_EXECUTION_IDENTITY,
    }


def test_r7_g_e2e_repeats_execution_evidence_metrics_and_artifacts(
    tmp_path: Path,
) -> None:
    result = run_r7_g_deterministic_e2e(**_run_kwargs(), output_dir=tmp_path)

    assert result.negative_controls == (
        "readiness_mismatch",
        "malformed_log",
        "held",
        "rejected",
        "stale",
        "measurement_unavailable",
        "technical_invalid",
        "artifact_identity_mismatch",
    )
    assert result.run.motion_log_bytes == (
        tmp_path / R7_G_E2E_MOTION_LOG_NAME
    ).read_bytes()
    assert [
        (item.condition_id, item.execution.classification.value, item.execution.step_count)
        for item in result.run.conditions
    ] == [("world", "success", 57), ("tool", "failure", 250)]
    assert result.run.condition("world").execution.final_elapsed_time_s == pytest.approx(1.14)
    assert result.run.condition("tool").execution.final_elapsed_time_s == pytest.approx(5.0)
    assert all(item.artifact_bytes for item in result.run.conditions)
    assert (
        tmp_path / R7_G_E2E_WORLD_ARTIFACT_NAME
    ).read_bytes() == result.run.condition("world").artifact_bytes
    assert (
        tmp_path / R7_G_E2E_TOOL_ARTIFACT_NAME
    ).read_bytes() == result.run.condition("tool").artifact_bytes
    assert (tmp_path / f".{R7_G_E2E_WORLD_ARTIFACT_NAME}.lock").exists()
    assert (tmp_path / f".{R7_G_E2E_TOOL_ARTIFACT_NAME}.lock").exists()


def test_r7_g_e2e_installable_entrypoint_reports_only_canonical_names(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(
        [
            "--output-dir",
            str(tmp_path),
            "--manifest-software-revision",
            FIXTURE_REVISION,
            "--execution-software-revision",
            FIXTURE_REVISION,
        ]
    ) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["outputs"] == [
        R7_G_E2E_MOTION_LOG_NAME,
        R7_G_E2E_WORLD_ARTIFACT_NAME,
        R7_G_E2E_TOOL_ARTIFACT_NAME,
    ]
    assert summary["manifest_software_revision"] == FIXTURE_REVISION
    assert summary["execution_software_revision"] == FIXTURE_REVISION
    assert "AppData" not in json.dumps(summary)
    assert [item["step_count"] for item in summary["conditions"]] == [57, 250]


def test_r7_g_e2e_requires_independent_revision_inputs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as missing:
        main(["--output-dir", str(tmp_path / "missing")])
    assert missing.value.code == 2
    with pytest.raises(TypeError):
        run_r7_g_deterministic_e2e()


def test_r7_g_e2e_revision_mismatch_fails_before_execution_or_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def execution_must_not_start(*args: object, **kwargs: object) -> None:
        raise AssertionError("execution started before readiness revision gate")

    monkeypatch.setattr(e2e, "run_evaluation_condition_pair", execution_must_not_start)
    mismatched = SoftwareExecutionIdentity(
        repository_identity=R7_G_E2E_REPOSITORY_IDENTITY,
        software_revision_identity="test-revision:issue-409-observed-mismatch",
    )
    with pytest.raises((RuntimeError, TypeError, ValueError)):
        run_r7_g_deterministic_e2e(
            manifest_software_revision_identity=FIXTURE_REVISION,
            execution_identity=mismatched,
            output_dir=tmp_path / "mismatch-output",
        )
    assert not (tmp_path / "mismatch-output").exists()


@pytest.mark.parametrize(
    "revision",
    (
        "test-revision:issue-409-tampered",
        "test-revision:caller-supplied-r7-g-negative-control",
    ),
)
def test_r7_g_e2e_derived_mismatch_control_handles_suffix_like_inputs(
    revision: str,
) -> None:
    result = run_r7_g_deterministic_e2e(
        manifest_software_revision_identity=revision,
        execution_identity=SoftwareExecutionIdentity(
            repository_identity=R7_G_E2E_REPOSITORY_IDENTITY,
            software_revision_identity=revision,
        ),
    )
    assert result.negative_controls == (
        "readiness_mismatch",
        "malformed_log",
        "held",
        "rejected",
        "stale",
        "measurement_unavailable",
        "technical_invalid",
        "artifact_identity_mismatch",
    )


@pytest.mark.parametrize(
    "revision",
    (
        "test-revision:caller-supplied-r7-g-negative-control",
        "git-sha1:" + "0" * 40,
        "git-sha256:" + "f" * 64,
    ),
)
def test_derived_negative_revision_is_valid_and_always_differs(
    revision: str,
) -> None:
    derived = e2e._derived_negative_revision_identity(revision)
    assert derived != revision
    SoftwareExecutionIdentity(
        repository_identity=R7_G_E2E_REPOSITORY_IDENTITY,
        software_revision_identity=derived,
    )


def test_sample_only_negative_controls_reject_and_noop_is_not_a_control() -> None:
    run = e2e._build_run(**_run_kwargs())
    _, _, _, original_outcome = e2e._condition_records(run.records, "world")

    def held(sample: e2e.MotionSampleRecord) -> e2e.MotionSampleRecord:
        return replace(sample, motion_status="held", motion_rejection_reason="test:held")

    def rejected(sample: e2e.MotionSampleRecord) -> e2e.MotionSampleRecord:
        return replace(
            sample,
            motion_status="held",
            motion_rejection_reason="test:rejected",
            target_rejected=True,
            target_rejection_reason="test:rejected",
        )

    def stale(sample: e2e.MotionSampleRecord) -> e2e.MotionSampleRecord:
        return replace(
            sample,
            source_active=False,
            stale_reason="test:stale",
            motion_status="held",
            motion_rejection_reason="test:stale",
        )

    def unavailable(sample: e2e.MotionSampleRecord) -> e2e.MotionSampleRecord:
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
            measurement_unavailable_reason="test:measurement-unavailable",
        )

    for name, mutator in (
        ("held", held),
        ("rejected", rejected),
        ("stale", stale),
        ("measurement_unavailable", unavailable),
    ):
        records = e2e._mutated_world_records(run, mutator)
        _, _, _, mutated_outcome = e2e._condition_records(records, "world")
        assert mutated_outcome == original_outcome
        e2e._assert_artifact_rejected(run, records, name)

    no_op_records = e2e._mutated_world_records(run, lambda sample: sample)
    with pytest.raises(R7GE2EError):
        e2e._assert_artifact_rejected(run, no_op_records, "no-op")


def test_r7_g_e2e_summary_is_stable_and_excludes_paths() -> None:
    first = run_r7_g_deterministic_e2e(**_run_kwargs())
    second = run_r7_g_deterministic_e2e(**_run_kwargs())
    assert _summary(first) == _summary(second)
    assert all(
        value not in json.dumps(_summary(first))
        for value in ("AppData", "worktree", "process", "uuid")
    )
