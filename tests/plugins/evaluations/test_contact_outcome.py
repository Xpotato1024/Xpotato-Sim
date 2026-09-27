from __future__ import annotations

from dataclasses import replace

import pytest

from xpotato_sim.plugins.evaluations.contact_outcome import CONTACT_OUTCOME_PLUGIN
from xpotato_sim.runtime.contact.task_contract import (
    CONTACT_TASK_OUTCOME_EVIDENCE,
    CONTACT_TASK_OUTCOME_PROVENANCE,
    CONTACT_TASK_TERMINAL_EVIDENCE,
    CONTACT_TASK_TERMINAL_PROVENANCE,
    ContactTaskOutcome,
    ContactTaskPhase,
    ContactTrialIdentity,
)
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidence,
    CanonicalEvidenceSet,
    EvidenceStatus,
    TaskTerminalClassification,
)


_DIGEST = "sha256:" + "a" * 64


def _outcome(
    classification: TaskTerminalClassification,
) -> ContactTaskOutcome:
    phase = {
        TaskTerminalClassification.RUNNING: ContactTaskPhase.APPROACH,
        TaskTerminalClassification.SUCCESS: ContactTaskPhase.SUCCESS,
        TaskTerminalClassification.FAILURE: ContactTaskPhase.FAILURE,
        TaskTerminalClassification.TECHNICAL_INVALID: ContactTaskPhase.TECHNICAL_INVALID,
    }[classification]
    terminal_time = None if classification is TaskTerminalClassification.RUNNING else 0.4
    return ContactTaskOutcome(
        manifest_digest=_DIGEST,
        trial=ContactTrialIdentity("evaluation-trial"),
        dwell_interval_s=0.2,
        timeout_s=1.0,
        target_penetration_band_m=(0.0, 0.002),
        target_normal_force_band_n=None,
        approach_alignment_min_cosine=None,
        normal_alignment_min_cosine=None,
        max_contact_location_drift_m=None,
        require_pose_measurement=False,
        phase=phase,
        classification=classification,
        reason=None if classification in {
            TaskTerminalClassification.RUNNING,
            TaskTerminalClassification.SUCCESS,
        } else "fixture terminal classification",
        terminal_time_s=terminal_time,
        completion_time_s=(
            0.4 if classification is TaskTerminalClassification.SUCCESS else None
        ),
        first_contact_time_s=None,
        peak_normal_force_n=None,
        max_penetration_m=None,
        overshoot_m=None,
        steady_state_error_m=None,
        force_variability_n=None,
        peak_tangential_force_n=None,
        slip_proxy_m=None,
        contact_loss_count=0,
        recontact_count=0,
        final_tip_position_world_m=None,
        final_object_position_world_m=None,
        final_object_orientation_wxyz=None,
        final_contact_location_world_m=None,
        contact_location_drift_m=None,
        final_normal_alignment_cosine=None,
        observations_count=0,
    )


def _evidence(
    classification: TaskTerminalClassification,
    *,
    outcome_status: EvidenceStatus = EvidenceStatus.MEASURED,
) -> CanonicalEvidenceSet:
    outcome = _outcome(classification)
    terminal = {
        "classification": classification.value,
        "completion_time_s": outcome.completion_time_s,
        "manifest_digest": _DIGEST,
        "phase": outcome.phase.value,
        "reason": outcome.reason,
        "terminal_time_s": outcome.terminal_time_s,
        "trial": outcome.trial.to_document(),
    }
    return CanonicalEvidenceSet(
        (
            CanonicalEvidence(
                identity=CONTACT_TASK_TERMINAL_EVIDENCE,
                status=EvidenceStatus.MEASURED,
                value=terminal,
                provenance=CONTACT_TASK_TERMINAL_PROVENANCE,
            ),
            CanonicalEvidence(
                identity=CONTACT_TASK_OUTCOME_EVIDENCE,
                status=outcome_status,
                value=(None if outcome_status is not EvidenceStatus.MEASURED else outcome.to_document()),
                provenance=CONTACT_TASK_OUTCOME_PROVENANCE,
                reason=("fixture invalid" if outcome_status is not EvidenceStatus.MEASURED else None),
            ),
        )
    )


def test_contact_outcome_projection_preserves_terminal_artifact() -> None:
    result = CONTACT_OUTCOME_PLUGIN.derive_metric(
        _evidence(TaskTerminalClassification.SUCCESS), {}
    )
    assert result.status is EvidenceStatus.MEASURED
    assert result.value["classification"] == "success"  # type: ignore[index]
    assert result.value["completion_time_s"] == pytest.approx(0.4)  # type: ignore[index]


def test_contact_outcome_failure_has_no_completion_metric() -> None:
    result = CONTACT_OUTCOME_PLUGIN.derive_metric(
        _evidence(TaskTerminalClassification.FAILURE), {}
    )
    assert result.status is EvidenceStatus.MEASURED
    assert result.value["completion_time_s"] is None  # type: ignore[index]


def test_contact_outcome_running_is_unavailable() -> None:
    result = CONTACT_OUTCOME_PLUGIN.derive_metric(
        _evidence(TaskTerminalClassification.RUNNING), {}
    )
    assert result.value is None
    assert result.status is EvidenceStatus.UNAVAILABLE


def test_contact_outcome_invalid_evidence_never_becomes_success() -> None:
    result = CONTACT_OUTCOME_PLUGIN.derive_metric(
        _evidence(
            TaskTerminalClassification.TECHNICAL_INVALID,
            outcome_status=EvidenceStatus.INVALID,
        ),
        {},
    )
    assert result.value is None
    assert result.status is EvidenceStatus.INVALID


def test_contact_outcome_rejects_forged_outcome_producer() -> None:
    evidence = _evidence(TaskTerminalClassification.SUCCESS)
    original = evidence.require(CONTACT_TASK_OUTCOME_EVIDENCE)
    forged = CanonicalEvidence(
        identity=original.identity,
        status=original.status,
        value=original.value,
        provenance="runner:preclassified",
    )
    with pytest.raises(ValueError, match="producer"):
        CONTACT_OUTCOME_PLUGIN.derive_metric(
            CanonicalEvidenceSet(
                (
                    evidence.require(CONTACT_TASK_TERMINAL_EVIDENCE),
                    forged,
                )
            ),
            {},
        )


def test_contact_outcome_contract_rejects_malformed_digest_and_missing_failure_reason() -> None:
    with pytest.raises(ValueError, match="digest"):
        replace(_outcome(TaskTerminalClassification.SUCCESS), manifest_digest=object())
    with pytest.raises(ValueError, match="requires a reason"):
        replace(_outcome(TaskTerminalClassification.FAILURE), reason=None)


def test_outcome_context_conditions_are_canonical_and_reconstructable() -> None:
    outcome = _outcome(TaskTerminalClassification.SUCCESS)
    decoded = ContactTaskOutcome.from_document(outcome.to_document())

    assert decoded == outcome
    configured = replace(
        outcome,
        target_penetration_band_m=(0.001, 0.002),
        target_normal_force_band_n=(1.0, 2.0),
        approach_alignment_min_cosine=0.5,
        normal_alignment_min_cosine=0.6,
        max_contact_location_drift_m=0.01,
        require_pose_measurement=True,
    )
    assert ContactTaskOutcome.from_document(configured.to_document()) == configured
    changed = replace(outcome, dwell_interval_s=0.3)
    assert changed.canonical_bytes() != outcome.canonical_bytes()
