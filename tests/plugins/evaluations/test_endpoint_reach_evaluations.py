from __future__ import annotations

import pytest

from xpotato_sim.plugins.evaluations.completion_time import COMPLETION_TIME_PLUGIN
from xpotato_sim.plugins.evaluations.final_endpoint_error import (
    FINAL_ENDPOINT_ERROR_PLUGIN,
)
from xpotato_sim.plugins.evaluations.off_axis_drift import OFF_AXIS_DRIFT_PLUGIN
from xpotato_sim.plugins.evaluations.success_within_timeout import (
    SUCCESS_WITHIN_TIMEOUT_PLUGIN,
)
from xpotato_sim.plugins.evaluations._endpoint_reach_evidence import (
    ENDPOINT_REACH_TERMINAL_EVIDENCE,
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
)
from xpotato_sim.plugins.tasks.endpoint_reach_task import ENDPOINT_REACH_TASK_PLUGIN
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidence,
    CanonicalEvidenceSet,
    EvidenceStatus,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    ENDPOINT_REACH_TERMINAL_PROVENANCE,
    ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
    EndpointReachObservation,
    EndpointReachTaskContext,
)


def _task_evidence(*, success: bool = True) -> CanonicalEvidenceSet:
    binding = ENDPOINT_REACH_TASK_PLUGIN.bind_context(
        EndpointReachTaskContext(
            initial_position_world_m=(0.0, 0.0, 0.0),
            target_position_world_m=(1.0, 0.0, 0.0),
            target_tolerance_m=0.11,
            dwell_interval_s=0.2,
            timeout_s=2.0,
        ),
        {},
    )
    transition = binding.advance(
        binding.initial_state(),
        EndpointReachObservation(0.0, (0.0, 0.0, 0.0)),
    )
    transition = binding.advance(
        transition.state,
        EndpointReachObservation(0.5, (0.5, 0.2, 0.0)),
    )
    if success:
        transition = binding.advance(
            transition.state,
            EndpointReachObservation(1.2, (0.9, 0.0, 0.0)),
        )
        transition = binding.advance(
            transition.state,
            EndpointReachObservation(1.5, (0.9, 0.0, 0.0)),
        )
    else:
        transition = binding.advance(
            transition.state,
            EndpointReachObservation(2.0, (0.5, 0.2, 0.0)),
        )
    return transition.evidence


def test_primary_and_secondary_metrics_are_pure_and_typed() -> None:
    evidence = _task_evidence()
    success = SUCCESS_WITHIN_TIMEOUT_PLUGIN.derive_metric(evidence, {})
    drift = OFF_AXIS_DRIFT_PLUGIN.derive_metric(evidence, {})

    assert success.value is True
    assert success.status is EvidenceStatus.MEASURED
    assert SUCCESS_WITHIN_TIMEOUT_PLUGIN.unit == "boolean"
    assert SUCCESS_WITHIN_TIMEOUT_PLUGIN.frame is None
    assert drift.value == pytest.approx(0.2)
    assert drift.status is EvidenceStatus.MEASURED
    assert OFF_AXIS_DRIFT_PLUGIN.unit == "meter"
    assert OFF_AXIS_DRIFT_PLUGIN.frame == "MuJoCo world / scene frame"


def test_descriptive_metrics_preserve_failure_semantics() -> None:
    success_evidence = _task_evidence()
    assert COMPLETION_TIME_PLUGIN.derive_metric(success_evidence, {}).value == 1.5
    assert FINAL_ENDPOINT_ERROR_PLUGIN.derive_metric(
        success_evidence, {}
    ).value == pytest.approx(0.1)

    failure_evidence = _task_evidence(success=False)
    primary = SUCCESS_WITHIN_TIMEOUT_PLUGIN.derive_metric(failure_evidence, {})
    completion = COMPLETION_TIME_PLUGIN.derive_metric(failure_evidence, {})
    assert primary.value is False
    assert primary.status is EvidenceStatus.MEASURED
    assert completion.value is None
    assert completion.status is EvidenceStatus.UNAVAILABLE


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (EvidenceStatus.UNAVAILABLE, EvidenceStatus.UNAVAILABLE),
        (EvidenceStatus.INVALID, EvidenceStatus.INVALID),
    ),
)
def test_missing_or_non_value_evidence_never_becomes_zero_or_success(
    status: EvidenceStatus,
    expected: EvidenceStatus,
) -> None:
    kwargs = {
        "identity": ENDPOINT_REACH_TERMINAL_EVIDENCE,
        "status": status,
        "value": None,
        "provenance": "test-task",
        "reason": "not usable",
    }
    result = SUCCESS_WITHIN_TIMEOUT_PLUGIN.derive_metric(
        CanonicalEvidenceSet((CanonicalEvidence(**kwargs),)),
        {},
    )
    assert result.value is None
    assert result.status is expected

    missing = SUCCESS_WITHIN_TIMEOUT_PLUGIN.derive_metric(
        CanonicalEvidenceSet(()),
        {},
    )
    assert missing.value is None
    assert missing.status is EvidenceStatus.UNAVAILABLE


def test_trajectory_validation_rejects_invalid_geometry() -> None:
    invalid = CanonicalEvidenceSet(
        (
            CanonicalEvidence(
                identity=ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
                status=EvidenceStatus.MEASURED,
                value={
                    "initial_position_world_m": (0.0, 0.0, 0.0),
                    "target_position_world_m": (0.0, 0.0, 0.0),
                    "samples": (
                        {
                            "elapsed_time_s": 0.0,
                            "position_world_m": (0.0, 0.0, 0.0),
                        },
                    ),
                },
                provenance=ENDPOINT_REACH_TRAJECTORY_PROVENANCE,
            ),
        )
    )
    with pytest.raises(ValueError, match="target must differ"):
        OFF_AXIS_DRIFT_PLUGIN.derive_metric(invalid, {})


def test_primary_metric_rejects_forged_measured_terminal_producer() -> None:
    forged = CanonicalEvidenceSet(
        (
            CanonicalEvidence(
                identity=ENDPOINT_REACH_TERMINAL_EVIDENCE,
                status=EvidenceStatus.MEASURED,
                value={
                    "classification": "success",
                    "elapsed_time_s": 0.1,
                    "reason": None,
                },
                provenance="runner:preclassified",
            ),
        )
    )
    with pytest.raises(ValueError, match="invalid producer"):
        SUCCESS_WITHIN_TIMEOUT_PLUGIN.derive_metric(forged, {})

    assert ENDPOINT_REACH_TERMINAL_PROVENANCE == "endpoint_reach_task/v1:terminal"
