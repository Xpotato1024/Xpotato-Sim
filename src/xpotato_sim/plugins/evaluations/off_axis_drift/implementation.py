"""measured endpoint trajectoryの最大垂直距離を表すoff-axis drift。"""

from collections.abc import Mapping
from math import sqrt

from xpotato_sim.plugins.evaluations._endpoint_reach_evidence import (
    ENDPOINT_REACH_TRAJECTORY_EVIDENCE,
    OFF_AXIS_DRIFT_IDENTITY,
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


class OffAxisDriftDeriver:
    """initial-to-target world-frame lineからの最大距離を導出する。"""

    def derive(
        self,
        evidence: CanonicalEvidenceSet,
        parameters: Mapping[str, object],
        *,
        provenance: str,
    ) -> MetricResult:
        if parameters:
            raise ValueError("off-axis drift evaluator accepts no parameters")
        trajectory = trajectory_evidence(evidence)
        direction = tuple(
            trajectory.target_position_world_m[index]
            - trajectory.initial_position_world_m[index]
            for index in range(3)
        )
        norm = sqrt(sum(value * value for value in direction))
        unit = tuple(value / norm for value in direction)
        distances: list[float] = []
        for sample in trajectory.samples:
            offset = tuple(
                sample.position_world_m[index]
                - trajectory.initial_position_world_m[index]
                for index in range(3)
            )
            along = sum(offset[index] * unit[index] for index in range(3))
            perpendicular = tuple(
                offset[index] - along * unit[index] for index in range(3)
            )
            distances.append(sqrt(sum(value * value for value in perpendicular)))
        return MetricResult(
            metric_id=OFF_AXIS_DRIFT_IDENTITY,
            value=max(distances),
            status=EvidenceStatus.MEASURED,
            provenance=provenance,
        )


OFF_AXIS_DRIFT_PLUGIN = EvaluationPlugin(
    identity=OFF_AXIS_DRIFT_IDENTITY,
    metric_deriver=OffAxisDriftDeriver(),
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


__all__ = ["OFF_AXIS_DRIFT_PLUGIN", "OffAxisDriftDeriver"]
