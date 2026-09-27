from __future__ import annotations

import json
from enum import Enum

import pytest

from xpotato_sim.runtime.safety.physical_limits import (
    EvidenceStatus,
    LimitConversionProvenance,
    LimitQuantity,
    LimitSourceProvenance,
    LimitSpace,
    PhysicalLimit,
    PhysicalSafetyEnvelope,
    classify_source_status,
    effective_limit_status,
    make_unknown_limit,
    validate_limit_conversion,
    validate_limit_source,
    validate_physical_limit,
    validate_envelope,
)


def _source(
    *,
    kind: str = "lab_document",
    status: EvidenceStatus = EvidenceStatus.AUTHORITATIVE,
    evidence_reference: str | None = "lab-record-001",
    source_id: str = "fast-arm-limit-sheet",
    revision: str = "rev-1",
) -> LimitSourceProvenance:
    return LimitSourceProvenance(
        source_kind=kind,
        source_id=source_id,
        revision=revision,
        status=status,
        evidence_reference=evidence_reference,
    )


def _joint_limit(
    *,
    name: str = "joint_1",
    status: EvidenceStatus = EvidenceStatus.AUTHORITATIVE,
    source: LimitSourceProvenance | None = None,
    lower: float | None = -1.0,
    upper: float | None = 1.0,
    conversion: LimitConversionProvenance | None = None,
) -> PhysicalLimit:
    return PhysicalLimit(
        name=name,
        quantity=LimitQuantity.POSITION,
        lower=lower,
        upper=upper,
        unit="rad",
        space=LimitSpace.JOINT,
        frame="fast_arm joint space",
        status=status,
        source=source or _source(status=status),
        conversion=conversion or LimitConversionProvenance.identity(LimitSpace.JOINT),
    )


class _OverridingIdentity(str):
    """casefold/stripをoverrideしてvalidatorの型境界を検証する。"""

    def strip(self, chars: str | None = None) -> str:
        raise AssertionError("identity validator must reject before strip")

    def casefold(self) -> str:
        raise AssertionError("identity validator must reject before casefold")


class _SpoofedFloat(float):
    def __new__(cls, raw: float, coerced: float) -> "_SpoofedFloat":
        value = float.__new__(cls, raw)
        value._coerced = coerced
        return value

    def __float__(self) -> float:
        return self._coerced


class _StatefulTuple(tuple[object, ...]):
    def __new__(
        cls,
        first: tuple[object, ...],
        later: tuple[object, ...],
    ) -> "_StatefulTuple":
        value = tuple.__new__(cls, first)
        value.iteration_count = 0
        value.later = later
        return value

    def __iter__(self):
        self.iteration_count += 1
        return iter(tuple.__iter__(self) if self.iteration_count == 1 else self.later)


def _forged_enum_member(
    enum_type: type[Enum],
    *,
    name: str,
    value: str,
    raw_value: str | None = None,
) -> Enum:
    """constructorとstored validatorを試す偽造str enum memberを作る。"""

    forged = str.__new__(enum_type, value if raw_value is None else raw_value)
    object.__setattr__(forged, "_name_", name)
    object.__setattr__(forged, "_value_", value)
    return forged


@pytest.mark.parametrize(
    "identity",
    (
        "unknown",
        "UNKNOWN",
        "unavailable",
        "UNAVAILABLE",
        "n/a",
        "N/A",
        "none",
        "null",
        "placeholder",
        "sample",
        "synthetic",
        "fixture",
        "test_fixture",
        "fixture_data",
        "n_a",
        "not_available",
        "not-applicable",
    ),
)
def test_physical_limit_name_requires_concrete_identity(identity: str) -> None:
    with pytest.raises(ValueError, match="concrete identity"):
        _joint_limit(name=identity)


def test_physical_limit_identity_requires_builtin_str_before_overrides() -> None:
    identity = _OverridingIdentity("unknown")

    with pytest.raises(ValueError, match="built-in string"):
        _joint_limit(name=identity)

    limit = _joint_limit()
    object.__setattr__(limit, "name", identity)
    with pytest.raises(ValueError, match="built-in string"):
        validate_physical_limit(limit)

    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )
    with pytest.raises(ValueError, match="built-in string"):
        envelope.limit_for(identity)


@pytest.mark.parametrize("field", ("source_id", "revision", "evidence_reference"))
def test_authoritative_source_identity_fields_reject_str_subclass(
    field: str,
) -> None:
    identity = _OverridingIdentity("unknown")

    with pytest.raises(ValueError, match="non-empty string"):
        _source(**{field: identity})


def test_authority_classification_rejects_str_subclass_reference() -> None:
    identity = _OverridingIdentity("unknown")

    with pytest.raises(ValueError, match="non-empty string"):
        classify_source_status(
            source_kind="manufacturer_document",
            evidence_reference=identity,
            authority_asserted=True,
        )


def test_typed_evidence_status_rejects_str_subclass_before_enum_conversion() -> None:
    class ExplodingStatus(str):
        def strip(self, chars: str | None = None) -> str:
            raise AssertionError("status boundary must reject before strip")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("status boundary must reject before equality")

        def __ne__(self, other: object) -> bool:
            raise AssertionError("status boundary must reject before inequality")

        def __hash__(self) -> int:
            raise AssertionError("status boundary must reject before hashing")

    with pytest.raises(TypeError, match="must be a string"):
        _source(status=ExplodingStatus("authoritative"))  # type: ignore[arg-type]


def test_physical_enum_constructors_normalize_builtin_strings() -> None:
    source = _source(status="provisional")  # type: ignore[arg-type]
    assert source.status is EvidenceStatus.PROVISIONAL

    conversion = LimitConversionProvenance(
        source_space="joint",  # type: ignore[arg-type]
        target_space="joint",  # type: ignore[arg-type]
        method="identity",
        relation_id="identity:joint",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
    )
    assert conversion.source_space is LimitSpace.JOINT
    assert conversion.target_space is LimitSpace.JOINT

    limit = PhysicalLimit(
        name="joint_1",
        quantity="position",  # type: ignore[arg-type]
        lower=-1.0,
        upper=1.0,
        unit="rad",
        space="joint",  # type: ignore[arg-type]
        frame="fast_arm joint space",
        status="provisional",  # type: ignore[arg-type]
        source=source,
    )
    assert limit.quantity is LimitQuantity.POSITION
    assert limit.space is LimitSpace.JOINT
    assert limit.status is EvidenceStatus.PROVISIONAL


def test_forged_quantity_member_is_rejected_at_constructor_and_deep_validator() -> None:
    spoof = _forged_enum_member(
        LimitQuantity,
        name="VELOCITY",
        value="velocity",
        raw_value="position",
    )
    assert isinstance(spoof, LimitQuantity)
    assert spoof is not LimitQuantity.VELOCITY
    assert spoof == LimitQuantity.POSITION

    with pytest.raises(TypeError, match="canonical"):
        PhysicalLimit(
            name="joint_1",
            quantity=spoof,  # type: ignore[arg-type]
            lower=-1.0,
            upper=1.0,
            unit="rad",
            space=LimitSpace.JOINT,
            frame="fast_arm joint space",
            status=EvidenceStatus.PROVISIONAL,
            source=_source(status=EvidenceStatus.PROVISIONAL),
        )

    limit = _joint_limit()
    object.__setattr__(limit, "quantity", spoof)
    assert not limit.is_authoritative
    with pytest.raises(TypeError, match="canonical"):
        validate_physical_limit(limit)
    with pytest.raises(TypeError, match="canonical"):
        limit.to_dict()


@pytest.mark.parametrize(
    ("kind", "field", "enum_type", "raw", "forged"),
    (
        (
            "source",
            "status",
            EvidenceStatus,
            "provisional",
            _forged_enum_member(
                EvidenceStatus,
                name="AUTHORITATIVE",
                value="authoritative",
            ),
        ),
        (
            "conversion",
            "source_space",
            LimitSpace,
            "joint",
            _forged_enum_member(LimitSpace, name="MOTOR", value="motor"),
        ),
        (
            "conversion",
            "target_space",
            LimitSpace,
            "joint",
            _forged_enum_member(LimitSpace, name="MOTOR", value="motor"),
        ),
        (
            "limit",
            "quantity",
            LimitQuantity,
            "position",
            _forged_enum_member(LimitQuantity, name="VELOCITY", value="velocity"),
        ),
        (
            "limit",
            "space",
            LimitSpace,
            "joint",
            _forged_enum_member(LimitSpace, name="MOTOR", value="motor"),
        ),
        (
            "limit",
            "status",
            EvidenceStatus,
            "provisional",
            _forged_enum_member(
                EvidenceStatus,
                name="AUTHORITATIVE",
                value="authoritative",
            ),
        ),
    ),
)
def test_physical_stored_enum_fields_reject_raw_strings_and_forged_members(
    kind: str,
    field: str,
    enum_type: type[Enum],
    raw: str,
    forged: Enum,
) -> None:
    assert type(forged) is enum_type
    if kind == "source":
        value: object = _source(status=EvidenceStatus.PROVISIONAL)
        validator = validate_limit_source
    elif kind == "conversion":
        value = LimitConversionProvenance.identity(LimitSpace.JOINT)
        validator = validate_limit_conversion
    else:
        value = _joint_limit()
        validator = validate_physical_limit

    for bad in (raw, forged):
        object.__setattr__(value, field, bad)
        with pytest.raises(TypeError, match="canonical"):
            validator(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "identity",
    ("unknown", "UNKNOWN", "unavailable", "N/A", "none", "fixture_data"),
)
def test_bypassed_limit_name_cannot_reach_validation_or_nested_envelope(
    identity: str,
) -> None:
    limit = _joint_limit()
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(limit,),
    )
    object.__setattr__(limit, "name", identity)

    assert not limit.is_authoritative
    with pytest.raises(ValueError, match="concrete identity"):
        validate_physical_limit(limit)
    with pytest.raises(ValueError, match="concrete identity"):
        envelope.to_json_bytes()
    with pytest.raises(ValueError, match="concrete identity"):
        validate_envelope(envelope)


@pytest.mark.parametrize(
    "identity",
    ("unknown", "UNKNOWN", "unavailable", "n/a", "placeholder", "test_fixture"),
)
def test_envelope_decoder_rejects_placeholder_nested_limit_name(
    identity: str,
) -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )
    raw = json.loads(envelope.to_json_bytes())
    raw["limits"][0]["name"] = identity

    with pytest.raises(ValueError, match="concrete identity"):
        PhysicalSafetyEnvelope.from_json_bytes(
            json.dumps(raw, separators=(",", ":")).encode("utf-8")
        )


@pytest.mark.parametrize(
    "identity",
    ("unknown", "UNKNOWN", "unavailable", "N/A", "none", "fixture_data"),
)
def test_envelope_lookup_rejects_placeholder_query(identity: str) -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )

    with pytest.raises(ValueError, match="concrete identity"):
        envelope.limit_for(identity)


def test_authoritative_limit_requires_explicit_physical_provenance() -> None:
    limit = _joint_limit()

    assert limit.is_authoritative
    assert limit.source.is_physical_evidence
    assert limit.conversion is not None
    assert limit.conversion.method == "identity"


def test_software_sources_cannot_be_marked_authoritative() -> None:
    with pytest.raises(ValueError, match="software-only"):
        _source(kind="joint_limit_toml")


@pytest.mark.parametrize("authority_asserted", ("false", 1))
def test_source_classification_requires_exact_bool_authority_assertion(
    authority_asserted: object,
) -> None:
    with pytest.raises(TypeError, match="authority_asserted must be bool"):
        classify_source_status(
            source_kind="manufacturer_document",
            evidence_reference="record-1",
            authority_asserted=authority_asserted,  # type: ignore[arg-type]
        )


def test_source_classification_rejects_placeholder_authority_reference() -> None:
    with pytest.raises(ValueError, match="concrete identities"):
        classify_source_status(
            source_kind="manufacturer_document",
            evidence_reference="unknown",
            authority_asserted=True,
        )


def test_source_classification_rejects_synthetic_authority_kind() -> None:
    with pytest.raises(ValueError, match="synthetic"):
        classify_source_status(
            source_kind="fixture",
            evidence_reference="record-1",
            authority_asserted=True,
        )


def test_source_classification_rejects_synthetic_authority_without_reference() -> None:
    with pytest.raises(ValueError, match="synthetic"):
        classify_source_status(
            source_kind="fixture",
            evidence_reference=None,
            authority_asserted=True,
        )


def test_source_classification_rejects_whitespace_reference() -> None:
    with pytest.raises(ValueError, match="evidence_reference"):
        classify_source_status(
            source_kind="manufacturer_document",
            evidence_reference=" record-1 ",
            authority_asserted=True,
        )


def test_source_kind_must_use_canonical_lowercase_underscore_identity() -> None:
    with pytest.raises(ValueError, match="canonical lowercase underscore"):
        _source(kind="JOINT_LIMIT_TOML")


def test_authoritative_source_rejects_synthetic_source_kind() -> None:
    with pytest.raises(ValueError, match="synthetic"):
        _source(kind="fixture")


@pytest.mark.parametrize(
    "source_kind",
    ("test_fixture", "fixture_data", "simulation_snapshot", "unknown_source"),
)
def test_authoritative_source_kind_uses_narrow_allowlist(
    source_kind: str,
) -> None:
    with pytest.raises(ValueError, match="approved|authoritative"):
        _source(kind=source_kind)


def test_authoritative_source_rejects_whitespace_evidence_reference() -> None:
    with pytest.raises(ValueError, match="evidence_reference"):
        _source(evidence_reference=" record-1 ")


@pytest.mark.parametrize("field_name", ("source_id", "revision", "evidence_reference"))
def test_authoritative_source_rejects_placeholder_identity(field_name: str) -> None:
    values: dict[str, object] = {
        "source_id": "fast-arm-limit-sheet",
        "revision": "rev-1",
        "evidence_reference": "lab-record-001",
    }
    values[field_name] = "unknown"

    with pytest.raises(ValueError, match="concrete identities"):
        _source(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("source_kind", ("manufacturer_document", "physical_measurement"))
def test_concrete_physical_authority_remains_valid(source_kind: str) -> None:
    source = _source(kind=source_kind)

    assert source.status is EvidenceStatus.AUTHORITATIVE
    assert source.is_physical_evidence


def test_missing_physical_source_is_typed_unknown_and_not_bounded() -> None:
    limit = make_unknown_limit(
        name="elbow_joint",
        quantity=LimitQuantity.POSITION,
        space=LimitSpace.JOINT,
        unit="rad",
        frame="fast_arm joint space",
        reason="manufacturer range has not been supplied",
    )

    assert limit.status is EvidenceStatus.UNKNOWN
    assert not limit.is_bounded
    assert not limit.is_authoritative


def test_envelope_serialization_is_deterministic_and_round_trips() -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
        source_summary="explicit lab evidence fixture",
    )

    encoded = envelope.to_json_bytes()
    assert encoded == envelope.to_json_bytes()
    assert not encoded.startswith(b"\xef\xbb\xbf")
    assert PhysicalSafetyEnvelope.from_json_bytes(encoded) == envelope
    assert validate_envelope(envelope) is envelope


def test_envelope_requires_non_empty_limits() -> None:
    with pytest.raises(ValueError, match="limits must be non-empty"):
        PhysicalSafetyEnvelope(
            envelope_id="empty",
            envelope_version=1,
            robot_id="fast_arm",
            model_id="fast_arm",
            limits=(),
        )


@pytest.mark.parametrize(
    "identity",
    ("unknown", "unavailable", "none", "fixture_data"),
)
def test_envelope_robot_id_requires_concrete_identity_in_constructor_and_json(
    identity: str,
) -> None:
    with pytest.raises(ValueError, match="concrete identity"):
        PhysicalSafetyEnvelope(
            envelope_id="fast_arm_physical_limits",
            envelope_version=1,
            robot_id=identity,
            model_id="fast_arm",
            limits=(_joint_limit(),),
        )

    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )
    raw = json.loads(envelope.to_json_bytes())
    raw["robot_id"] = identity
    with pytest.raises(ValueError, match="concrete identity"):
        PhysicalSafetyEnvelope.from_json_bytes(
            json.dumps(raw, separators=(",", ":")).encode("utf-8")
        )


def test_envelope_stored_limits_require_builtin_tuple_before_iteration() -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )
    stateful_limits = _StatefulTuple(
        envelope.limits,
        (_joint_limit(name="joint_2"),),
    )
    object.__setattr__(envelope, "limits", stateful_limits)

    with pytest.raises(TypeError, match="built-in tuple"):
        validate_envelope(envelope)
    assert stateful_limits.iteration_count == 0


def test_physical_limit_validator_rejects_spoofed_stored_float_before_float_hook() -> None:
    limit = _joint_limit()
    object.__setattr__(limit, "lower", _SpoofedFloat(999.0, -1.0))

    assert not limit.is_authoritative
    with pytest.raises(TypeError, match="canonical float"):
        validate_physical_limit(limit)
    with pytest.raises(TypeError, match="canonical float"):
        limit.to_dict()


@pytest.mark.parametrize("field", ("gear_ratio", "sign", "offset"))
def test_conversion_validator_rejects_spoofed_stored_float_before_float_hook(
    field: str,
) -> None:
    conversion = LimitConversionProvenance.identity(LimitSpace.JOINT)
    object.__setattr__(conversion, field, _SpoofedFloat(999.0, 1.0))

    with pytest.raises(TypeError, match="canonical float"):
        validate_limit_conversion(conversion)


def test_envelope_boundary_rejects_nested_replacement_and_bypass() -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )
    replacement = _joint_limit()
    object.__setattr__(envelope, "limits", (replacement,))
    with pytest.raises(ValueError, match="mutated or bypassed"):
        envelope.to_dict()
    with pytest.raises(ValueError, match="mutated or bypassed"):
        validate_envelope(envelope)

    bypassed = object.__new__(PhysicalSafetyEnvelope)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        bypassed.to_json_bytes()


def test_projected_limit_envelope_round_trips_through_canonical_decoder() -> None:
    raw = {
        "schema_version": 1,
        "envelope_id": "fast_arm_projected_limits",
        "envelope_version": 1,
        "robot_id": "fast_arm",
        "model_id": "fast_arm",
        "limits": [
            {
                "name": "joint_1",
                "quantity": "position",
                "lower": -1.0,
                "upper": 1.0,
                "unit": "rad",
                "space": "joint",
                "frame": "fast_arm joint space",
                "status": "authoritative",
                "source": _source().to_dict(),
                "conversion": {
                    "source_space": "motor",
                    "target_space": "joint",
                    "method": "joint = sign * source / gear_ratio + offset",
                    "relation_id": "motor_1-to-joint_1/v1",
                    "gear_ratio": 2.0,
                    "sign": -1.0,
                    "offset": 0.25,
                    "source_name": "motor_1",
                },
            }
        ],
    }
    encoded = json.dumps(raw, separators=(",", ":")).encode("utf-8")
    envelope = PhysicalSafetyEnvelope.from_json_bytes(encoded)
    projected = envelope.limits[0]
    assert raw["limits"][0]["conversion"]["source_name"] == "motor_1"
    assert projected.conversion is not None
    assert projected.conversion.source_space is LimitSpace.MOTOR
    assert PhysicalSafetyEnvelope.from_json_bytes(envelope.to_json_bytes()) == envelope
    del raw["limits"][0]["conversion"]["source_name"]
    with pytest.raises(ValueError, match="source_name"):
        PhysicalSafetyEnvelope.from_json_bytes(
            json.dumps(raw, separators=(",", ":")).encode("utf-8")
        )


@pytest.mark.parametrize(
    ("field_name", "malformed_value"),
    (("gear_ratio", True), ("sign", True), ("offset", False)),
)
def test_envelope_decoder_rejects_json_booleans_in_conversion_numbers(
    field_name: str,
    malformed_value: bool,
) -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fixture",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )
    raw = json.loads(envelope.to_json_bytes())
    raw["limits"][0]["conversion"][field_name] = malformed_value

    with pytest.raises(TypeError, match=field_name):
        PhysicalSafetyEnvelope.from_json_bytes(
            json.dumps(raw, separators=(",", ":")).encode("utf-8")
        )


def test_public_p2_validators_reject_subclass_bypasses() -> None:
    class SourceSubclass(LimitSourceProvenance):
        pass

    class ConversionSubclass(LimitConversionProvenance):
        pass

    class LimitSubclass(PhysicalLimit):
        pass

    class EnvelopeSubclass(PhysicalSafetyEnvelope):
        pass

    for dto_type, validator in (
        (SourceSubclass, validate_limit_source),
        (ConversionSubclass, validate_limit_conversion),
        (LimitSubclass, validate_physical_limit),
        (EnvelopeSubclass, validate_envelope),
    ):
        with pytest.raises(TypeError):
            validator(object.__new__(dto_type))


def test_envelope_rejects_unknown_fields_and_bom() -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fixture",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )
    raw = json.loads(envelope.to_json_bytes())
    raw["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        PhysicalSafetyEnvelope.from_json_bytes(
            json.dumps(raw, separators=(",", ":")).encode("utf-8")
        )
    with pytest.raises(ValueError, match="BOM"):
        PhysicalSafetyEnvelope.from_json_bytes(b"\xef\xbb\xbf" + envelope.to_json_bytes())


def test_envelope_decoder_rejects_bytes_subclass_before_overrides() -> None:
    class ExplodingBytes(bytes):
        def startswith(
            self,
            prefix: bytes,
            start: int = 0,
            end: int | None = None,
        ) -> bool:
            raise AssertionError("bytes decoder must reject before startswith")

        def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
            raise AssertionError("bytes decoder must reject before decode")

    with pytest.raises(TypeError, match="bytes"):
        PhysicalSafetyEnvelope.from_json_bytes(ExplodingBytes(b"\xef\xbb\xbfnot-json"))


def test_envelope_decoder_rejects_spoofed_bytes_subclass_with_valid_decode() -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fast_arm_physical_limits",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )
    calls: list[str] = []

    class SpoofedBytes(bytes):
        def startswith(
            self,
            prefix: bytes,
            start: int = 0,
            end: int | None = None,
        ) -> bool:
            calls.append("startswith")
            return False

        def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
            calls.append("decode")
            return envelope.to_json_bytes().decode("utf-8")

    with pytest.raises(TypeError, match="bytes"):
        PhysicalSafetyEnvelope.from_json_bytes(SpoofedBytes(b"\xef\xbb\xbfnot-json"))
    assert calls == []


def test_invalid_and_conflicting_values_do_not_become_authoritative() -> None:
    with pytest.raises(ValueError, match="finite"):
        PhysicalLimit(
            name="joint_1",
            quantity=LimitQuantity.POSITION,
            lower=float("nan"),
            upper=1.0,
            unit="rad",
            space=LimitSpace.JOINT,
            frame="fast_arm joint space",
            status=EvidenceStatus.INVALID,
            source=_source(status=EvidenceStatus.INVALID, evidence_reference=None),
            reason="source values were inconsistent",
        )

    conflict = PhysicalLimit(
        name="joint_1",
        quantity=LimitQuantity.POSITION,
        lower=None,
        upper=None,
        unit="rad",
        space=LimitSpace.JOINT,
        frame="fast_arm joint space",
        status=EvidenceStatus.CONFLICT,
        source=_source(status=EvidenceStatus.CONFLICT, evidence_reference=None),
        reason="two revisions disagree",
    )
    assert not conflict.is_authoritative


def test_authoritative_limit_requires_authoritative_typed_source() -> None:
    with pytest.raises(ValueError, match="authoritative limit requires authoritative source"):
        _joint_limit(
            status=EvidenceStatus.AUTHORITATIVE,
            source=_source(
                kind="lab_document",
                status=EvidenceStatus.UNKNOWN,
                evidence_reference=None,
            ),
        )


def test_constructor_bypassed_source_or_limit_cannot_become_authoritative() -> None:
    provisional_source = _source(
        kind="fixture",
        status=EvidenceStatus.PROVISIONAL,
        evidence_reference=None,
    )
    object.__setattr__(provisional_source, "status", EvidenceStatus.AUTHORITATIVE)
    assert not provisional_source.is_physical_evidence

    with pytest.raises(ValueError, match="source kind|provenance"):
        _joint_limit(status=EvidenceStatus.AUTHORITATIVE, source=provisional_source)

    limit = _joint_limit(status=EvidenceStatus.PROVISIONAL)
    object.__setattr__(limit, "status", EvidenceStatus.AUTHORITATIVE)
    assert not limit.is_authoritative
    assert effective_limit_status(limit) is EvidenceStatus.INVALID


def test_limit_rejects_same_semantic_nested_source_or_conversion_replacement() -> None:
    limit = _joint_limit()
    replacement_source = _source()
    object.__setattr__(limit, "source", replacement_source)
    assert not limit.is_authoritative
    with pytest.raises(ValueError, match="mutated or bypassed"):
        limit.to_dict()

    limit = _joint_limit()
    bypassed_source = object.__new__(LimitSourceProvenance)
    object.__setattr__(limit, "source", bypassed_source)
    assert not limit.is_authoritative
    with pytest.raises((AttributeError, TypeError, ValueError)):
        limit.to_dict()

    limit = _joint_limit()
    replacement_conversion = LimitConversionProvenance.identity(LimitSpace.JOINT)
    object.__setattr__(limit, "conversion", replacement_conversion)
    assert not limit.is_authoritative
    with pytest.raises(ValueError, match="mutated or bypassed"):
        limit.to_dict()


def test_external_source_seal_rejects_coherent_private_snapshot_rewrite() -> None:
    source = _source(
        kind="lab_document",
        status=EvidenceStatus.PROVISIONAL,
    )
    object.__setattr__(source, "status", EvidenceStatus.AUTHORITATIVE)
    object.__setattr__(
        source,
        "_canonical_snapshot",
        (
            source.source_kind,
            source.source_id,
            source.revision,
            source.status,
            source.evidence_reference,
            source.observed_at,
            source.notes,
        ),
    )
    assert not source.is_physical_evidence


def test_joint_limit_rejects_non_identity_conversion_metadata() -> None:
    conversion = LimitConversionProvenance(
        source_space=LimitSpace.JOINT,
        target_space=LimitSpace.JOINT,
        method="forged",
        relation_id="forged-relation",
        gear_ratio=2.0,
        sign=1.0,
        offset=0.0,
    )
    with pytest.raises(ValueError, match="identity"):
        _joint_limit(conversion=conversion)


def test_joint_limit_rejects_public_projected_conversion_attachment() -> None:
    conversion = LimitConversionProvenance.projected(
        source_space=LimitSpace.MOTOR,
        relation_id="motor-1-to-joint-1/v1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        source_name="motor_1",
    )

    with pytest.raises(ValueError, match="canonical projection origin"):
        _joint_limit(conversion=conversion)


def test_joint_limit_rejects_forged_cross_space_conversion_origin() -> None:
    conversion = LimitConversionProvenance(
        source_space=LimitSpace.MOTOR,
        target_space=LimitSpace.JOINT,
        method="joint = sign * source / gear_ratio + offset",
        relation_id="motor-1-to-joint-1/v1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
    )
    with pytest.raises(ValueError, match="canonical origin"):
        _joint_limit(conversion=conversion)


def test_projected_limit_validator_rejects_constructor_bypass() -> None:
    limit = _joint_limit()
    conversion = LimitConversionProvenance.projected(
        source_space=LimitSpace.MOTOR,
        relation_id="motor-1-to-joint-1/v1",
        gear_ratio=1.0,
        sign=1.0,
        offset=0.0,
        source_name="motor_1",
    )
    object.__setattr__(limit, "conversion", conversion)

    assert not limit.is_authoritative
    with pytest.raises(ValueError, match="canonical projection origin"):
        validate_physical_limit(limit)
    with pytest.raises(ValueError, match="canonical projection origin"):
        limit.to_dict()

    bypassed = object.__new__(PhysicalLimit)
    for field_name in (
        "name",
        "quantity",
        "lower",
        "upper",
        "unit",
        "space",
        "frame",
        "status",
        "source",
        "conversion",
        "reason",
    ):
        object.__setattr__(bypassed, field_name, getattr(limit, field_name))
    with pytest.raises(ValueError, match="construction origin"):
        validate_physical_limit(bypassed)


@pytest.mark.parametrize(
    ("limit_status", "source_status", "expected"),
    (
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.PROVISIONAL, EvidenceStatus.PROVISIONAL),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.UNKNOWN, EvidenceStatus.UNKNOWN),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.UNAVAILABLE, EvidenceStatus.UNAVAILABLE),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.CONFLICT, EvidenceStatus.CONFLICT),
        (EvidenceStatus.PROVISIONAL, EvidenceStatus.INVALID, EvidenceStatus.INVALID),
        (EvidenceStatus.UNKNOWN, EvidenceStatus.CONFLICT, EvidenceStatus.CONFLICT),
        (EvidenceStatus.CONFLICT, EvidenceStatus.INVALID, EvidenceStatus.INVALID),
    ),
)
def test_effective_status_has_typed_value_source_precedence(
    limit_status: EvidenceStatus,
    source_status: EvidenceStatus,
    expected: EvidenceStatus,
) -> None:
    limit = PhysicalLimit(
        name="joint_1",
        quantity=LimitQuantity.POSITION,
        lower=None
        if limit_status
        in {
            EvidenceStatus.UNKNOWN,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.CONFLICT,
            EvidenceStatus.INVALID,
        }
        else -1.0,
        upper=None
        if limit_status
        in {
            EvidenceStatus.UNKNOWN,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.CONFLICT,
            EvidenceStatus.INVALID,
        }
        else 1.0,
        unit="rad",
        space=LimitSpace.JOINT,
        frame="fast_arm joint space",
        status=limit_status,
        source=_source(
            kind="fixture",
            status=source_status,
            evidence_reference=None,
        ),
        reason=f"{limit_status.value} fixture"
        if limit_status
        in {
            EvidenceStatus.UNKNOWN,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.CONFLICT,
            EvidenceStatus.INVALID,
        }
        else None,
    )

    assert effective_limit_status(limit) is expected


@pytest.mark.parametrize(
    "status",
    (
        EvidenceStatus.UNKNOWN,
        EvidenceStatus.UNAVAILABLE,
        EvidenceStatus.CONFLICT,
        EvidenceStatus.INVALID,
    ),
)
def test_unresolved_limit_statuses_are_always_unbounded(
    status: EvidenceStatus,
) -> None:
    limit = PhysicalLimit(
        name="joint_1",
        quantity=LimitQuantity.POSITION,
        lower=None,
        upper=None,
        unit="rad",
        space=LimitSpace.JOINT,
        frame="fast_arm joint space",
        status=status,
        source=_source(status=status, evidence_reference=None),
        reason=f"{status.value} source",
    )

    assert not limit.is_bounded
    with pytest.raises(ValueError, match="must not contain bounds"):
        PhysicalLimit(
            name="joint_1",
            quantity=LimitQuantity.POSITION,
            lower=-1.0,
            upper=1.0,
            unit="rad",
            space=LimitSpace.JOINT,
            frame="fast_arm joint space",
            status=status,
            source=_source(status=status, evidence_reference=None),
            reason=f"{status.value} source",
        )


def test_envelope_decoder_rejects_duplicate_json_keys() -> None:
    envelope = PhysicalSafetyEnvelope(
        envelope_id="fixture",
        envelope_version=1,
        robot_id="fast_arm",
        model_id="fast_arm",
        limits=(_joint_limit(),),
    )

    duplicate_root = (
        b'{"schema_version":1,"schema_version":1,'
        b'"envelope_id":"fixture","envelope_version":1,'
        b'"robot_id":"fast_arm","model_id":"fast_arm","limits":[]}'
    )
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        PhysicalSafetyEnvelope.from_json_bytes(duplicate_root)

    raw = envelope.to_json_bytes().decode("utf-8")
    duplicate_nested = raw.replace(
        '"source_kind":"lab_document"',
        '"source_kind":"lab_document","source_kind":"lab_document"',
        1,
    ).encode("utf-8")
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        PhysicalSafetyEnvelope.from_json_bytes(duplicate_nested)


@pytest.mark.parametrize(
    ("kind", "evidence_reference", "authority_asserted", "expected"),
    (
        ("joint_limit_toml", "record", True, EvidenceStatus.PROVISIONAL),
        ("manufacturer_document", "record", True, EvidenceStatus.AUTHORITATIVE),
        ("lab_document", None, True, EvidenceStatus.UNKNOWN),
        ("controller_setting", None, False, EvidenceStatus.PROVISIONAL),
    ),
)
def test_source_classification_is_explicit_and_fail_closed(
    kind: str,
    evidence_reference: str | None,
    authority_asserted: bool,
    expected: EvidenceStatus,
) -> None:
    assert (
        classify_source_status(
            source_kind=kind,
            evidence_reference=evidence_reference,
            authority_asserted=authority_asserted,
        )
        is expected
    )
