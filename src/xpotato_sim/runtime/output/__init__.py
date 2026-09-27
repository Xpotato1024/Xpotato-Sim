"""Physical output permission boundary and later offline trace owners."""

from __future__ import annotations

from xpotato_sim.runtime.output.permission import (
    evaluate_physical_output_permission,
)
from xpotato_sim.runtime.output.lifecycle import (
    PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION,
    PhysicalOutputLifecycle,
    PhysicalOutputLifecycleDispatchResult,
    PhysicalOutputLifecycleEvent,
    PhysicalOutputLifecycleEventKind,
    PhysicalOutputLifecycleResult,
    PhysicalOutputLifecycleSink,
    PhysicalOutputLifecycleState,
    PhysicalOutputLifecycleTrace,
)
from xpotato_sim.runtime.output.safety_gate import (
    PHYSICAL_OUTPUT_CANDIDATE_ID_PREFIX,
    PHYSICAL_OUTPUT_SAFETY_BINDING_SCHEMA_VERSION,
    PHYSICAL_OUTPUT_SAFETY_EVIDENCE_SCHEMA_VERSION,
    PhysicalOutputSafetyEvaluation,
    PhysicalOutputSafetyStatus,
    PhysicalOutputSafetyTraceEvidence,
    PhysicalOutputSendableRequest,
    bind_physical_output_safety,
    compose_physical_output_safety_input,
    evaluate_and_bind_physical_output_safety,
    physical_output_candidate_id,
    validate_physical_output_safety_evaluation,
    validate_physical_output_sendable_request,
)
from xpotato_sim.runtime.output.trace import (
    PHYSICAL_OUTPUT_TRACE_SCHEMA_VERSION,
    PhysicalOutputRecordingSink,
    PhysicalOutputTrace,
    PhysicalOutputTraceDecisionStatus,
    PhysicalOutputTraceEvent,
    PhysicalOutputTraceEventKind,
    physical_output_traces_equivalent,
    replay_physical_output_trace,
)

__all__ = [
    "PHYSICAL_OUTPUT_CANDIDATE_ID_PREFIX",
    "PHYSICAL_OUTPUT_SAFETY_BINDING_SCHEMA_VERSION",
    "PHYSICAL_OUTPUT_SAFETY_EVIDENCE_SCHEMA_VERSION",
    "PHYSICAL_OUTPUT_TRACE_SCHEMA_VERSION",
    "PHYSICAL_OUTPUT_LIFECYCLE_SCHEMA_VERSION",
    "PhysicalOutputLifecycle",
    "PhysicalOutputLifecycleDispatchResult",
    "PhysicalOutputLifecycleEvent",
    "PhysicalOutputLifecycleEventKind",
    "PhysicalOutputLifecycleResult",
    "PhysicalOutputLifecycleSink",
    "PhysicalOutputLifecycleState",
    "PhysicalOutputLifecycleTrace",
    "PhysicalOutputSafetyEvaluation",
    "PhysicalOutputSafetyStatus",
    "PhysicalOutputSafetyTraceEvidence",
    "PhysicalOutputSendableRequest",
    "PhysicalOutputRecordingSink",
    "PhysicalOutputTrace",
    "PhysicalOutputTraceDecisionStatus",
    "PhysicalOutputTraceEvent",
    "PhysicalOutputTraceEventKind",
    "bind_physical_output_safety",
    "compose_physical_output_safety_input",
    "evaluate_and_bind_physical_output_safety",
    "evaluate_physical_output_permission",
    "physical_output_candidate_id",
    "physical_output_traces_equivalent",
    "replay_physical_output_trace",
    "validate_physical_output_safety_evaluation",
    "validate_physical_output_sendable_request",
]
