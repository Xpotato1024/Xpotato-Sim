"""R7-G endpoint-reachのbinary measured success outcome。"""

from collections.abc import Mapping

from xpotato_sim.plugins.evaluations._endpoint_reach_evidence import (
    ENDPOINT_REACH_TERMINAL_EVIDENCE,
    SUCCESS_WITHIN_TIMEOUT_IDENTITY,
    terminal_evidence,
    unavailable_result,
)
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidenceSet,
    EvaluationPlugin,
    EvidenceDisposition,
    EvidencePolicy,
    EvidenceStatus,
    MetricResult,
    ParameterContract,
    TaskTerminalClassification,
)


PROVENANCE = "endpoint_reach_task:endpoint_reach_terminal_classification/v1"


class SuccessWithinTimeoutDeriver:
    """Task-owned terminal classificationをboolean metricへ投影する。"""

    def derive(
        self,
        evidence: CanonicalEvidenceSet,
        parameters: Mapping[str, object],
        *,
        provenance: str,
    ) -> MetricResult:
        if parameters:
            raise ValueError("success-within-timeout evaluator accepts no parameters")
        terminal = terminal_evidence(evidence)
        if terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID:
            return unavailable_result(
                SUCCESS_WITHIN_TIMEOUT_IDENTITY,
                provenance,
                terminal.reason or "task evidence is technical-invalid",
                invalid=True,
            )
        if terminal.classification is TaskTerminalClassification.RUNNING:
            return unavailable_result(
                SUCCESS_WITHIN_TIMEOUT_IDENTITY,
                provenance,
                "task has not reached a terminal classification",
            )
        return MetricResult(
            metric_id=SUCCESS_WITHIN_TIMEOUT_IDENTITY,
            value=terminal.classification is TaskTerminalClassification.SUCCESS,
            status=EvidenceStatus.MEASURED,
            provenance=provenance,
        )


SUCCESS_WITHIN_TIMEOUT_PLUGIN = EvaluationPlugin(
    identity=SUCCESS_WITHIN_TIMEOUT_IDENTITY,
    metric_deriver=SuccessWithinTimeoutDeriver(),
    required_evidence=frozenset({ENDPOINT_REACH_TERMINAL_EVIDENCE}),
    evidence_policy=EvidencePolicy(
        missing=EvidenceDisposition.PRODUCE_UNAVAILABLE,
        unavailable=EvidenceDisposition.PRODUCE_UNAVAILABLE,
        invalid=EvidenceDisposition.PRODUCE_INVALID,
    ),
    parameter_contract=ParameterContract(),
    provenance=PROVENANCE,
    unit="boolean",
    frame=None,
)


__all__ = ["SUCCESS_WITHIN_TIMEOUT_PLUGIN", "SuccessWithinTimeoutDeriver"]
