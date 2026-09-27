"""成功したendpoint-reach trialだけのdescriptive completion time。"""

from collections.abc import Mapping

from xpotato_sim.plugins.evaluations._endpoint_reach_evidence import (
    COMPLETION_TIME_IDENTITY,
    ENDPOINT_REACH_TERMINAL_EVIDENCE,
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


class CompletionTimeDeriver:
    """success時だけtimeを返し、failed trialには値を合成しない。"""

    def derive(
        self,
        evidence: CanonicalEvidenceSet,
        parameters: Mapping[str, object],
        *,
        provenance: str,
    ) -> MetricResult:
        if parameters:
            raise ValueError("completion-time evaluator accepts no parameters")
        terminal = terminal_evidence(evidence)
        if terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID:
            return unavailable_result(
                COMPLETION_TIME_IDENTITY,
                provenance,
                terminal.reason or "task evidence is technical-invalid",
                invalid=True,
            )
        if terminal.classification is not TaskTerminalClassification.SUCCESS:
            return unavailable_result(
                COMPLETION_TIME_IDENTITY,
                provenance,
                "completion time is unavailable for a non-success trial",
            )
        assert terminal.elapsed_time_s is not None
        return MetricResult(
            metric_id=COMPLETION_TIME_IDENTITY,
            value=terminal.elapsed_time_s,
            status=EvidenceStatus.MEASURED,
            provenance=provenance,
        )


COMPLETION_TIME_PLUGIN = EvaluationPlugin(
    identity=COMPLETION_TIME_IDENTITY,
    metric_deriver=CompletionTimeDeriver(),
    required_evidence=frozenset({ENDPOINT_REACH_TERMINAL_EVIDENCE}),
    evidence_policy=EvidencePolicy(
        missing=EvidenceDisposition.PRODUCE_UNAVAILABLE,
        unavailable=EvidenceDisposition.PRODUCE_UNAVAILABLE,
        invalid=EvidenceDisposition.PRODUCE_INVALID,
    ),
    parameter_contract=ParameterContract(),
    provenance=PROVENANCE,
    unit="second",
    frame=None,
)


__all__ = ["COMPLETION_TIME_PLUGIN", "CompletionTimeDeriver"]
