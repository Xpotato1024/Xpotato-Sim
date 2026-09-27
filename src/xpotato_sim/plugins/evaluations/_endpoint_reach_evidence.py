"""endpoint-reach metric専用のidentityとstrict evidence helper。"""

from xpotato_sim.runtime.experiment.contracts import (
    EvidenceStatus,
    MetricResult,
    VersionedIdentity,
)
from xpotato_sim.runtime.experiment.endpoint_reach_evidence import (
    ENDPOINT_REACH_TERMINAL_EVIDENCE,
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
    EndpointReachTerminalEvidence,
    EndpointReachTrajectoryEvidence,
    decode_endpoint_reach_terminal_evidence,
    decode_endpoint_reach_trajectory_evidence,
)


SUCCESS_WITHIN_TIMEOUT_IDENTITY = VersionedIdentity("success_within_timeout", 1)
OFF_AXIS_DRIFT_IDENTITY = VersionedIdentity("off_axis_drift", 1)
COMPLETION_TIME_IDENTITY = VersionedIdentity("completion_time", 1)
FINAL_ENDPOINT_ERROR_IDENTITY = VersionedIdentity("final_endpoint_error", 1)

terminal_evidence = decode_endpoint_reach_terminal_evidence
trajectory_evidence = decode_endpoint_reach_trajectory_evidence
TerminalEvidence = EndpointReachTerminalEvidence
TrajectoryEvidence = EndpointReachTrajectoryEvidence


def unavailable_result(
    identity: VersionedIdentity,
    provenance: str,
    reason: str,
    *,
    invalid: bool = False,
) -> MetricResult:
    return MetricResult(
        metric_id=identity,
        value=None,
        status=EvidenceStatus.INVALID if invalid else EvidenceStatus.UNAVAILABLE,
        provenance=provenance,
        reason=reason,
    )


__all__ = [
    "COMPLETION_TIME_IDENTITY",
    "ENDPOINT_REACH_TERMINAL_EVIDENCE",
    "ENDPOINT_REACH_TRAJECTORY_EVIDENCE",
    "FINAL_ENDPOINT_ERROR_IDENTITY",
    "OFF_AXIS_DRIFT_IDENTITY",
    "SUCCESS_WITHIN_TIMEOUT_IDENTITY",
    "TerminalEvidence",
    "TrajectoryEvidence",
    "terminal_evidence",
    "trajectory_evidence",
    "unavailable_result",
]
