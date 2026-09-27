from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from xpotato_sim.runtime.output.safety_gate import (
    PhysicalOutputSafetyEvaluation,
    PhysicalOutputSafetyTraceEvidence,
    PhysicalOutputSendableRequest,
    bind_physical_output_safety,
    evaluate_and_bind_physical_output_safety,
    physical_output_candidate_id,
    validate_physical_output_sendable_request,
)
from xpotato_sim.runtime.safety.limit_resolution import LimitResolutionStatus
from xpotato_sim.runtime.safety.physical_safety_core import evaluate_physical_safety
from xpotato_sim.schemas import PhysicalOutputRequest

from tests.runtime.test_physical_safety_core import _input
from tests.schemas.test_physical_output_contract import _endpoint_request
from tests.support.output_candidate_evidence import joint_request, observed_safety_input


def _request(**changes: object) -> PhysicalOutputRequest:
    return joint_request(**changes)


def _safety_input(request: PhysicalOutputRequest, *, limits=LimitResolutionStatus.RESOLVED_AUTHORITATIVE):
    safety_input = observed_safety_input(request, limits=_input(limits=limits).limit_resolution)
    return replace(
        safety_input,
        candidate_id=physical_output_candidate_id(request),
        provenance=safety_input.provenance,
    )


def test_allow_binding_hashes_canonical_request_and_typed_safety_content() -> None:
    request = _request()
    safety_input = _safety_input(request)
    evaluation = evaluate_and_bind_physical_output_safety(
        request,
        safety_input,
        checked_at_s=request.timestamp_s,
    )

    assert evaluation.status == "allowed"
    assert evaluation.sendable
    assert evaluation.request_sha256 == sha256(request.to_json_bytes()).hexdigest()
    assert evaluation.candidate_id == physical_output_candidate_id(request)
    assert safety_input.candidate_id == evaluation.decision.candidate_id
    assert evaluation.safety_input_sha256
    assert evaluation.decision_sha256
    sendable = evaluation.to_sendable_request()
    assert isinstance(sendable, PhysicalOutputSendableRequest)
    assert validate_physical_output_sendable_request(sendable) is sendable
    assert sendable.request == request

    evidence = evaluation.to_trace_evidence()
    assert PhysicalOutputSafetyTraceEvidence.from_json_value(evidence.to_json_value()) == evidence
    assert evidence.gate_status == "allowed"
    assert evidence.candidate_id == safety_input.candidate_id
    assert evidence.safety_input_sha256 == evaluation.safety_input_sha256
    assert evidence.decision_sha256 == evaluation.decision_sha256

    changed_request = replace(
        request,
        sequence=request.sequence + 1,
        timestamp_s=request.timestamp_s + 0.1,
        command=replace(request.command, timestamp_s=request.timestamp_s + 0.1),
    )
    changed_request_evaluation = evaluate_and_bind_physical_output_safety(
        changed_request,
        safety_input,
        checked_at_s=changed_request.timestamp_s,
    )
    assert changed_request_evaluation.request_sha256 != evaluation.request_sha256
    assert changed_request_evaluation.binding_sha256 != evaluation.binding_sha256
    assert changed_request_evaluation.status == "rejected"
    assert changed_request_evaluation.reason == "physical_safety_candidate_mismatch"
    assert not changed_request_evaluation.sendable

    changed_safety_input = _safety_input(
        request,
        limits=LimitResolutionStatus.RESOLVED_PROVISIONAL,
    )
    changed_safety_evaluation = evaluate_and_bind_physical_output_safety(
        request,
        changed_safety_input,
        checked_at_s=request.timestamp_s,
    )
    assert changed_safety_input.candidate_id == safety_input.candidate_id
    assert changed_safety_evaluation.safety_input_sha256 != evaluation.safety_input_sha256
    assert changed_safety_evaluation.binding_sha256 != evaluation.binding_sha256


def test_candidate_id_is_versioned_sha256_of_canonical_request_bytes() -> None:
    request = _request()

    assert physical_output_candidate_id(request) == (
        "physical-output-candidate/v1:sha256:"
        f"{sha256(request.to_json_bytes()).hexdigest()}"
    )


def test_public_candidate_id_relabel_cannot_promote_evidence_for_another_target() -> None:
    request_a = _request()
    evidence_a = _safety_input(request_a)
    request_b = replace(request_a, command=replace(request_a.command, joint_angles_rad=(0.25, 0.0)))
    relabeled = replace(evidence_a, candidate_id=physical_output_candidate_id(request_b))
    decision_b = evaluate_physical_safety(relabeled)
    assert decision_b.action.value == "allow"
    evaluation = bind_physical_output_safety(request_b, relabeled, decision_b, checked_at_s=1.0)
    assert evaluation.status == "rejected"
    assert evaluation.reason == "physical_safety_evaluated_candidate_mismatch"
    assert not evaluation.sendable
    with pytest.raises(ValueError, match="explicit safety allow"):
        evaluation.to_sendable_request()
    correct = evaluate_and_bind_physical_output_safety(request_b, _safety_input(request_b), checked_at_s=1.0)
    assert correct.sendable


def test_authoritative_position_bounds_reject_out_of_range_candidate() -> None:
    for joint_angles in ((1.01, 0.0), (0.0, -1.01)):
        request = _request(command=replace(_request().command, joint_angles_rad=joint_angles))
        safety_input = _safety_input(request)
        decision = evaluate_physical_safety(safety_input)
        assert decision.action.value == "reject"
        assert decision.reason.identity == "limit:limit_candidate_out_of_bounds"
        evaluation = bind_physical_output_safety(
            request,
            safety_input,
            decision,
            checked_at_s=request.timestamp_s,
        )
        assert evaluation.status == "rejected"
        assert not evaluation.sendable


def test_authoritative_position_bounds_are_inclusive() -> None:
    for joint_angles in ((-1.0, 1.0), (1.0, -1.0)):
        request = _request(command=replace(_request().command, joint_angles_rad=joint_angles))
        evaluation = evaluate_and_bind_physical_output_safety(
            request,
            _safety_input(request),
            checked_at_s=request.timestamp_s,
        )
        assert evaluation.status == "allowed"
        assert evaluation.sendable


def test_relabelled_out_of_range_candidate_cannot_become_sendable() -> None:
    request = _request(command=replace(_request().command, joint_angles_rad=(1.01, 0.0)))
    safety_input = _safety_input(request)
    relabelled = replace(safety_input, candidate_id=physical_output_candidate_id(request))
    decision = evaluate_physical_safety(relabelled)
    assert decision.action.value == "reject"
    assert decision.reason.identity == "limit:limit_candidate_out_of_bounds"
    evaluation = bind_physical_output_safety(
        request,
        relabelled,
        decision,
        checked_at_s=request.timestamp_s,
    )
    assert evaluation.status == "rejected"
    assert not evaluation.sendable


def test_checker_candidate_mismatch_is_fail_closed_before_output() -> None:
    request_a = _request()
    request_b = replace(request_a, command=replace(request_a.command, joint_angles_rad=(0.25, 0.0)))
    a, b = _safety_input(request_a), _safety_input(request_b)
    mixed = replace(a, dynamic=b.dynamic)
    assert evaluate_physical_safety(mixed).action.value == "invalid"
    assert not evaluate_and_bind_physical_output_safety(request_a, mixed, checked_at_s=1.0).sendable


def test_endpoint_relabel_cannot_reuse_another_evaluated_joint_route() -> None:
    request_a = _request()
    evidence_a = _safety_input(request_a)
    request_b = replace(request_a, endpoint_id="other-joint-group")
    relabeled = replace(evidence_a, candidate_id=physical_output_candidate_id(request_b))
    evaluation = evaluate_and_bind_physical_output_safety(request_b, relabeled, checked_at_s=1.0)
    assert evaluation.reason == "physical_safety_evaluated_endpoint_mismatch"
    assert not evaluation.sendable
    correct = evaluate_and_bind_physical_output_safety(request_b, _safety_input(request_b), checked_at_s=1.0)
    assert correct.sendable
    mixed = replace(relabeled, dynamic=_safety_input(request_b).dynamic)
    assert evaluate_physical_safety(mixed).action.value == "invalid"


def test_unresolved_endpoint_velocity_does_not_infer_a_trajectory() -> None:
    request = replace(_endpoint_request(), target_robot_id="fixture-robot")
    evidence = replace(_safety_input(_request()), candidate_id=physical_output_candidate_id(request))
    evaluation = evaluate_and_bind_physical_output_safety(request, evidence, checked_at_s=1.0)
    assert evaluation.reason == "physical_safety_candidate_semantics_unresolved"
    assert not evaluation.sendable


def test_reconstructed_collision_result_does_not_inherit_observation_origin() -> None:
    request = _request()
    evidence = _safety_input(request)
    reconstructed = replace(evidence.collision)
    assert reconstructed.clear
    assert reconstructed.evaluated_candidate is None
    evaluation = evaluate_and_bind_physical_output_safety(
        request, replace(evidence, collision=reconstructed), checked_at_s=1.0,
    )
    assert evaluation.reason == "physical_safety_evaluated_candidate_missing"
    assert not evaluation.sendable


def test_same_safe_candidate_evidence_cannot_be_reused_for_changed_command() -> None:
    request = _request()
    safety_input = _safety_input(request)
    safe_decision = evaluate_physical_safety(safety_input)
    changed_command = replace(
        request.command,
        joint_angles_rad=(
            request.command.joint_angles_rad[0] + 0.01,
            *request.command.joint_angles_rad[1:],
        ),
    )
    changed_request = replace(
        request,
        sequence=request.sequence + 1,
        command=changed_command,
    )

    evaluation = bind_physical_output_safety(
        changed_request,
        safety_input,
        safe_decision,
        checked_at_s=changed_request.timestamp_s,
    )

    assert evaluation.status == "rejected"
    assert evaluation.reason == "physical_safety_candidate_mismatch"
    assert not evaluation.sendable


def test_arbitrary_safety_candidate_id_cannot_become_sendable() -> None:
    request = _request()
    safety_input = replace(_safety_input(request), candidate_id="arbitrary-safe-candidate")
    evaluation = evaluate_and_bind_physical_output_safety(
        request,
        safety_input,
        checked_at_s=request.timestamp_s,
    )

    assert evaluation.safety_action.value == "allow"
    assert evaluation.status == "rejected"
    assert evaluation.reason == "physical_safety_candidate_mismatch"
    assert not evaluation.sendable


def test_safety_decision_candidate_id_must_match_request() -> None:
    request = _request()
    safety_input = _safety_input(request)
    unrelated_input = replace(safety_input, candidate_id="arbitrary-safe-candidate")
    mismatched_decision = evaluate_physical_safety(unrelated_input)
    assert mismatched_decision.action.value == "allow"

    evaluation = bind_physical_output_safety(
        request,
        safety_input,
        mismatched_decision,
        checked_at_s=request.timestamp_s,
    )

    assert evaluation.status == "rejected"
    assert evaluation.reason == "physical_safety_candidate_mismatch"
    assert not evaluation.sendable


def test_nonallow_decision_cannot_create_sendable_request() -> None:
    request = _request()
    safety_input = _safety_input(request, limits=LimitResolutionStatus.RESOLVED_PROVISIONAL)
    evaluation = evaluate_and_bind_physical_output_safety(
        request,
        safety_input,
        checked_at_s=request.timestamp_s,
    )

    assert evaluation.status == "held"
    assert not evaluation.sendable
    with pytest.raises(ValueError, match="explicit safety allow"):
        evaluation.to_sendable_request()
    with pytest.raises(ValueError, match="explicit P5 allow"):
        PhysicalOutputSendableRequest(evaluation)


def test_binding_rejects_robot_and_revision_mismatch() -> None:
    request = _request()
    base = _safety_input(request)
    wrong_robot = "other-fixture-robot"
    wrong_robot_input = replace(
        base,
        limit_resolution=replace(base.limit_resolution, robot_id=wrong_robot),
        collision=replace(
            base.collision,
            context=replace(base.collision.context, robot_id=wrong_robot),
        ),
    )
    wrong_robot = evaluate_and_bind_physical_output_safety(
        request,
        wrong_robot_input,
        checked_at_s=request.timestamp_s,
    )
    assert wrong_robot.status == "rejected"
    assert wrong_robot.reason == "physical_safety_target_robot_mismatch"
    assert not wrong_robot.sendable

    no_revision_input = replace(
        base,
        provenance=tuple(
            item for item in base.provenance if not item.startswith("software_revision:")
        ),
    )
    wrong_revision = evaluate_and_bind_physical_output_safety(
        request,
        no_revision_input,
        checked_at_s=request.timestamp_s,
    )
    assert wrong_revision.status == "rejected"
    assert wrong_revision.reason == "physical_safety_software_revision_mismatch"
    assert not wrong_revision.sendable


def test_binding_rejects_a_valid_decision_from_another_safety_input() -> None:
    request = _request()
    safety_input = _safety_input(request)
    unrelated_input = _safety_input(
        request,
        limits=LimitResolutionStatus.MISMATCH,
    )
    evaluation = PhysicalOutputSafetyEvaluation(
        request=request,
        safety_input=safety_input,
        decision=evaluate_physical_safety(unrelated_input),
        checked_at_s=request.timestamp_s,
    )

    assert evaluation.status == "invalid"
    assert evaluation.reason == "physical_safety_decision_mismatch"
    assert not evaluation.sendable
