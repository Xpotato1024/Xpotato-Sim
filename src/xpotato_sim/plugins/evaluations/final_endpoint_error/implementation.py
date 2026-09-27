"""R7-Gのfinal measured endpoint-to-target errorを表すdescriptive metric。"""

from collections.abc import Mapping
from math import sqrt

from xpotato_sim.plugins.evaluations._endpoint_reach_evidence import (
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
    FINAL_ENDPOINT_ERROR_IDENTITY,
    trajectory_evidence,
)
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidenceSet,
    EvaluationPlugin,
    EvidenceDisposition,
    EvidencePolicy,
    EvidenceStatus,
    MetricResult,
    ParameterContract,
)


PROVENANCE = "endpoint_reach_task:endpoint_reach_measured_trajectory/v1"
WORLD_FRAME = "MuJoCo world / scene frame"


class FinalEndpointErrorDeriver:
    """最後のmeasured world-frame sampleからEuclidean errorを導出する。"""

    def derive(
        self,
        evidence: CanonicalEvidenceSet,
        parameters: Mapping[str, object],
        *,
        provenance: str,
    ) -> MetricResult:
        if parameters:
            raise ValueError("final endpoint error evaluator accepts no parameters")
        trajectory = trajectory_evidence(evidence)
        final_position = trajectory.samples[-1].position_world_m
        error = sqrt(
            sum(
                (
                    final_position[index]
                    - trajectory.target_position_world_m[index]
                )
                ** 2
                for index in range(3)
            )
        )
        return MetricResult(
            metric_id=FINAL_ENDPOINT_ERROR_IDENTITY,
            value=error,
            status=EvidenceStatus.MEASURED,
            provenance=provenance,
        )


FINAL_ENDPOINT_ERROR_PLUGIN = EvaluationPlugin(
    identity=FINAL_ENDPOINT_ERROR_IDENTITY,
    metric_deriver=FinalEndpointErrorDeriver(),
    required_evidence=frozenset({ENDPOINT_REACH_TRAJECTORY_EVIDENCE}),
    evidence_policy=EvidencePolicy(
        missing=EvidenceDisposition.PRODUCE_UNAVAILABLE,
        unavailable=EvidenceDisposition.PRODUCE_UNAVAILABLE,
        invalid=EvidenceDisposition.PRODUCE_INVALID,
    ),
    parameter_contract=ParameterContract(),
    provenance=PROVENANCE,
    unit="meter",
    frame=WORLD_FRAME,
)


__all__ = ["FINAL_ENDPOINT_ERROR_PLUGIN", "FinalEndpointErrorDeriver"]
