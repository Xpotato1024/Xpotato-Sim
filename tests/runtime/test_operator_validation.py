from __future__ import annotations

import json
from dataclasses import replace

import pytest

from xpotato_sim.runtime.safety.operator_validation import (
    ClearanceDeclaration,
    EvidenceClass,
    MeasurementSource,
    MeasurementSourceKind,
    OperatorIdentity,
    PreflightChecklist,
    PreflightItem,
    RollbackProcedure,
    SafetyDecisionEvidence,
    StopProcedure,
    TargetIdentity,
    ValidationCheckEvidence,
    ValidationCheckKind,
    ValidationCheckSpec,
    ValidationCheckStatus,
    ValidationClassification,
    ValidationEvidenceArtifact,
    ValidationProcedure,
    build_dry_run_validation_artifact,
    build_validation_artifact,
    decode_validation_artifact,
    validate_operator_gate,
    validate_validation_check_evidence,
    validate_validation_artifact,
    validate_validation_procedure,
)
from xpotato_sim.runtime.safety.physical_safety_core import SafetyDecisionAction


STARTED = "2026-08-28T10:00:00Z"
OBSERVED = "2026-08-28T10:00:01Z"
COMPLETED = "2026-08-28T10:00:02Z"


def _procedure(
    *,
    operator_confirmed: bool = True,
    preflight_complete: bool = True,
    verified_clearance_m: float | None = 1.0,
    clearance_verified_at: str | None = OBSERVED,
    acknowledged_by: str = "operator-001",
    required_checks: tuple[ValidationCheckSpec, ...] | None = None,
) -> ValidationProcedure:
    checks = required_checks if required_checks is not None else (
        ValidationCheckSpec("limits", ValidationCheckKind.LIMIT_RANGE, "range check"),
        ValidationCheckSpec("collision", ValidationCheckKind.COLLISION_CLEARANCE, "clearance check"),
        ValidationCheckSpec("trajectory", ValidationCheckKind.TRAJECTORY_FEASIBILITY, "bounded trajectory check"),
        ValidationCheckSpec("stop", ValidationCheckKind.STOP_PROCEDURE, "stop check"),
        ValidationCheckSpec("rollback", ValidationCheckKind.ROLLBACK_PROCEDURE, "rollback check"),
    )
    return ValidationProcedure(
        procedure_id="procedure-001",
        target=TargetIdentity("target-001", "fast-arm", "controller-001", "connection-001", "model-001"),
        operator=OperatorIdentity("operator-001", "safety-operator"),
        software_revision="revision-001",
        created_at=STARTED,
        preflight=PreflightChecklist(
            (
                PreflightItem("power-isolated", "power isolation was checked", preflight_complete),
                PreflightItem("workspace-clear", "workspace clearance was checked", preflight_complete),
            ),
            acknowledged_by,
            OBSERVED,
        ),
        clearance=ClearanceDeclaration(
            0.5,
            verified_clearance_m,
            MeasurementSource(MeasurementSourceKind.SOFTWARE_DRY_RUN, "fixture", "revision-001"),
            clearance_verified_at,
        ),
        stop=StopProcedure(("stop command",), ("emergency stop",)),
        rollback=RollbackProcedure(("restore neutral state",), "neutral-home"),
        required_checks=checks,
        operator_confirmed=operator_confirmed,
    )


def _check(
    check_id: str,
    kind: ValidationCheckKind,
    *,
    status: ValidationCheckStatus = ValidationCheckStatus.PASS,
    action: SafetyDecisionAction = SafetyDecisionAction.ALLOW,
    source: MeasurementSource | None = None,
    software_revision: str = "revision-001",
) -> ValidationCheckEvidence:
    evidence_source = source or MeasurementSource(
        MeasurementSourceKind.SOFTWARE_DRY_RUN,
        "fixture",
        "revision-001",
    )
    reason_identity_by_action = {
        SafetyDecisionAction.ALLOW: "limit:limit_resolution_authoritative",
        SafetyDecisionAction.HOLD: "limit:limit_resolution_provisional",
        SafetyDecisionAction.REJECT: "limit:limit_resolution_mismatch",
        SafetyDecisionAction.STOP: "collision:collision_detected",
        SafetyDecisionAction.UNAVAILABLE: "limit:limit_resolution_unavailable",
        SafetyDecisionAction.INVALID: "limit:limit_resolution_invalid",
    }
    provenance_values = [
        check_id,
        evidence_source.source_id,
        evidence_source.revision,
        software_revision,
    ]
    if evidence_source.evidence_reference is not None:
        provenance_values.append(evidence_source.evidence_reference)
    provenance = tuple(dict.fromkeys(provenance_values))
    return ValidationCheckEvidence(
        check_id=check_id,
        kind=kind,
        status=status,
        expected={"expected": "fixture-value"},
        observed=None if status in {ValidationCheckStatus.UNAVAILABLE, ValidationCheckStatus.TECHNICAL_INVALID} else {"observed": "fixture-value"},
        measurement_source=evidence_source,
        observed_at=None if status in {ValidationCheckStatus.UNAVAILABLE, ValidationCheckStatus.TECHNICAL_INVALID} else OBSERVED,
        software_revision=software_revision,
        safety_decision=SafetyDecisionEvidence(
            action,
            reason_identity_by_action[action],
            provenance,
        ),
        reason="fixture check",
    )


def _all_checks(**kwargs: object) -> tuple[ValidationCheckEvidence, ...]:
    return (
        _check("limits", ValidationCheckKind.LIMIT_RANGE, **kwargs),
        _check("collision", ValidationCheckKind.COLLISION_CLEARANCE, **kwargs),
        _check("trajectory", ValidationCheckKind.TRAJECTORY_FEASIBILITY, **kwargs),
        _check("stop", ValidationCheckKind.STOP_PROCEDURE, **kwargs),
        _check("rollback", ValidationCheckKind.ROLLBACK_PROCEDURE, **kwargs),
    )


def test_procedure_requires_all_mandatory_check_kinds_at_construction() -> None:
    stop_only = (ValidationCheckSpec("stop", ValidationCheckKind.STOP_PROCEDURE, "stop check"),)
    with pytest.raises(ValueError, match="mandatory validation check kinds"):
        _procedure(required_checks=stop_only)


@pytest.mark.parametrize("missing_kind", tuple(ValidationCheckKind))
def test_procedure_rejects_each_missing_mandatory_check_kind(missing_kind: ValidationCheckKind) -> None:
    required_checks = tuple(spec for spec in _procedure().required_checks if spec.kind is not missing_kind)
    with pytest.raises(ValueError, match="mandatory validation check kinds"):
        _procedure(required_checks=required_checks)


def test_mandatory_coverage_allows_distinct_same_kind_checks() -> None:
    required_checks = _procedure().required_checks + (
        ValidationCheckSpec("limits-secondary", ValidationCheckKind.LIMIT_RANGE, "secondary range check"),
    )
    procedure = _procedure(required_checks=required_checks)
    artifact = build_dry_run_validation_artifact(
        procedure,
        _all_checks() + (_check("limits-secondary", ValidationCheckKind.LIMIT_RANGE),),
        artifact_id="artifact-extra-same-kind",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert artifact.classification is ValidationClassification.PASS


def test_duplicate_required_check_ids_remain_invalid() -> None:
    required_checks = _procedure().required_checks + (
        ValidationCheckSpec("limits", ValidationCheckKind.LIMIT_RANGE, "duplicate range check"),
    )
    with pytest.raises(ValueError, match="required check IDs must be unique"):
        _procedure(required_checks=required_checks)


def test_strict_decode_rejects_procedure_missing_mandatory_coverage() -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-missing-coverage",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    document = artifact.to_dict()
    document["procedure"]["required_checks"] = [
        ValidationCheckSpec("stop", ValidationCheckKind.STOP_PROCEDURE, "stop check").to_dict()
    ]

    with pytest.raises(ValueError, match="mandatory validation check kinds"):
        decode_validation_artifact(json.dumps(document, separators=(",", ":")))


def test_classifier_rejects_malformed_procedure_without_pass() -> None:
    valid_procedure = _procedure()
    malformed_procedure = object.__new__(ValidationProcedure)
    for field_name in ValidationProcedure.__dataclass_fields__:
        object.__setattr__(malformed_procedure, field_name, getattr(valid_procedure, field_name))
    object.__setattr__(
        malformed_procedure,
        "required_checks",
        (ValidationCheckSpec("stop", ValidationCheckKind.STOP_PROCEDURE, "stop check"),),
    )

    artifact = build_dry_run_validation_artifact(
        malformed_procedure,
        (_check("stop", ValidationCheckKind.STOP_PROCEDURE),),
        artifact_id="artifact-malformed-procedure",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID


def test_operator_gate_requires_confirmation_preflight_clearance_stop_and_rollback() -> None:
    procedure = _procedure()
    gate = validate_operator_gate(procedure)
    assert gate.classification is ValidationClassification.PASS
    assert gate.reason_code == "operator_gate_ready"

    assert validate_operator_gate(_procedure(operator_confirmed=False)).reason_code == "operator_confirmation_required"
    assert validate_operator_gate(_procedure(preflight_complete=False)).reason_code == "preflight_incomplete"
    assert validate_operator_gate(_procedure(verified_clearance_m=None, clearance_verified_at=None)).reason_code == "clearance_verification_unavailable"
    assert validate_operator_gate(_procedure(verified_clearance_m=0.1)).classification is ValidationClassification.FAIL
    assert validate_operator_gate(_procedure(acknowledged_by="other-operator")).reason_code == "preflight_operator_mismatch"


def test_dry_run_artifact_round_trips_and_marks_software_evidence() -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-001",
        started_at=STARTED,
        completed_at=COMPLETED,
    )

    encoded = artifact.to_json_bytes()
    assert artifact.classification is ValidationClassification.PASS
    assert artifact.evidence_class is EvidenceClass.SOFTWARE_ONLY
    assert not artifact.physical_evidence_present
    assert not encoded.startswith(b"\xef\xbb\xbf")
    assert validate_validation_artifact(artifact).to_json_bytes() == encoded
    decoded = decode_validation_artifact(encoded)
    assert decoded.evidence_class is EvidenceClass.SOFTWARE_ONLY
    assert not decoded.physical_evidence_present
    assert decoded.to_json_bytes() == encoded


def test_failure_unavailable_aborted_and_technical_invalid_never_become_pass() -> None:
    failed = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(status=ValidationCheckStatus.FAIL, action=SafetyDecisionAction.REJECT),
        artifact_id="artifact-fail",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert failed.classification is ValidationClassification.FAIL

    incomplete = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks()[:-1],
        artifact_id="artifact-incomplete",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert incomplete.classification is ValidationClassification.UNAVAILABLE
    assert not incomplete.complete

    aborted = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks()[:-1],
        artifact_id="artifact-aborted",
        started_at=STARTED,
        completed_at=COMPLETED,
        operator_aborted=True,
    )
    assert aborted.classification is ValidationClassification.ABORTED

    technical = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(status=ValidationCheckStatus.TECHNICAL_INVALID, action=SafetyDecisionAction.INVALID),
        artifact_id="artifact-technical",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert technical.classification is ValidationClassification.TECHNICAL_INVALID


@pytest.mark.parametrize(
    ("procedure_kwargs", "operator_aborted"),
    (
        pytest.param({}, True, id="operator-aborted"),
        pytest.param({"verified_clearance_m": 0.1}, False, id="clearance-gate-fail"),
        pytest.param({"preflight_complete": False}, False, id="gate-unavailable"),
    ),
)
def test_technical_invalid_check_precedes_abort_and_gate_outcomes(
    procedure_kwargs: dict[str, object],
    operator_aborted: bool,
) -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(**procedure_kwargs),
        _all_checks(
            status=ValidationCheckStatus.TECHNICAL_INVALID,
            action=SafetyDecisionAction.INVALID,
        ),
        artifact_id="artifact-technical-invalid-precedence",
        started_at=STARTED,
        completed_at=COMPLETED,
        operator_aborted=operator_aborted,
    )

    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert artifact.classification_reason == "technical_invalid_check"
    assert not artifact.complete

    decoded = decode_validation_artifact(artifact.to_json_bytes())
    assert decoded.classification is ValidationClassification.TECHNICAL_INVALID
    assert decoded.classification_reason == "technical_invalid_check"
    assert not decoded.complete


def test_source_revision_and_kind_identity_are_strict() -> None:
    revision_mismatch = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(software_revision="revision-other"),
        artifact_id="artifact-revision",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert revision_mismatch.classification is ValidationClassification.TECHNICAL_INVALID

    wrong_kind = list(_all_checks())
    wrong_kind[0] = _check("limits", ValidationCheckKind.COLLISION_CLEARANCE)
    kind_mismatch = build_dry_run_validation_artifact(
        _procedure(),
        tuple(wrong_kind),
        artifact_id="artifact-kind",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert kind_mismatch.classification is ValidationClassification.TECHNICAL_INVALID

    unknown_source = _all_checks(
        status=ValidationCheckStatus.UNAVAILABLE,
        action=SafetyDecisionAction.UNAVAILABLE,
        source=MeasurementSource(MeasurementSourceKind.UNKNOWN, "unknown-source", "unknown-revision"),
    )
    unavailable = build_dry_run_validation_artifact(
        _procedure(),
        unknown_source,
        artifact_id="artifact-source",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert unavailable.classification is ValidationClassification.UNAVAILABLE
    assert unavailable.evidence_class is EvidenceClass.UNKNOWN


def test_physical_source_is_visible_and_not_collapsed_into_software() -> None:
    source = MeasurementSource(MeasurementSourceKind.PHYSICAL_MEASUREMENT, "sensor-001", "run-001", "obs-001")
    artifact = build_validation_artifact(
        _procedure(),
        _all_checks(source=source),
        artifact_id="artifact-physical",
        started_at=STARTED,
        completed_at=COMPLETED,
    )

    assert artifact.classification is ValidationClassification.PASS
    assert artifact.evidence_class is EvidenceClass.MIXED
    assert artifact.physical_evidence_present

    with pytest.raises(ValueError, match="#509"):
        build_dry_run_validation_artifact(
            _procedure(),
            _all_checks(source=source),
            artifact_id="artifact-physical-dry-run",
            started_at=STARTED,
            completed_at=COMPLETED,
        )

    physical_clearance_procedure = replace(
        _procedure(),
        clearance=replace(_procedure().clearance, source=source),
    )
    mixed_artifact = build_validation_artifact(
        physical_clearance_procedure,
        _all_checks(),
        artifact_id="artifact-mixed-clearance",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    mixed_encoded = mixed_artifact.to_json_bytes()
    assert mixed_artifact.classification is ValidationClassification.PASS
    assert mixed_artifact.evidence_class is EvidenceClass.MIXED
    assert mixed_artifact.physical_evidence_present
    assert validate_validation_artifact(mixed_artifact).to_json_bytes() == mixed_encoded
    assert decode_validation_artifact(mixed_encoded).evidence_class is EvidenceClass.MIXED

    with pytest.raises(ValueError, match="#509"):
        build_dry_run_validation_artifact(
            physical_clearance_procedure,
            _all_checks(),
            artifact_id="artifact-physical-clearance-dry-run",
            started_at=STARTED,
            completed_at=COMPLETED,
        )

    document_source = MeasurementSource(MeasurementSourceKind.MANUFACTURER_DOCUMENT, "manual-001", "rev-001", "page-12")
    document_artifact = build_validation_artifact(
        _procedure(),
        _all_checks(source=document_source),
        artifact_id="artifact-document",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert document_artifact.evidence_class is EvidenceClass.MIXED
    with pytest.raises(ValueError, match="evidence_reference"):
        MeasurementSource(MeasurementSourceKind.MANUFACTURER_DOCUMENT, "manual-001", "rev-001")


@pytest.mark.parametrize("location", ("clearance", "check"))
def test_dry_run_raw_precheck_rejects_physical_source_before_malformed_procedure(
    location: str,
) -> None:
    procedure = _bypassed_procedure()
    checks: tuple[object, ...]
    if location == "clearance":
        object.__setattr__(
            procedure,
            "clearance",
            {"source": {"kind": MeasurementSourceKind.PHYSICAL_MEASUREMENT.value}},
        )
        checks = _all_checks()
    else:
        object.__setattr__(procedure, "clearance", object())
        checks = (
            {"measurement_source": {"kind": MeasurementSourceKind.PHYSICAL_MEASUREMENT.value}},
        )

    with pytest.raises(ValueError, match="#509"):
        build_dry_run_validation_artifact(
            procedure,
            checks,
            artifact_id=f"artifact-raw-physical-{location}",
            started_at=STARTED,
            completed_at=COMPLETED,
        )


def test_dry_run_raw_precheck_rejects_mixed_source_before_lifecycle() -> None:
    procedure = _bypassed_procedure()
    physical = MeasurementSource(
        MeasurementSourceKind.PHYSICAL_MEASUREMENT,
        "sensor-raw",
        "run-raw",
        "observation-raw",
    )
    checks = list(_all_checks())
    object.__setattr__(checks[0], "measurement_source", physical)

    with pytest.raises(ValueError, match="#509"):
        build_dry_run_validation_artifact(
            procedure,
            checks,
            artifact_id="artifact-raw-mixed",
            started_at=STARTED,
            completed_at=None,
        )


def test_safety_decision_evidence_requires_component_reason_identity_and_provenance() -> None:
    with pytest.raises(ValueError, match="component:reason_code"):
        SafetyDecisionEvidence(SafetyDecisionAction.ALLOW, "allow", ("fixture",))
    with pytest.raises(ValueError, match="projection component"):
        SafetyDecisionEvidence(SafetyDecisionAction.ALLOW, "fixture:allow", ("fixture",))
    with pytest.raises(ValueError, match="allow projection requires concrete provenance"):
        SafetyDecisionEvidence(SafetyDecisionAction.ALLOW, "limit:limit_resolution_authoritative", ())


def test_strict_decoder_rejects_unknown_duplicate_bom_and_nonfinite_values() -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-strict",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    document = artifact.to_dict()

    unknown = dict(document)
    unknown["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        decode_validation_artifact(unknown)

    duplicate = artifact.to_json_bytes().decode("utf-8").replace('"artifact_id":"artifact-strict"', '"artifact_id":"artifact-strict","artifact_id":"duplicate"', 1)
    with pytest.raises(ValueError, match="duplicate JSON field"):
        decode_validation_artifact(duplicate)

    with pytest.raises(ValueError, match="BOM"):
        decode_validation_artifact(b"\xef\xbb\xbf" + artifact.to_json_bytes())

    nonfinite = json.loads(artifact.to_json_bytes().decode("utf-8"))
    nonfinite["checks"][0]["expected"]["value"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        decode_validation_artifact(nonfinite)


@pytest.mark.parametrize("field_name", ["expected", "observed"])
@pytest.mark.parametrize("representation", ["mapping", "json_text", "json_bytes"])
def test_strict_decoder_rejects_huge_nested_integer_as_value_error(field_name: str, representation: str) -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-huge-integer",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    document = artifact.to_dict()
    document["checks"][0][field_name] = {"nested": {"value": 10**400}}
    if representation == "mapping":
        malformed: object = document
    else:
        encoded = json.dumps(document, separators=(",", ":"))
        malformed = encoded if representation == "json_text" else encoded.encode("utf-8")

    with pytest.raises(ValueError, match="finite"):
        decode_validation_artifact(malformed)


def test_check_contract_requires_status_specific_safety_action_and_observation() -> None:
    source = MeasurementSource(MeasurementSourceKind.SOFTWARE_DRY_RUN, "fixture", "revision-001")
    with pytest.raises(ValueError, match="observed_at"):
        ValidationCheckEvidence(
            "check",
            ValidationCheckKind.LIMIT_RANGE,
            ValidationCheckStatus.PASS,
            {"expected": 1},
            {"observed": 1},
            source,
            None,
            "revision-001",
            SafetyDecisionEvidence(
                SafetyDecisionAction.ALLOW,
                "limit:limit_resolution_authoritative",
                ("check", "fixture", "revision-001"),
            ),
            "missing timestamp",
        )

    with pytest.raises(ValueError, match="unavailable safety decision"):
        _check("check", ValidationCheckKind.LIMIT_RANGE, status=ValidationCheckStatus.UNAVAILABLE, action=SafetyDecisionAction.HOLD)


def test_pass_check_requires_non_empty_observed_evidence() -> None:
    source = MeasurementSource(MeasurementSourceKind.SOFTWARE_DRY_RUN, "fixture", "revision-001")
    with pytest.raises(ValueError, match="non-empty observed"):
        ValidationCheckEvidence(
            "check",
            ValidationCheckKind.LIMIT_RANGE,
            ValidationCheckStatus.PASS,
            {"expected": 1},
            {},
            source,
            OBSERVED,
            "revision-001",
            SafetyDecisionEvidence(
                SafetyDecisionAction.ALLOW,
                "limit:limit_resolution_authoritative",
                ("check", "fixture", "revision-001"),
            ),
            "observed evidence is empty",
        )


def test_unknown_source_cannot_be_pass_or_allow() -> None:
    unknown = MeasurementSource(
        MeasurementSourceKind.UNKNOWN,
        "unknown-source",
        "unknown-revision",
    )
    with pytest.raises(ValueError, match="cannot pass or allow"):
        _check("check", ValidationCheckKind.LIMIT_RANGE, source=unknown)


def test_unknown_pass_precedes_placeholder_projection_diagnostic() -> None:
    unknown = MeasurementSource(MeasurementSourceKind.UNKNOWN, "unknown", "unknown")
    decision = SafetyDecisionEvidence(
        SafetyDecisionAction.ALLOW,
        "limit:limit_resolution_authoritative",
        ("check", "fixture", "revision-001", "revision-other"),
    )

    with pytest.raises(ValueError, match="unknown measurement source cannot pass or allow"):
        ValidationCheckEvidence(
            "check",
            ValidationCheckKind.LIMIT_RANGE,
            ValidationCheckStatus.PASS,
            {"expected": 1},
            {"observed": 1},
            unknown,
            OBSERVED,
            "revision-001",
            decision,
            "unknown source",
        )


def test_safety_decision_provenance_rejects_placeholder_and_unrelated_evidence() -> None:
    with pytest.raises(ValueError, match="concrete identities"):
        SafetyDecisionEvidence(
            SafetyDecisionAction.ALLOW,
            "limit:limit_resolution_authoritative",
            ("unknown",),
        )

    check = _check("check", ValidationCheckKind.LIMIT_RANGE)
    unrelated = SafetyDecisionEvidence(
        SafetyDecisionAction.ALLOW,
        "limit:limit_resolution_authoritative",
        ("unrelated-check", "unrelated-source", "unrelated-revision", "revision-001"),
    )
    object.__setattr__(check, "safety_decision", unrelated)
    with pytest.raises(ValueError, match="bind to check and measurement evidence"):
        validate_validation_check_evidence(check)


def test_nested_measurement_source_bypass_is_revalidated() -> None:
    check = _check("check", ValidationCheckKind.LIMIT_RANGE)
    object.__setattr__(check.measurement_source, "source_id", "")

    with pytest.raises(ValueError, match="source_id"):
        validate_validation_check_evidence(check)


def test_nested_semantically_valid_source_mutation_is_rejected_by_external_seal() -> None:
    check = _check("check", ValidationCheckKind.LIMIT_RANGE)
    object.__setattr__(check.measurement_source, "source_id", "rewritten-fixture")

    with pytest.raises(ValueError, match="constructor-sealed"):
        validate_validation_check_evidence(check)


@pytest.mark.parametrize("nested_kind", ("source", "decision", "expected"))
def test_same_value_nested_replacement_is_rejected_by_check_seal(
    nested_kind: str,
) -> None:
    check = _check("check", ValidationCheckKind.LIMIT_RANGE)
    if nested_kind == "source":
        original = check.measurement_source
        replacement = MeasurementSource(
            original.kind,
            original.source_id,
            original.revision,
            original.evidence_reference,
        )
        object.__setattr__(check, "measurement_source", replacement)
    elif nested_kind == "decision":
        original = check.safety_decision
        replacement = SafetyDecisionEvidence(
            original.action,
            original.reason_identity,
            original.provenance,
        )
        object.__setattr__(check, "safety_decision", replacement)
    else:
        object.__setattr__(check, "expected", dict(check.expected))

    with pytest.raises(ValueError, match="constructor-sealed"):
        validate_validation_check_evidence(check)
    with pytest.raises(ValueError, match="constructor-sealed"):
        check.to_dict()


def test_check_revalidation_preserves_original_nested_objects() -> None:
    check = _check("check", ValidationCheckKind.LIMIT_RANGE)
    source = check.measurement_source
    decision = check.safety_decision

    validated = validate_validation_check_evidence(check)

    assert validated is check
    assert validated.measurement_source is source
    assert validated.safety_decision is decision


def test_clearance_source_same_value_constructor_bypass_is_rejected() -> None:
    procedure = _procedure()
    nested_source = object.__new__(MeasurementSource)
    object.__setattr__(nested_source, "kind", MeasurementSourceKind.SOFTWARE_DRY_RUN)
    object.__setattr__(nested_source, "source_id", "fixture")
    object.__setattr__(nested_source, "revision", "revision-001")
    object.__setattr__(nested_source, "evidence_reference", None)
    object.__setattr__(procedure.clearance, "source", nested_source)

    with pytest.raises(ValueError, match="constructor-sealed"):
        validate_validation_procedure(procedure)


def test_public_leaf_serializer_revalidates_its_external_seal() -> None:
    source = MeasurementSource(
        MeasurementSourceKind.SOFTWARE_DRY_RUN,
        "fixture",
        "revision-001",
    )
    object.__setattr__(source, "source_id", "rewritten-fixture")

    with pytest.raises(ValueError, match="constructor-sealed"):
        source.to_dict()


def test_decision_leaf_serializer_revalidates_its_external_seal() -> None:
    decision = SafetyDecisionEvidence(
        SafetyDecisionAction.ALLOW,
        "limit:limit_resolution_authoritative",
        ("check", "fixture", "revision-001"),
    )
    object.__setattr__(decision, "provenance", ("rewritten-fixture",))

    with pytest.raises(ValueError, match="constructor-sealed"):
        decision.to_dict()


def test_nested_constructor_bypass_is_revalidated() -> None:
    check = _check("check", ValidationCheckKind.LIMIT_RANGE)
    malformed_source = object.__new__(MeasurementSource)
    object.__setattr__(check, "measurement_source", malformed_source)

    with pytest.raises(ValueError, match="structurally incomplete"):
        validate_validation_check_evidence(check)


@pytest.mark.parametrize("nested_kind", ("source", "decision"))
def test_valid_looking_nested_constructor_bypass_is_not_authority(
    nested_kind: str,
) -> None:
    check = _check("check", ValidationCheckKind.LIMIT_RANGE)
    if nested_kind == "source":
        nested = object.__new__(MeasurementSource)
        object.__setattr__(nested, "kind", MeasurementSourceKind.SOFTWARE_DRY_RUN)
        object.__setattr__(nested, "source_id", "fixture")
        object.__setattr__(nested, "revision", "revision-001")
        object.__setattr__(nested, "evidence_reference", None)
        object.__setattr__(check, "measurement_source", nested)
    else:
        nested = object.__new__(SafetyDecisionEvidence)
        object.__setattr__(nested, "action", SafetyDecisionAction.ALLOW)
        object.__setattr__(nested, "reason_identity", "limit:limit_resolution_authoritative")
        object.__setattr__(nested, "provenance", ("check", "fixture", "revision-001"))
        object.__setattr__(check, "safety_decision", nested)

    with pytest.raises(ValueError, match="constructor-sealed"):
        validate_validation_check_evidence(check)


def test_public_check_and_nested_dtos_require_exact_types() -> None:
    base_check = _check("check", ValidationCheckKind.LIMIT_RANGE)

    class DerivedCheck(ValidationCheckEvidence):
        pass

    derived_check = object.__new__(DerivedCheck)
    for field_name in ValidationCheckEvidence.__dataclass_fields__:
        object.__setattr__(derived_check, field_name, getattr(base_check, field_name))
    with pytest.raises(TypeError, match="check must be ValidationCheckEvidence"):
        validate_validation_check_evidence(derived_check)

    class DerivedSource(MeasurementSource):
        pass

    with pytest.raises(TypeError, match="measurement_source must be MeasurementSource"):
        _check(
            "derived-source",
            ValidationCheckKind.LIMIT_RANGE,
            source=DerivedSource(
                MeasurementSourceKind.SOFTWARE_DRY_RUN,
                "fixture",
                "revision-001",
            ),
        )

    class DerivedDecision(SafetyDecisionEvidence):
        pass

    derived_decision = DerivedDecision(
        SafetyDecisionAction.ALLOW,
        "limit:limit_resolution_authoritative",
        ("check", "fixture", "revision-001"),
    )
    with pytest.raises(TypeError, match="safety_decision must be SafetyDecisionEvidence"):
        ValidationCheckEvidence(
            check_id=base_check.check_id,
            kind=base_check.kind,
            status=base_check.status,
            expected=base_check.expected,
            observed=base_check.observed,
            measurement_source=base_check.measurement_source,
            observed_at=base_check.observed_at,
            software_revision=base_check.software_revision,
            safety_decision=derived_decision,
            reason=base_check.reason,
        )


def test_nested_safety_decision_bypass_is_revalidated() -> None:
    check = _check("check", ValidationCheckKind.LIMIT_RANGE)
    object.__setattr__(check.safety_decision, "provenance", ())

    with pytest.raises(ValueError, match="non-empty tuple"):
        validate_validation_check_evidence(check)


def test_classifier_rejects_nested_bypass_without_pass() -> None:
    checks = list(_all_checks())
    object.__setattr__(checks[0].measurement_source, "source_id", "")

    artifact = build_dry_run_validation_artifact(
        _procedure(),
        checks,
        artifact_id="artifact-nested-bypass",
        started_at=STARTED,
        completed_at=COMPLETED,
    )

    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert artifact.evidence_class is EvidenceClass.UNKNOWN


@pytest.mark.parametrize("mutation", ("expected", "observed", "reason", "provenance", "whole_check"))
def test_artifact_boundary_rejects_constructor_bypassed_check(
    mutation: str,
) -> None:
    checks = list(_all_checks())
    if mutation == "expected":
        object.__setattr__(checks[0], "expected", {})
    elif mutation == "observed":
        object.__setattr__(checks[0], "observed", {})
    elif mutation == "reason":
        object.__setattr__(checks[0], "reason", "")
    elif mutation == "provenance":
        object.__setattr__(checks[0].safety_decision, "provenance", ())
    else:
        checks[0] = object.__new__(ValidationCheckEvidence)

    try:
        artifact = build_dry_run_validation_artifact(
            _procedure(),
            checks,
            artifact_id=f"artifact-bypassed-{mutation}",
            started_at=STARTED,
            completed_at=COMPLETED,
        )
    except (TypeError, ValueError):
        return

    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert not artifact.complete


def test_artifact_complete_rechecks_nested_evidence_after_mutation() -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-mutated-nested",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    object.__setattr__(artifact.checks[0].safety_decision, "action", SafetyDecisionAction.STOP)

    assert not artifact.complete


def test_strict_decode_reconstructs_nested_measurement_source() -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-nested-decode",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    document = artifact.to_dict()
    document["checks"][0]["measurement_source"]["source_id"] = ""

    with pytest.raises(ValueError, match="source_id"):
        decode_validation_artifact(document)


def test_artifact_lifecycle_rejects_reversed_timestamps_and_boolean_schema_version() -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-lifecycle",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    with pytest.raises(ValueError, match="completed_at must not precede started_at"):
        build_dry_run_validation_artifact(
            _procedure(),
            _all_checks(),
            artifact_id="artifact-reversed",
            started_at=COMPLETED,
            completed_at=STARTED,
        )

    malformed = artifact.to_dict()
    malformed["schema_version"] = True
    with pytest.raises(ValueError, match="schema version"):
        decode_validation_artifact(malformed)


def _bypassed_procedure() -> ValidationProcedure:
    procedure = object.__new__(ValidationProcedure)
    valid = _procedure()
    for field_name in ValidationProcedure.__dataclass_fields__:
        object.__setattr__(procedure, field_name, getattr(valid, field_name))
    return procedure


@pytest.mark.parametrize(
    "mutation",
    (
        "target_type",
        "target_field",
        "required_type",
        "missing_kind",
        "duplicate_id",
        "operator_confirmed_type",
        "dry_run_only",
        "metadata",
        "nested_spec",
    ),
)
def test_validation_procedure_canonical_validator_rejects_bypassed_fields(
    mutation: str,
) -> None:
    procedure = _bypassed_procedure()
    if mutation == "target_type":
        object.__setattr__(procedure, "target", object())
    elif mutation == "target_field":
        object.__setattr__(procedure.target, "robot_id", "")
    elif mutation == "required_type":
        object.__setattr__(procedure, "required_checks", object())
    elif mutation == "missing_kind":
        object.__setattr__(
            procedure,
            "required_checks",
            (ValidationCheckSpec("limits", ValidationCheckKind.LIMIT_RANGE, "range check"),),
        )
    elif mutation == "duplicate_id":
        object.__setattr__(
            procedure,
            "required_checks",
            procedure.required_checks + (procedure.required_checks[0],),
        )
    elif mutation == "operator_confirmed_type":
        object.__setattr__(procedure, "operator_confirmed", 1)
    elif mutation == "dry_run_only":
        object.__setattr__(procedure, "dry_run_only", False)
    elif mutation == "metadata":
        object.__setattr__(procedure, "created_at", "")
    else:
        malformed_spec = object.__new__(ValidationCheckSpec)
        object.__setattr__(malformed_spec, "check_id", "")
        object.__setattr__(malformed_spec, "kind", ValidationCheckKind.LIMIT_RANGE)
        object.__setattr__(malformed_spec, "description", "range check")
        object.__setattr__(
            procedure,
            "required_checks",
            (malformed_spec, *procedure.required_checks[1:]),
        )

    with pytest.raises((TypeError, ValueError)):
        validate_validation_procedure(procedure)

    artifact = build_dry_run_validation_artifact(
        procedure,
        _all_checks(),
        artifact_id=f"artifact-malformed-procedure-{mutation}",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert not artifact.complete


def test_procedure_external_seal_rejects_semantically_valid_nested_mutation() -> None:
    procedure = _procedure()
    object.__setattr__(procedure.target, "robot_id", "rewritten-robot")

    with pytest.raises(ValueError, match="constructor-sealed"):
        validate_validation_procedure(procedure)


@pytest.mark.parametrize("field_name", ("target", "required_checks"))
def test_procedure_external_seal_rejects_same_value_nested_replacement(
    field_name: str,
) -> None:
    procedure = _procedure()
    if field_name == "target":
        original = procedure.target
        replacement = TargetIdentity(
            original.target_id,
            original.robot_id,
            original.controller_id,
            original.connection_id,
            original.model_id,
        )
    else:
        replacement = tuple(item for item in procedure.required_checks)
        assert replacement is not procedure.required_checks
    object.__setattr__(procedure, field_name, replacement)

    with pytest.raises(ValueError, match="constructor-sealed"):
        validate_validation_procedure(procedure)
    with pytest.raises(ValueError, match="constructor-sealed"):
        procedure.to_dict()


def test_valid_looking_nested_procedure_bypass_is_not_authority() -> None:
    procedure = _procedure()
    nested_target = object.__new__(TargetIdentity)
    for field_name in TargetIdentity.__dataclass_fields__:
        object.__setattr__(nested_target, field_name, getattr(procedure.target, field_name))
    object.__setattr__(procedure, "target", nested_target)

    with pytest.raises(ValueError, match="constructor-sealed"):
        validate_validation_procedure(procedure)


def test_whole_artifact_bypass_cannot_complete_or_serialize_as_pass() -> None:
    valid = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-whole-bypass",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    malformed = object.__new__(ValidationEvidenceArtifact)
    for field_name in ValidationEvidenceArtifact.__dataclass_fields__:
        object.__setattr__(malformed, field_name, getattr(valid, field_name))
    malformed_procedure = _bypassed_procedure()
    object.__setattr__(malformed_procedure, "operator_confirmed", 1)
    object.__setattr__(malformed, "procedure", malformed_procedure)

    assert not malformed.complete
    with pytest.raises((TypeError, ValueError)):
        malformed.to_json_bytes()
    with pytest.raises((TypeError, ValueError)):
        validate_validation_artifact(malformed)


def test_artifact_external_seal_rejects_coherent_private_fingerprint_rewrite() -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id="artifact-external-seal",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    object.__setattr__(artifact, "artifact_id", "artifact-rewritten")
    object.__setattr__(
        artifact,
        "_binding_fingerprint",
        (
            artifact.artifact_id,
            id(artifact.procedure),
            artifact.started_at,
            artifact.completed_at,
            artifact.classification,
            artifact.classification_reason,
            tuple(id(item) for item in artifact.checks),
            artifact.operator_aborted,
            artifact.schema_version,
        ),
    )

    assert not artifact.complete
    with pytest.raises(ValueError, match="constructor-sealed"):
        artifact.to_json_bytes()


@pytest.mark.parametrize("replacement", ("checks_tuple", "check"))
def test_artifact_external_seal_rejects_same_value_nested_replacement(
    replacement: str,
) -> None:
    artifact = build_dry_run_validation_artifact(
        _procedure(),
        _all_checks(),
        artifact_id=f"artifact-same-value-{replacement}",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    if replacement == "checks_tuple":
        checks = tuple(item for item in artifact.checks)
        assert checks is not artifact.checks
    else:
        original = artifact.checks[0]
        replacement_check = ValidationCheckEvidence(
            original.check_id,
            original.kind,
            original.status,
            original.expected,
            original.observed,
            original.measurement_source,
            original.observed_at,
            original.software_revision,
            original.safety_decision,
            original.reason,
        )
        checks = (replacement_check, *artifact.checks[1:])
    object.__setattr__(artifact, "checks", checks)

    assert not artifact.complete
    assert artifact.evidence_class is EvidenceClass.UNKNOWN
    with pytest.raises(ValueError, match="constructor-sealed"):
        artifact.to_json_bytes()


def test_classifier_validates_malformed_checks_before_abort_short_circuit() -> None:
    malformed = object.__new__(ValidationCheckEvidence)
    checks = list(_all_checks())
    checks[0] = malformed
    artifact = build_validation_artifact(
        _procedure(),
        tuple(checks),
        artifact_id="artifact-malformed-before-abort",
        started_at=STARTED,
        completed_at=None,
        operator_aborted=True,
    )

    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert artifact.classification_reason == "check_evidence_invalid"


def test_classifier_validates_duplicate_ids_before_abort_short_circuit() -> None:
    checks = list(_all_checks())
    object.__setattr__(checks[1], "check_id", checks[0].check_id)
    artifact = build_validation_artifact(
        _procedure(),
        tuple(checks),
        artifact_id="artifact-duplicate-before-abort",
        started_at=STARTED,
        completed_at=None,
        operator_aborted=True,
    )

    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert artifact.classification_reason == "check_identity_invalid"


def test_classifier_rejects_unexpected_id_before_abort_and_missing_completion() -> None:
    checks = list(_all_checks())
    object.__setattr__(checks[0], "check_id", "unexpected-check")

    artifact = build_validation_artifact(
        _procedure(),
        tuple(checks),
        artifact_id="artifact-unexpected-before-lifecycle",
        started_at=STARTED,
        completed_at=None,
        operator_aborted=True,
    )

    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert artifact.classification_reason == "check_identity_invalid"


def test_duplicate_actual_check_ids_are_technical_invalid_even_when_checks_are_invalid() -> None:
    checks = list(
        _all_checks(
            status=ValidationCheckStatus.TECHNICAL_INVALID,
            action=SafetyDecisionAction.INVALID,
        )
    )
    object.__setattr__(checks[1], "check_id", checks[0].check_id)

    artifact = build_validation_artifact(
        _procedure(),
        tuple(checks),
        artifact_id="artifact-duplicate-technical-checks",
        started_at=STARTED,
        completed_at=COMPLETED,
    )

    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert artifact.classification_reason == "check_identity_invalid"
    assert not artifact.complete


def test_classifier_keeps_technical_invalid_before_unknown_source() -> None:
    checks = _all_checks(
        status=ValidationCheckStatus.TECHNICAL_INVALID,
        action=SafetyDecisionAction.INVALID,
        source=MeasurementSource(
            MeasurementSourceKind.UNKNOWN,
            "unknown-technical-source",
            "unknown-technical-revision",
        ),
    )

    artifact = build_dry_run_validation_artifact(
        _procedure(),
        checks,
        artifact_id="artifact-technical-before-unknown",
        started_at=STARTED,
        completed_at=COMPLETED,
    )

    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID
    assert artifact.classification_reason == "technical_invalid_check"
    assert artifact.evidence_class is EvidenceClass.UNKNOWN


def test_strict_decode_rejects_duplicate_actual_check_ids_even_when_technical_invalid() -> None:
    checks = list(
        _all_checks(
            status=ValidationCheckStatus.TECHNICAL_INVALID,
            action=SafetyDecisionAction.INVALID,
        )
    )
    object.__setattr__(checks[1], "check_id", checks[0].check_id)
    artifact = build_validation_artifact(
        _procedure(),
        tuple(checks),
        artifact_id="artifact-duplicate-strict-decode",
        started_at=STARTED,
        completed_at=COMPLETED,
    )
    assert artifact.classification is ValidationClassification.TECHNICAL_INVALID

    with pytest.raises(ValueError, match="check IDs must be unique"):
        decode_validation_artifact(artifact.to_json_bytes())
