"""R7-H contact press/hold outcome Evaluation Plugin。

This evaluator only decodes Task-owned canonical evidence.  It never reads a
viewer diagnostic, reruns physics measurement, or consumes a filtered reaction
force.  A technical-invalid or incomplete Task remains a non-value result.
"""

from __future__ import annotations

from collections.abc import Mapping

from xpotato_sim.runtime.contact.task_contract import (
    CONTACT_OUTCOME_IDENTITY,
    CONTACT_OUTCOME_PROVENANCE,
    CONTACT_TASK_OUTCOME_EVIDENCE,
    CONTACT_TASK_OUTCOME_PROVENANCE,
    CONTACT_TASK_TERMINAL_EVIDENCE,
    ContactTaskContractError,
    ContactTaskOutcome,
    decode_contact_task_terminal_evidence,
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


PROVENANCE = CONTACT_OUTCOME_PROVENANCE


class ContactOutcomeDeriver:
    """Task outcome artifactをmetric resultへ投影するpure deriver。"""

    def derive(
        self,
        evidence: CanonicalEvidenceSet,
        parameters: Mapping[str, object],
        *,
        provenance: str,
    ) -> MetricResult:
        if parameters:
            raise ValueError("contact outcome evaluator accepts no parameters")
        terminal = decode_contact_task_terminal_evidence(evidence)
        outcome_entry = evidence.require(CONTACT_TASK_OUTCOME_EVIDENCE)
        if outcome_entry.status is not EvidenceStatus.MEASURED:
            raise ContactTaskContractError(
                "contact task outcome evidence must be measured for decoding"
            )
        if outcome_entry.provenance != CONTACT_TASK_OUTCOME_PROVENANCE:
            raise ContactTaskContractError(
                "contact task outcome evidence producer is invalid"
            )
        outcome = ContactTaskOutcome.from_document(outcome_entry.value)
        if outcome.manifest_digest != terminal.manifest_digest:
            raise ContactTaskContractError(
                "contact task terminal/outcome manifest identities disagree"
            )
        if outcome.trial != terminal.trial:
            raise ContactTaskContractError(
                "contact task terminal/outcome trial identities disagree"
            )
        if outcome.phase is not terminal.phase or outcome.classification is not terminal.classification:
            raise ContactTaskContractError(
                "contact task terminal/outcome classifications disagree"
            )
        if outcome.completion_time_s != terminal.completion_time_s:
            raise ContactTaskContractError(
                "contact task terminal/outcome completion times disagree"
            )
        if outcome.classification is TaskTerminalClassification.TECHNICAL_INVALID:
            return MetricResult(
                metric_id=CONTACT_OUTCOME_IDENTITY,
                value=None,
                status=EvidenceStatus.INVALID,
                provenance=provenance,
                reason=outcome.reason or "contact task outcome is technical-invalid",
            )
        if outcome.classification is TaskTerminalClassification.RUNNING:
            return MetricResult(
                metric_id=CONTACT_OUTCOME_IDENTITY,
                value=None,
                status=EvidenceStatus.UNAVAILABLE,
                provenance=provenance,
                reason="contact task has not reached a terminal classification",
            )
        return MetricResult(
            metric_id=CONTACT_OUTCOME_IDENTITY,
            value=outcome.to_document(),
            status=EvidenceStatus.MEASURED,
            provenance=provenance,
        )


CONTACT_OUTCOME_PLUGIN = EvaluationPlugin(
    identity=CONTACT_OUTCOME_IDENTITY,
    metric_deriver=ContactOutcomeDeriver(),
    required_evidence=frozenset(
        {CONTACT_TASK_TERMINAL_EVIDENCE, CONTACT_TASK_OUTCOME_EVIDENCE}
    ),
    evidence_policy=EvidencePolicy(
        missing=EvidenceDisposition.PRODUCE_UNAVAILABLE,
        unavailable=EvidenceDisposition.PRODUCE_UNAVAILABLE,
        invalid=EvidenceDisposition.PRODUCE_INVALID,
    ),
    parameter_contract=ParameterContract(),
    provenance=CONTACT_OUTCOME_PROVENANCE,
    unit="contact_task_outcome",
    frame="MuJoCo world / scene frame",
)


__all__ = [
    "CONTACT_OUTCOME_IDENTITY",
    "CONTACT_OUTCOME_PLUGIN",
    "ContactOutcomeDeriver",
    "PROVENANCE",
]
