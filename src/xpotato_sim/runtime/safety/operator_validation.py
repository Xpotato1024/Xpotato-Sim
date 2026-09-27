"""Operator-gated physical validation procedure and evidence artifact.

これは実機を操作するadapterではない。target / operator / clearance / preflight / stop /
rollbackを明示したprocedureと、expected / observed / source / revision / safety decisionを
strictに保存するsoftware-only boundaryであり、actual hardware runは#509の責務として残す。
"""

from __future__ import annotations

import json
import math
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Any

from xpotato_sim.runtime.safety.physical_safety_core import (
    SafetyComponent,
    SafetyDecisionAction,
    validate_safety_decision_projection,
)


VALIDATION_ARTIFACT_SCHEMA_VERSION = 1


# operator-validation DTOのauthorityはprivate fieldだけに依存しない。constructor時の
# semantic snapshotをowner-local weak identity sealへ保存し、public nested fieldsと
# private hintを同時に書き換えるcallerやobject.__new__ bypassを再利用不能にする。
_OPERATOR_SEALS: dict[
    int, tuple[weakref.ReferenceType[object], object]
] = {}
_OPERATOR_SEALS_LOCK = RLock()


def _release_operator_seal(
    key: int,
    reference: weakref.ReferenceType[object],
) -> None:
    with _OPERATOR_SEALS_LOCK:
        entry = _OPERATOR_SEALS.get(key)
        if entry is not None and entry[0] is reference:
            _OPERATOR_SEALS.pop(key, None)


def _register_operator_seal(value: object, snapshot: object) -> None:
    key = id(value)
    reference = weakref.ref(
        value,
        lambda ref, key=key: _release_operator_seal(key, ref),
    )
    with _OPERATOR_SEALS_LOCK:
        _OPERATOR_SEALS[key] = (reference, snapshot)


def _validate_operator_seal(value: object, snapshot: object) -> None:
    key = id(value)
    with _OPERATOR_SEALS_LOCK:
        entry = _OPERATOR_SEALS.get(key)
        if entry is None or entry[0]() is not value or entry[1] != snapshot:
            raise ValueError("operator validation DTO is not constructor-sealed")


class ValidationClassification(str, Enum):
    """procedure / artifactのclosed lifecycle classification。"""

    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    ABORTED = "aborted"
    TECHNICAL_INVALID = "technical_invalid"


class ValidationCheckStatus(str, Enum):
    """個別checkのclosed outcome。"""

    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    TECHNICAL_INVALID = "technical_invalid"


class ValidationCheckKind(str, Enum):
    """procedureが要求する検証axis。"""

    LIMIT_RANGE = "limit_range"
    COLLISION_CLEARANCE = "collision_clearance"
    TRAJECTORY_FEASIBILITY = "trajectory_feasibility"
    STOP_PROCEDURE = "stop_procedure"
    ROLLBACK_PROCEDURE = "rollback_procedure"


# R7-J procedureがPASSへ進むために、各axisを少なくとも一つ要求する。
_MANDATORY_VALIDATION_CHECK_KINDS = frozenset(
    {
        ValidationCheckKind.LIMIT_RANGE,
        ValidationCheckKind.COLLISION_CLEARANCE,
        ValidationCheckKind.TRAJECTORY_FEASIBILITY,
        ValidationCheckKind.STOP_PROCEDURE,
        ValidationCheckKind.ROLLBACK_PROCEDURE,
    }
)


class MeasurementSourceKind(str, Enum):
    """observed値の出所。softwareとphysicalを混同しない。"""

    SOFTWARE_DRY_RUN = "software_dry_run"
    MUJOCO_SIMULATION = "mujoco_simulation"
    MANUFACTURER_DOCUMENT = "manufacturer_document"
    PHYSICAL_MEASUREMENT = "physical_measurement"
    UNKNOWN = "unknown"

    @property
    def is_physical(self) -> bool:
        return self in {
            MeasurementSourceKind.MANUFACTURER_DOCUMENT,
            MeasurementSourceKind.PHYSICAL_MEASUREMENT,
        }


class EvidenceClass(str, Enum):
    """artifact内のevidence source composition。"""

    NONE = "none"
    SOFTWARE_ONLY = "software_only"
    PHYSICAL_ONLY = "physical_only"
    MIXED = "mixed"
    UNKNOWN = "unknown"


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _parse_timestamp(name: str, value: object) -> datetime:
    text = _text(name, value)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _timestamp(name: str, value: object) -> str:
    text = _text(name, value)
    _parse_timestamp(name, text)
    return text


def _finite_value(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return 0.0 if number == 0.0 else number


def _json_value(name: str, value: object) -> object:
    """expected / observedのfinite JSON subsetを再帰的に検証する。"""

    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        return _finite_value(name, value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            _text(f"{name} key", key)
            if key in result:
                raise ValueError(f"{name} contains duplicate key: {key}")
            result[key] = _json_value(f"{name}.{key}", child)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(f"{name}[{index}]", child) for index, child in enumerate(value)]
    raise TypeError(f"{name} contains a value that is not JSON-compatible")


def _mapping(name: str, value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _fields(name: str, value: object, allowed: set[str]) -> Mapping[str, object]:
    raw = _mapping(name, value)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {sorted(unknown)!r}")
    missing = allowed - set(raw)
    if missing:
        raise ValueError(f"{name} is missing fields: {sorted(missing)!r}")
    return raw


def _enum(enum_type: type[Enum], name: str, value: object) -> Any:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{name} must be one of: {allowed}") from exc


def _reason_identity(value: object) -> str:
    """P5 component-owned reason identityをstrictに検証する。"""

    identity = _text("reason_identity", value)
    parts = identity.split(":")
    if len(parts) != 2:
        raise ValueError("reason_identity must be component:reason_code")
    try:
        SafetyComponent(parts[0])
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SafetyComponent)
        raise ValueError(f"reason_identity component must be one of: {allowed}") from exc
    reason_code = parts[1]
    if not reason_code or not reason_code[0].islower() or any(
        not (character.isascii() and (character.islower() or character.isdigit() or character == "_"))
        for character in reason_code
    ):
        raise ValueError("reason_identity reason_code must use lowercase underscore notation")
    return identity


@dataclass(frozen=True, slots=True, weakref_slot=True)
class TargetIdentity:
    """検証対象robot/controller/connection/model identity。"""

    target_id: str
    robot_id: str
    controller_id: str
    connection_id: str
    model_id: str

    def __post_init__(self) -> None:
        for name in ("target_id", "robot_id", "controller_id", "connection_id", "model_id"):
            _text(name, getattr(self, name))
        _register_operator_seal(self, _target_snapshot(self))

    def to_dict(self) -> dict[str, object]:
        if type(self) is not TargetIdentity:
            raise TypeError("target must be TargetIdentity")
        _validate_operator_seal(self, _target_snapshot(self))
        return {
            "target_id": self.target_id,
            "robot_id": self.robot_id,
            "controller_id": self.controller_id,
            "connection_id": self.connection_id,
            "model_id": self.model_id,
        }


@dataclass(frozen=True, slots=True, weakref_slot=True)
class OperatorIdentity:
    """operator identity。"""

    operator_id: str
    role: str

    def __post_init__(self) -> None:
        _text("operator_id", self.operator_id)
        _text("role", self.role)
        _register_operator_seal(self, _operator_snapshot(self))

    def to_dict(self) -> dict[str, object]:
        if type(self) is not OperatorIdentity:
            raise TypeError("operator must be OperatorIdentity")
        _validate_operator_seal(self, _operator_snapshot(self))
        return {"operator_id": self.operator_id, "role": self.role}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class MeasurementSource:
    """expected / observed valueのsourceとevidence reference。"""

    kind: MeasurementSourceKind
    source_id: str
    revision: str
    evidence_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MeasurementSourceKind):
            object.__setattr__(self, "kind", MeasurementSourceKind(self.kind))
        _text("source_id", self.source_id)
        _text("revision", self.revision)
        if self.evidence_reference is not None:
            _text("evidence_reference", self.evidence_reference)
        if self.kind.is_physical and not self.evidence_reference:
            raise ValueError("physical source requires evidence_reference")
        _register_operator_seal(self, _measurement_source_snapshot(self))

    def to_dict(self) -> dict[str, object]:
        if type(self) is not MeasurementSource:
            raise TypeError("measurement_source must be MeasurementSource")
        _validate_operator_seal(self, _measurement_source_snapshot(self))
        result: dict[str, object] = {
            "kind": self.kind.value,
            "source_id": self.source_id,
            "revision": self.revision,
            "evidence_reference": self.evidence_reference,
        }
        return result


@dataclass(frozen=True, slots=True, weakref_slot=True)
class PreflightItem:
    """operatorが確認する一項目。"""

    item_id: str
    description: str
    checked: bool

    def __post_init__(self) -> None:
        _text("item_id", self.item_id)
        _text("description", self.description)
        if not isinstance(self.checked, bool):
            raise TypeError("checked must be bool")
        _register_operator_seal(self, _preflight_item_snapshot(self))

    def to_dict(self) -> dict[str, object]:
        if type(self) is not PreflightItem:
            raise TypeError("preflight item must be PreflightItem")
        _validate_operator_seal(self, _preflight_item_snapshot(self))
        return {"item_id": self.item_id, "description": self.description, "checked": self.checked}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class PreflightChecklist:
    """preflight項目とoperator acknowledgment。"""

    items: tuple[PreflightItem, ...]
    acknowledged_by: str
    acknowledged_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or not self.items:
            raise ValueError("preflight items must be a non-empty tuple")
        if not all(type(item) is PreflightItem for item in self.items):
            raise TypeError("preflight items must contain PreflightItem values")
        ids = tuple(item.item_id for item in self.items)
        if len(ids) != len(set(ids)):
            raise ValueError("preflight item IDs must be unique")
        _text("acknowledged_by", self.acknowledged_by)
        _timestamp("acknowledged_at", self.acknowledged_at)
        _register_operator_seal(self, _preflight_snapshot(self))

    @property
    def complete(self) -> bool:
        try:
            _validate_preflight(self)
        except Exception:
            return False
        return all(item.checked for item in self.items)

    def to_dict(self) -> dict[str, object]:
        if type(self) is not PreflightChecklist:
            raise TypeError("preflight must be PreflightChecklist")
        _validate_operator_seal(self, _preflight_snapshot(self))
        return {
            "items": [item.to_dict() for item in self.items],
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
        }


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ClearanceDeclaration:
    """required physical clearanceとverification evidence。"""

    required_clearance_m: float
    verified_clearance_m: float | None
    source: MeasurementSource
    verified_at: str | None

    def __post_init__(self) -> None:
        required = _finite_value("required_clearance_m", self.required_clearance_m)
        if required <= 0.0:
            raise ValueError("required_clearance_m must be positive")
        verified = None if self.verified_clearance_m is None else _finite_value("verified_clearance_m", self.verified_clearance_m)
        if verified is not None and verified < 0.0:
            raise ValueError("verified_clearance_m must be non-negative")
        if type(self.source) is not MeasurementSource:
            raise TypeError("source must be MeasurementSource")
        if self.verified_at is not None:
            _timestamp("verified_at", self.verified_at)
        object.__setattr__(self, "required_clearance_m", required)
        object.__setattr__(self, "verified_clearance_m", verified)
        _register_operator_seal(self, _clearance_snapshot(self))

    @property
    def verified(self) -> bool:
        try:
            _validate_clearance(self)
        except Exception:
            return False
        return self.verified_clearance_m is not None and self.verified_clearance_m >= self.required_clearance_m

    def to_dict(self) -> dict[str, object]:
        if type(self) is not ClearanceDeclaration:
            raise TypeError("clearance must be ClearanceDeclaration")
        _validate_operator_seal(self, _clearance_snapshot(self))
        _validate_operator_seal(self.source, _measurement_source_snapshot(self.source))
        return {
            "required_clearance_m": self.required_clearance_m,
            "verified_clearance_m": self.verified_clearance_m,
            "source": self.source.to_dict(),
            "verified_at": self.verified_at,
        }


@dataclass(frozen=True, slots=True, weakref_slot=True)
class StopProcedure:
    """normal stop / emergency stop procedure。"""

    normal_stop_steps: tuple[str, ...]
    emergency_stop_steps: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, steps in (("normal_stop_steps", self.normal_stop_steps), ("emergency_stop_steps", self.emergency_stop_steps)):
            if not isinstance(steps, tuple) or not steps or not all(isinstance(step, str) and step.strip() for step in steps):
                raise ValueError(f"{name} must contain non-empty steps")
        _register_operator_seal(self, _stop_snapshot(self))

    def to_dict(self) -> dict[str, object]:
        if type(self) is not StopProcedure:
            raise TypeError("stop must be StopProcedure")
        _validate_operator_seal(self, _stop_snapshot(self))
        return {"normal_stop_steps": list(self.normal_stop_steps), "emergency_stop_steps": list(self.emergency_stop_steps)}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class RollbackProcedure:
    """失敗 / abort後のrollback procedure。"""

    steps: tuple[str, ...]
    target_state: str

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple) or not self.steps or not all(isinstance(step, str) and step.strip() for step in self.steps):
            raise ValueError("rollback steps must contain non-empty steps")
        _text("target_state", self.target_state)
        _register_operator_seal(self, _rollback_snapshot(self))

    def to_dict(self) -> dict[str, object]:
        if type(self) is not RollbackProcedure:
            raise TypeError("rollback must be RollbackProcedure")
        _validate_operator_seal(self, _rollback_snapshot(self))
        return {"steps": list(self.steps), "target_state": self.target_state}


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ValidationCheckSpec:
    """procedureが要求するcheck identity。"""

    check_id: str
    kind: ValidationCheckKind
    description: str

    def __post_init__(self) -> None:
        _text("check_id", self.check_id)
        if not isinstance(self.kind, ValidationCheckKind):
            object.__setattr__(self, "kind", ValidationCheckKind(self.kind))
        _text("description", self.description)
        _register_operator_seal(self, _check_spec_snapshot(self))

    def to_dict(self) -> dict[str, object]:
        if type(self) is not ValidationCheckSpec:
            raise TypeError("check spec must be ValidationCheckSpec")
        _validate_operator_seal(self, _check_spec_snapshot(self))
        return {"check_id": self.check_id, "kind": self.kind.value, "description": self.description}


def _missing_mandatory_check_kinds(required_checks: Sequence[ValidationCheckSpec]) -> frozenset[ValidationCheckKind]:
    present_kinds = {item.kind for item in required_checks}
    return _MANDATORY_VALIDATION_CHECK_KINDS - present_kinds


def _validate_target_identity(value: object) -> TargetIdentity:
    if type(value) is not TargetIdentity:
        raise TypeError("target must be TargetIdentity")
    for name in ("target_id", "robot_id", "controller_id", "connection_id", "model_id"):
        _text(name, _nested_field(value, name))
    _validate_operator_seal(value, _target_snapshot(value))
    return value


def _validate_operator_identity(value: object) -> OperatorIdentity:
    if type(value) is not OperatorIdentity:
        raise TypeError("operator must be OperatorIdentity")
    _text("operator_id", _nested_field(value, "operator_id"))
    _text("role", _nested_field(value, "role"))
    _validate_operator_seal(value, _operator_snapshot(value))
    return value


def _validate_measurement_source(value: object) -> MeasurementSource:
    if type(value) is not MeasurementSource:
        raise TypeError("measurement_source must be MeasurementSource")
    kind = _nested_field(value, "kind")
    if type(kind) is not MeasurementSourceKind:
        raise TypeError("measurement source kind must be MeasurementSourceKind")
    _text("source_id", _nested_field(value, "source_id"))
    _text("revision", _nested_field(value, "revision"))
    evidence = _nested_field(value, "evidence_reference")
    if evidence is not None:
        _text("evidence_reference", evidence)
    if kind.is_physical and not evidence:
        raise ValueError("physical source requires evidence_reference")
    _validate_operator_seal(value, _measurement_source_snapshot(value))
    return value


def _validate_preflight_item(value: object) -> PreflightItem:
    if type(value) is not PreflightItem:
        raise TypeError("preflight items must contain PreflightItem values")
    _text("item_id", _nested_field(value, "item_id"))
    _text("description", _nested_field(value, "description"))
    if type(_nested_field(value, "checked")) is not bool:
        raise TypeError("checked must be bool")
    _validate_operator_seal(value, _preflight_item_snapshot(value))
    return value


def _validate_preflight(value: object) -> PreflightChecklist:
    if type(value) is not PreflightChecklist:
        raise TypeError("preflight must be PreflightChecklist")
    items = _nested_field(value, "items")
    if type(items) is not tuple or not items:
        raise ValueError("preflight items must be a non-empty tuple")
    for item in items:
        _validate_preflight_item(item)
    ids = tuple(item.item_id for item in items)
    if len(ids) != len(set(ids)):
        raise ValueError("preflight item IDs must be unique")
    _text("acknowledged_by", _nested_field(value, "acknowledged_by"))
    _timestamp("acknowledged_at", _nested_field(value, "acknowledged_at"))
    _validate_operator_seal(value, _preflight_snapshot(value))
    return value


def _validate_clearance(value: object) -> ClearanceDeclaration:
    if type(value) is not ClearanceDeclaration:
        raise TypeError("clearance must be ClearanceDeclaration")
    required = _finite_value("required_clearance_m", _nested_field(value, "required_clearance_m"))
    if required <= 0.0:
        raise ValueError("required_clearance_m must be positive")
    verified_value = _nested_field(value, "verified_clearance_m")
    if verified_value is not None:
        verified = _finite_value("verified_clearance_m", verified_value)
        if verified < 0.0:
            raise ValueError("verified_clearance_m must be non-negative")
    _validate_measurement_source(_nested_field(value, "source"))
    verified_at = _nested_field(value, "verified_at")
    if verified_at is not None:
        _timestamp("verified_at", verified_at)
    _validate_operator_seal(value, _clearance_snapshot(value))
    return value


def _validate_stop(value: object) -> StopProcedure:
    if type(value) is not StopProcedure:
        raise TypeError("stop must be StopProcedure")
    for name in ("normal_stop_steps", "emergency_stop_steps"):
        steps = _nested_field(value, name)
        if type(steps) is not tuple or not steps:
            raise ValueError(f"{name} must contain non-empty steps")
        for step in steps:
            _text(f"{name} step", step)
    _validate_operator_seal(value, _stop_snapshot(value))
    return value


def _validate_rollback(value: object) -> RollbackProcedure:
    if type(value) is not RollbackProcedure:
        raise TypeError("rollback must be RollbackProcedure")
    steps = _nested_field(value, "steps")
    if type(steps) is not tuple or not steps:
        raise ValueError("rollback steps must contain non-empty steps")
    for step in steps:
        _text("rollback step", step)
    _text("target_state", _nested_field(value, "target_state"))
    _validate_operator_seal(value, _rollback_snapshot(value))
    return value


def _validate_validation_check_spec(value: object) -> ValidationCheckSpec:
    if type(value) is not ValidationCheckSpec:
        raise TypeError("required_checks must contain ValidationCheckSpec values")
    _text("check_id", _nested_field(value, "check_id"))
    if type(_nested_field(value, "kind")) is not ValidationCheckKind:
        raise TypeError("check kind must be ValidationCheckKind")
    _text("description", _nested_field(value, "description"))
    _validate_operator_seal(value, _check_spec_snapshot(value))
    return value


def _validate_validation_procedure(
    procedure: ValidationProcedure,
    *,
    initialize: bool = False,
) -> ValidationProcedure:
    """ValidationProcedureの元nested DTOを再構築せず、同一objectのまま検証する。"""

    if type(procedure) is not ValidationProcedure:
        raise TypeError("procedure must be ValidationProcedure")
    _text("procedure_id", _nested_field(procedure, "procedure_id"))
    _validate_target_identity(_nested_field(procedure, "target"))
    _validate_operator_identity(_nested_field(procedure, "operator"))
    _text("software_revision", _nested_field(procedure, "software_revision"))
    _timestamp("created_at", _nested_field(procedure, "created_at"))
    _validate_preflight(_nested_field(procedure, "preflight"))
    _validate_clearance(_nested_field(procedure, "clearance"))
    _validate_stop(_nested_field(procedure, "stop"))
    _validate_rollback(_nested_field(procedure, "rollback"))
    required_checks = _nested_field(procedure, "required_checks")
    if type(required_checks) is not tuple or not required_checks:
        raise ValueError("required_checks must be a non-empty tuple")
    for item in required_checks:
        _validate_validation_check_spec(item)
    check_ids = tuple(item.check_id for item in required_checks)
    if len(check_ids) != len(set(check_ids)):
        raise ValueError("required check IDs must be unique")
    missing_kinds = _missing_mandatory_check_kinds(required_checks)
    if missing_kinds:
        missing = ", ".join(sorted(kind.value for kind in missing_kinds))
        raise ValueError(
            "required_checks must include all mandatory validation check kinds: "
            f"{missing}"
        )
    operator_confirmed = _nested_field(procedure, "operator_confirmed")
    if type(operator_confirmed) is not bool:
        raise TypeError("operator_confirmed must be bool")
    dry_run_only = _nested_field(procedure, "dry_run_only")
    if type(dry_run_only) is not bool or dry_run_only is not True:
        raise ValueError(
            "R7-J operator procedure is dry-run-only; actual hardware belongs to #509"
        )
    if initialize:
        _register_operator_seal(procedure, _procedure_snapshot(procedure))
    else:
        _validate_operator_seal(procedure, _procedure_snapshot(procedure))
    return procedure


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ValidationProcedure:
    """operator gateを満たしたsoftware-only validation plan。"""

    procedure_id: str
    target: TargetIdentity
    operator: OperatorIdentity
    software_revision: str
    created_at: str
    preflight: PreflightChecklist
    clearance: ClearanceDeclaration
    stop: StopProcedure
    rollback: RollbackProcedure
    required_checks: tuple[ValidationCheckSpec, ...]
    operator_confirmed: bool
    dry_run_only: bool = True

    def __post_init__(self) -> None:
        _validate_validation_procedure(self, initialize=True)

    def to_dict(self) -> dict[str, object]:
        _validate_validation_procedure(self)
        return _procedure_to_dict_raw(self)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ProcedureGateResult:
    """operator gateのreadiness。"""

    classification: ValidationClassification
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.classification, ValidationClassification):
            object.__setattr__(self, "classification", ValidationClassification(self.classification))
        _text("reason_code", self.reason_code)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SafetyDecisionEvidence:
    """P5 decision identityのartifact projection。"""

    action: SafetyDecisionAction
    reason_identity: str
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        action, reason_identity, provenance = validate_safety_decision_projection(
            self.action,
            self.reason_identity,
            self.provenance,
        )
        if not provenance:
            raise ValueError("provenance must be a non-empty tuple of non-empty strings")
        if len(provenance) != len(set(provenance)):
            raise ValueError("provenance must be unique")
        # canonical projectionの正規化はconstructor時に一度だけ行う。
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "reason_identity", reason_identity)
        object.__setattr__(self, "provenance", provenance)
        _register_operator_seal(self, _decision_evidence_snapshot(self))

    def to_dict(self) -> dict[str, object]:
        if type(self) is not SafetyDecisionEvidence:
            raise TypeError("safety_decision must be SafetyDecisionEvidence")
        _validate_operator_seal(self, _decision_evidence_snapshot(self))
        return {"action": self.action.value, "reason_identity": self.reason_identity, "provenance": list(self.provenance)}


def _validate_safety_decision_evidence(
    value: object,
) -> SafetyDecisionEvidence:
    """SafetyDecisionEvidenceの元objectを再構築せずに検証する。"""

    if type(value) is not SafetyDecisionEvidence:
        raise TypeError("safety_decision must be SafetyDecisionEvidence")
    action = _nested_field(value, "action")
    if type(action) is not SafetyDecisionAction:
        raise TypeError("safety decision action must be SafetyDecisionAction")
    reason_identity = _nested_field(value, "reason_identity")
    _reason_identity(reason_identity)
    provenance = _nested_field(value, "provenance")
    if type(provenance) is not tuple or not provenance or not all(
        type(item) is str and item == item.strip() and item for item in provenance
    ):
        raise ValueError("provenance must be a non-empty tuple of non-empty strings")
    if len(set(provenance)) != len(provenance):
        raise ValueError("provenance must be unique")
    validate_safety_decision_projection(action, reason_identity, provenance)
    _validate_operator_seal(value, _decision_evidence_snapshot(value))
    return value


def _validate_decision_provenance_binding(
    decision: SafetyDecisionEvidence,
    *,
    check_id: str,
    source: MeasurementSource,
    software_revision: str,
) -> None:
    """decision provenanceが当該check/sourceの実体を参照することを確認する。"""

    required = {check_id, source.source_id, source.revision, software_revision}
    if source.evidence_reference is not None:
        required.add(source.evidence_reference)
    if not required.issubset(set(decision.provenance)):
        raise ValueError(
            "safety decision provenance must bind to check and measurement evidence"
        )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ValidationCheckEvidence:
    """expected / observed / source / decision付きcheck result。"""

    check_id: str
    kind: ValidationCheckKind
    status: ValidationCheckStatus
    expected: Mapping[str, object]
    observed: Mapping[str, object] | None
    measurement_source: MeasurementSource
    observed_at: str | None
    software_revision: str
    safety_decision: SafetyDecisionEvidence
    reason: str

    def __post_init__(self) -> None:
        validate_validation_check_evidence(self, initialize=True)

    def to_dict(self) -> dict[str, object]:
        validate_validation_check_evidence(self)
        return _check_to_dict_raw(self)


def _measurement_source_to_dict_raw(source: MeasurementSource) -> dict[str, object]:
    return {
        "kind": source.kind.value,
        "source_id": source.source_id,
        "revision": source.revision,
        "evidence_reference": source.evidence_reference,
    }


def _target_to_dict_raw(target: TargetIdentity) -> dict[str, object]:
    return {
        "target_id": target.target_id,
        "robot_id": target.robot_id,
        "controller_id": target.controller_id,
        "connection_id": target.connection_id,
        "model_id": target.model_id,
    }


def _operator_to_dict_raw(operator: OperatorIdentity) -> dict[str, object]:
    return {"operator_id": operator.operator_id, "role": operator.role}


def _preflight_to_dict_raw(preflight: PreflightChecklist) -> dict[str, object]:
    return {
        "items": [
            {
                "item_id": item.item_id,
                "description": item.description,
                "checked": item.checked,
            }
            for item in preflight.items
        ],
        "acknowledged_by": preflight.acknowledged_by,
        "acknowledged_at": preflight.acknowledged_at,
    }


def _clearance_to_dict_raw(clearance: ClearanceDeclaration) -> dict[str, object]:
    return {
        "required_clearance_m": clearance.required_clearance_m,
        "verified_clearance_m": clearance.verified_clearance_m,
        "source": _measurement_source_to_dict_raw(clearance.source),
        "verified_at": clearance.verified_at,
    }


def _stop_to_dict_raw(stop: StopProcedure) -> dict[str, object]:
    return {
        "normal_stop_steps": list(stop.normal_stop_steps),
        "emergency_stop_steps": list(stop.emergency_stop_steps),
    }


def _rollback_to_dict_raw(rollback: RollbackProcedure) -> dict[str, object]:
    return {"steps": list(rollback.steps), "target_state": rollback.target_state}


def _check_spec_to_dict_raw(spec: ValidationCheckSpec) -> dict[str, object]:
    return {
        "check_id": spec.check_id,
        "kind": spec.kind.value,
        "description": spec.description,
    }


def _procedure_to_dict_raw(procedure: ValidationProcedure) -> dict[str, object]:
    return {
        "procedure_id": procedure.procedure_id,
        "target": _target_to_dict_raw(procedure.target),
        "operator": _operator_to_dict_raw(procedure.operator),
        "software_revision": procedure.software_revision,
        "created_at": procedure.created_at,
        "preflight": _preflight_to_dict_raw(procedure.preflight),
        "clearance": _clearance_to_dict_raw(procedure.clearance),
        "stop": _stop_to_dict_raw(procedure.stop),
        "rollback": _rollback_to_dict_raw(procedure.rollback),
        "required_checks": [
            _check_spec_to_dict_raw(item) for item in procedure.required_checks
        ],
        "operator_confirmed": procedure.operator_confirmed,
        "dry_run_only": procedure.dry_run_only,
    }


def _safety_decision_to_dict_raw(
    safety_decision: SafetyDecisionEvidence,
) -> dict[str, object]:
    return {
        "action": safety_decision.action.value,
        "reason_identity": safety_decision.reason_identity,
        "provenance": list(safety_decision.provenance),
    }


def _check_to_dict_raw(check: ValidationCheckEvidence) -> dict[str, object]:
    return {
        "check_id": check.check_id,
        "kind": check.kind.value,
        "status": check.status.value,
        "expected": dict(check.expected),
        "observed": None if check.observed is None else dict(check.observed),
        "measurement_source": _measurement_source_to_dict_raw(check.measurement_source),
        "observed_at": check.observed_at,
        "software_revision": check.software_revision,
        "safety_decision": _safety_decision_to_dict_raw(check.safety_decision),
        "reason": check.reason,
    }


def _nested_field(value: object, name: str) -> object:
    """constructor bypassで欠落したnested fieldをValueErrorへ閉じる。"""

    try:
        return getattr(value, name)
    except Exception as exc:
        raise ValueError(f"{name} is structurally incomplete") from exc


def _semantic_snapshot(value: object) -> object:
    """DTOの値を、object identityに依存しない比較可能な値へ凍結する。"""

    if isinstance(value, Enum):
        return ("enum", type(value).__qualname__, value.value)
    if isinstance(value, Mapping):
        items = tuple(
            sorted(
                (
                    _semantic_snapshot(key),
                    _semantic_snapshot(child),
                )
                for key, child in value.items()
            )
        )
        return ("mapping", items)
    if isinstance(value, (tuple, list)):
        return ("sequence", tuple(_semantic_snapshot(child) for child in value))
    if isinstance(value, (str, int, float, bool)) or value is None:
        return (type(value).__qualname__, value)
    return (type(value).__qualname__, repr(value))


def _identity_semantic_snapshot(value: object, semantic: object) -> object:
    """外部seal用の型・identity・semantic値を束ねる。"""

    return ("object", type(value), id(value), semantic)


def _tuple_snapshot(value: object, children: object) -> object:
    """tuple container自体の差替えも検出する。"""

    return ("tuple", type(value), id(value), children)


def _safe_attr(value: object, name: str) -> object:
    try:
        return _semantic_snapshot(getattr(value, name))
    except Exception:
        return ("invalid", name)


def _safe_nested_snapshot(
    label: str,
    value: object,
    snapshotter: object,
) -> object:
    try:
        semantic = snapshotter(value)  # type: ignore[operator]
    except Exception:
        semantic = ("invalid", label)
    return _identity_semantic_snapshot(value, semantic)


def _target_snapshot(value: TargetIdentity) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                value.target_id,
                value.robot_id,
                value.controller_id,
                value.connection_id,
                value.model_id,
            )
        ),
    )


def _operator_snapshot(value: OperatorIdentity) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot((value.operator_id, value.role)),
    )


def _measurement_source_snapshot(value: MeasurementSource) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (value.kind, value.source_id, value.revision, value.evidence_reference)
        ),
    )


def _preflight_item_snapshot(value: PreflightItem) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot((value.item_id, value.description, value.checked)),
    )


def _preflight_snapshot(value: PreflightChecklist) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                _tuple_snapshot(
                    value.items,
                    tuple(_preflight_item_snapshot(item) for item in value.items),
                ),
                value.acknowledged_by,
                value.acknowledged_at,
            )
        ),
    )


def _clearance_snapshot(value: ClearanceDeclaration) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                value.required_clearance_m,
                value.verified_clearance_m,
                _measurement_source_snapshot(value.source),
                value.verified_at,
            )
        ),
    )


def _stop_snapshot(value: StopProcedure) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                _tuple_snapshot(
                    value.normal_stop_steps,
                    _semantic_snapshot(value.normal_stop_steps),
                ),
                _tuple_snapshot(
                    value.emergency_stop_steps,
                    _semantic_snapshot(value.emergency_stop_steps),
                ),
            )
        ),
    )


def _rollback_snapshot(value: RollbackProcedure) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                _tuple_snapshot(value.steps, _semantic_snapshot(value.steps)),
                value.target_state,
            )
        ),
    )


def _check_spec_snapshot(value: ValidationCheckSpec) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot((value.check_id, value.kind, value.description)),
    )


def _procedure_snapshot(value: ValidationProcedure) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                value.procedure_id,
                _target_snapshot(value.target),
                _operator_snapshot(value.operator),
                value.software_revision,
                value.created_at,
                _preflight_snapshot(value.preflight),
                _clearance_snapshot(value.clearance),
                _stop_snapshot(value.stop),
                _rollback_snapshot(value.rollback),
                _tuple_snapshot(
                    value.required_checks,
                    tuple(_check_spec_snapshot(item) for item in value.required_checks),
                ),
                value.operator_confirmed,
                value.dry_run_only,
            )
        ),
    )


def _decision_evidence_snapshot(value: SafetyDecisionEvidence) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                value.action,
                value.reason_identity,
                _tuple_snapshot(value.provenance, _semantic_snapshot(value.provenance)),
            )
        ),
    )


def _check_snapshot(value: ValidationCheckEvidence) -> object:
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                value.check_id,
                value.kind,
                value.status,
                _identity_semantic_snapshot(
                    value.expected,
                    _semantic_snapshot(value.expected),
                ),
                _identity_semantic_snapshot(
                    value.observed,
                    _semantic_snapshot(value.observed),
                ),
                _measurement_source_snapshot(value.measurement_source),
                value.observed_at,
                value.software_revision,
                _decision_evidence_snapshot(value.safety_decision),
                value.reason,
            )
        ),
    )


def _artifact_snapshot(value: ValidationEvidenceArtifact) -> object:
    checks = getattr(value, "checks", None)
    return _identity_semantic_snapshot(
        value,
        _semantic_snapshot(
            (
                _safe_attr(value, "artifact_id"),
                _safe_nested_snapshot(
                    "procedure",
                    getattr(value, "procedure", None),
                    _procedure_snapshot,
                ),
                _safe_attr(value, "started_at"),
                _safe_attr(value, "completed_at"),
                _safe_attr(value, "classification"),
                _safe_attr(value, "classification_reason"),
                _safe_nested_snapshot(
                    "checks",
                    checks,
                    lambda values: _tuple_snapshot(
                        values,
                        tuple(
                            _safe_nested_snapshot("check", item, _check_snapshot)
                            for item in values
                        ),
                    ),
                ),
                _safe_attr(value, "operator_aborted"),
                _safe_attr(value, "schema_version"),
            )
        ),
    )


def validate_validation_check_evidence(
    check: ValidationCheckEvidence,
    *,
    initialize: bool = False,
) -> ValidationCheckEvidence:
    """checkと元nested source / decisionを差し替えずcanonicalに検証する。"""

    if type(check) is not ValidationCheckEvidence:
        raise TypeError("check must be ValidationCheckEvidence")
    check_id = _text("check_id", _nested_field(check, "check_id"))
    kind_value = _nested_field(check, "kind")
    if type(kind_value) is ValidationCheckKind:
        kind = kind_value
    elif initialize:
        kind = ValidationCheckKind(kind_value)
    else:
        raise TypeError("check kind must be ValidationCheckKind")
    status_value = _nested_field(check, "status")
    if type(status_value) is ValidationCheckStatus:
        status = status_value
    elif initialize:
        status = ValidationCheckStatus(status_value)
    else:
        raise TypeError("check status must be ValidationCheckStatus")
    expected_value = _nested_field(check, "expected")
    expected = _json_value("expected", expected_value)
    if not isinstance(expected, dict) or not expected:
        raise ValueError("expected must be a non-empty object")
    observed_value = _nested_field(check, "observed")
    observed = None if observed_value is None else _json_value("observed", observed_value)
    if observed is not None and not isinstance(observed, dict):
        raise ValueError("observed must be an object or None")
    if status is ValidationCheckStatus.PASS and (observed is None or not observed):
        raise ValueError("pass check requires non-empty observed evidence")
    if status is ValidationCheckStatus.FAIL and (observed is None or not observed):
        raise ValueError("fail check requires non-empty observed evidence")
    source_value = _nested_field(check, "measurement_source")
    source = _validate_measurement_source(source_value)
    observed_at_value = _nested_field(check, "observed_at")
    observed_at = None if observed_at_value is None else _timestamp("observed_at", observed_at_value)
    if status in {ValidationCheckStatus.PASS, ValidationCheckStatus.FAIL} and observed_at is None:
        raise ValueError("pass/fail check requires observed_at")
    software_revision = _text("software_revision", _nested_field(check, "software_revision"))
    safety_decision_value = _nested_field(check, "safety_decision")

    # Unknown measurement evidence is a check-level unavailable boundary. Keep
    # that decision ahead of P5 projection/provenance validation so a malformed
    # placeholder decision cannot turn an unknown PASS/ALLOW into a different
    # diagnostic or lifecycle outcome.
    if source.kind is MeasurementSourceKind.UNKNOWN and status is ValidationCheckStatus.PASS:
        raise ValueError("unknown measurement source cannot pass or allow")
    try:
        raw_action = _nested_field(safety_decision_value, "action")
    except Exception:
        raw_action = None
    if source.kind is MeasurementSourceKind.UNKNOWN and raw_action is SafetyDecisionAction.ALLOW:
        raise ValueError("unknown measurement source cannot pass or allow")

    safety_decision = _validate_safety_decision_evidence(safety_decision_value)
    reason = _text("reason", _nested_field(check, "reason"))
    _validate_decision_provenance_binding(
        safety_decision,
        check_id=check_id,
        source=source,
        software_revision=software_revision,
    )
    if source.kind is MeasurementSourceKind.UNKNOWN and safety_decision.action is SafetyDecisionAction.ALLOW:
        raise ValueError("unknown measurement source cannot pass or allow")
    if status is ValidationCheckStatus.PASS and safety_decision.action is not SafetyDecisionAction.ALLOW:
        raise ValueError("pass check requires allow safety decision")
    if status is ValidationCheckStatus.UNAVAILABLE and safety_decision.action is not SafetyDecisionAction.UNAVAILABLE:
        raise ValueError("unavailable check requires unavailable safety decision")
    if status is ValidationCheckStatus.TECHNICAL_INVALID and safety_decision.action is not SafetyDecisionAction.INVALID:
        raise ValueError("technical-invalid check requires invalid safety decision")
    if status is ValidationCheckStatus.FAIL and safety_decision.action not in {
        SafetyDecisionAction.REJECT,
        SafetyDecisionAction.HOLD,
        SafetyDecisionAction.STOP,
    }:
        raise ValueError("fail check requires reject, hold, or stop safety decision")
    if initialize:
        # 正規化はconstructor中に一度だけ行い、再検証時は元objectを保持する。
        object.__setattr__(check, "kind", kind)
        object.__setattr__(check, "status", status)
        object.__setattr__(check, "expected", expected)
        object.__setattr__(check, "observed", observed)
        object.__setattr__(check, "observed_at", observed_at)
    if initialize:
        _register_operator_seal(check, _check_snapshot(check))
    else:
        _validate_operator_seal(check, _check_snapshot(check))
    return check


def _derive_evidence_class(
    procedure: ValidationProcedure,
    checks: Sequence[ValidationCheckEvidence],
) -> EvidenceClass:
    try:
        _validate_validation_procedure(procedure)
        if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
            return EvidenceClass.UNKNOWN
        typed_checks = tuple(checks)
        for item in typed_checks:
            validate_validation_check_evidence(item)
    except Exception:
        return EvidenceClass.UNKNOWN

    source_kinds = (
        procedure.clearance.source.kind,
        *(item.measurement_source.kind for item in typed_checks),
    )
    if any(kind is MeasurementSourceKind.UNKNOWN for kind in source_kinds):
        return EvidenceClass.UNKNOWN
    physical = any(kind.is_physical for kind in source_kinds)
    software = any(not kind.is_physical for kind in source_kinds)
    if physical and software:
        return EvidenceClass.MIXED
    if physical:
        return EvidenceClass.PHYSICAL_ONLY
    return EvidenceClass.SOFTWARE_ONLY


def _check_has_physical_source(check: ValidationCheckEvidence) -> bool:
    """dry-run builderのsource判定をmalformed checkから隔離する。"""

    try:
        validate_validation_check_evidence(check)
        return check.measurement_source.kind.is_physical
    except Exception:
        return False


def _raw_measurement_source_kind(value: object) -> MeasurementSourceKind | None:
    """malformed procedure/checkでもphysical sourceを先に識別する。"""

    try:
        candidate = value.get("kind") if isinstance(value, Mapping) else getattr(value, "kind")
    except Exception:
        return None
    if type(candidate) is MeasurementSourceKind:
        return candidate
    if type(candidate) is str:
        try:
            return MeasurementSourceKind(candidate)
        except ValueError:
            return None
    return None


def _raw_field(value: object, name: str) -> object:
    """malformed DTOまたはmappingから、source判定用fieldだけを読む。"""

    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name)


def _raw_physical_source_present(
    procedure: object,
    checks: Sequence[object],
) -> bool:
    """dry-run構築前のsource境界。procedureが壊れていてもphysicalを拒否する。"""

    try:
        clearance = _raw_field(procedure, "clearance")
        clearance_source = _raw_field(clearance, "source")
    except Exception:
        clearance_source = None
    clearance_kind = _raw_measurement_source_kind(clearance_source)
    if clearance_kind is not None and clearance_kind.is_physical:
        return True
    for check in checks:
        try:
            source = _raw_field(check, "measurement_source")
        except Exception:
            continue
        kind = _raw_measurement_source_kind(source)
        if kind is not None and kind.is_physical:
            return True
    return False


def validate_operator_gate(procedure: ValidationProcedure) -> ProcedureGateResult:
    """operator / preflight / clearance gateをhardwareなしで評価する。"""

    if type(procedure) is not ValidationProcedure:
        return ProcedureGateResult(ValidationClassification.TECHNICAL_INVALID, "invalid_procedure")
    try:
        _validate_validation_procedure(procedure)
    except Exception:
        return ProcedureGateResult(ValidationClassification.TECHNICAL_INVALID, "invalid_procedure")
    if procedure.operator_confirmed is not True:
        return ProcedureGateResult(ValidationClassification.UNAVAILABLE, "operator_confirmation_required")
    if procedure.preflight.acknowledged_by != procedure.operator.operator_id:
        return ProcedureGateResult(ValidationClassification.UNAVAILABLE, "preflight_operator_mismatch")
    if not procedure.preflight.complete:
        return ProcedureGateResult(ValidationClassification.UNAVAILABLE, "preflight_incomplete")
    if procedure.clearance.verified_clearance_m is None or procedure.clearance.verified_at is None:
        return ProcedureGateResult(ValidationClassification.UNAVAILABLE, "clearance_verification_unavailable")
    if procedure.clearance.source.kind is MeasurementSourceKind.UNKNOWN:
        return ProcedureGateResult(ValidationClassification.UNAVAILABLE, "clearance_source_unknown")
    if not procedure.clearance.verified:
        return ProcedureGateResult(ValidationClassification.FAIL, "clearance_below_required")
    return ProcedureGateResult(ValidationClassification.PASS, "operator_gate_ready")


def validate_validation_procedure(procedure: ValidationProcedure) -> ValidationProcedure:
    """ValidationProcedureのpublic canonical deep validator。"""

    return _validate_validation_procedure(procedure)


def _validate_actual_check_ids(
    checks: Sequence[ValidationCheckEvidence],
) -> tuple[str, ...]:
    """actual check identityをclassifierとstrict decoderで共有する。"""

    actual_ids = tuple(item.check_id for item in checks)
    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("check IDs must be unique")
    return actual_ids


def _classify(
    procedure: ValidationProcedure,
    checks: Sequence[ValidationCheckEvidence],
    *,
    completed_at: str | None,
    operator_aborted: bool,
) -> tuple[ValidationClassification, str]:
    gate = validate_operator_gate(procedure)
    if gate.classification is ValidationClassification.TECHNICAL_INVALID:
        return gate.classification, gate.reason_code

    # Check identity is a classifier-level boundary. Read the actual IDs before
    # validating status/provenance so duplicate IDs cannot be hidden by a
    # malformed decision or an abort/completion lifecycle shortcut.
    actual_id_values: list[str] = []
    for check in checks:
        if type(check) is not ValidationCheckEvidence:
            return ValidationClassification.TECHNICAL_INVALID, "check_evidence_invalid"
        try:
            actual_id = _text("check_id", _nested_field(check, "check_id"))
        except Exception:
            return ValidationClassification.TECHNICAL_INVALID, "check_evidence_invalid"
        actual_id_values.append(actual_id)
    if len(actual_id_values) != len(set(actual_id_values)):
        return ValidationClassification.TECHNICAL_INVALID, "check_identity_invalid"
    actual_ids = tuple(actual_id_values)

    required = {item.check_id: item for item in procedure.required_checks}
    if any(item_id not in required for item_id in actual_ids):
        return ValidationClassification.TECHNICAL_INVALID, "check_identity_invalid"

    # Check schema and software/kind identity are validated before lifecycle
    # fields. An abort or missing completion timestamp must not hide malformed
    # data, and technical-invalid checks must precede UNKNOWN-source handling.
    for check in checks:
        try:
            validate_validation_check_evidence(check)
        except Exception:
            return ValidationClassification.TECHNICAL_INVALID, "check_evidence_invalid"
    for check in checks:
        if check.kind is not required[check.check_id].kind:
            return ValidationClassification.TECHNICAL_INVALID, "check_kind_mismatch"
        if check.software_revision != procedure.software_revision:
            return ValidationClassification.TECHNICAL_INVALID, "software_revision_mismatch"
    statuses = tuple(item.status for item in checks)
    # nestedのtechnical-invalidはprovenance / schema failureである。completion、abort、
    # operator gateのlifecycle結果より先に扱い、同時に発生したstopやgateの
    # failure / unavailableによって不正なevidenceを隠蔽しない。
    if ValidationCheckStatus.TECHNICAL_INVALID in statuses:
        return ValidationClassification.TECHNICAL_INVALID, "technical_invalid_check"
    if completed_at is None:
        return ValidationClassification.TECHNICAL_INVALID, "completion_timestamp_missing"
    if operator_aborted:
        return ValidationClassification.ABORTED, "operator_aborted"
    if set(actual_ids) != set(required):
        return ValidationClassification.UNAVAILABLE, "required_check_observation_incomplete"
    if gate.classification is ValidationClassification.FAIL:
        return ValidationClassification.FAIL, gate.reason_code
    if gate.classification is ValidationClassification.UNAVAILABLE:
        return ValidationClassification.UNAVAILABLE, gate.reason_code
    if any(item.measurement_source.kind is MeasurementSourceKind.UNKNOWN for item in checks):
        return ValidationClassification.UNAVAILABLE, "check_source_unknown"
    if ValidationCheckStatus.FAIL in statuses:
        return ValidationClassification.FAIL, "validation_check_failed"
    if ValidationCheckStatus.UNAVAILABLE in statuses:
        return ValidationClassification.UNAVAILABLE, "validation_check_unavailable"
    return ValidationClassification.PASS, "all_validation_checks_passed"


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ValidationEvidenceArtifact:
    """strict round-trip可能なoperator validation evidence artifact。"""

    artifact_id: str
    procedure: ValidationProcedure
    started_at: str
    completed_at: str | None
    classification: ValidationClassification
    classification_reason: str
    checks: tuple[ValidationCheckEvidence, ...]
    operator_aborted: bool = False
    schema_version: int = VALIDATION_ARTIFACT_SCHEMA_VERSION
    _binding_fingerprint: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _validate_validation_artifact(self, initialize=True)

    @property
    def evidence_class(self) -> EvidenceClass:
        try:
            _validate_validation_artifact(self)
        except Exception:
            return EvidenceClass.UNKNOWN
        return _derive_evidence_class(self.procedure, self.checks)

    @property
    def physical_evidence_present(self) -> bool:
        return self.evidence_class in {EvidenceClass.PHYSICAL_ONLY, EvidenceClass.MIXED}

    @property
    def complete(self) -> bool:
        try:
            _validate_validation_artifact(self)
        except Exception:
            return False
        return self.classification is ValidationClassification.PASS

    def to_dict(self) -> dict[str, object]:
        _validate_validation_artifact(self)
        return _artifact_to_dict_raw(self)

    def to_json_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


def _validate_validation_artifact(
    artifact: ValidationEvidenceArtifact,
    *,
    initialize: bool = False,
) -> ValidationEvidenceArtifact:
    """artifact全体をconstructor/property/serializationで共有して検証する。"""

    if type(artifact) is not ValidationEvidenceArtifact:
        raise TypeError("artifact must be ValidationEvidenceArtifact")
    artifact_id = _text("artifact_id", _nested_field(artifact, "artifact_id"))
    procedure = _nested_field(artifact, "procedure")
    if type(procedure) is not ValidationProcedure:
        raise TypeError("procedure must be ValidationProcedure")
    started_at = _timestamp("started_at", _nested_field(artifact, "started_at"))
    completed_value = _nested_field(artifact, "completed_at")
    completed_at = None if completed_value is None else _timestamp("completed_at", completed_value)
    if completed_at is not None and _parse_timestamp("completed_at", completed_at) < _parse_timestamp("started_at", started_at):
        raise ValueError("completed_at must not precede started_at")
    classification_value = _nested_field(artifact, "classification")
    if type(classification_value) is ValidationClassification:
        classification = classification_value
    elif initialize:
        classification = ValidationClassification(classification_value)
        # enumの正規化はconstructor中に一度だけ行い、再検証時は書き換えない。
        object.__setattr__(artifact, "classification", classification)
    else:
        raise TypeError("classification must be ValidationClassification")
    classification_reason = _text(
        "classification_reason",
        _nested_field(artifact, "classification_reason"),
    )
    checks = _nested_field(artifact, "checks")
    if type(checks) is not tuple:
        raise TypeError("checks must be a tuple")
    if any(type(item) is not ValidationCheckEvidence for item in checks):
        raise TypeError("checks must contain ValidationCheckEvidence values")
    operator_aborted = _nested_field(artifact, "operator_aborted")
    if type(operator_aborted) is not bool:
        raise TypeError("operator_aborted must be bool")
    schema_version = _nested_field(artifact, "schema_version")
    if (
        type(schema_version) is not int
        or schema_version != VALIDATION_ARTIFACT_SCHEMA_VERSION
    ):
        raise ValueError(f"unsupported validation artifact schema version: {schema_version!r}")
    # _classify itself invokes the procedure/check canonical validators. A malformed
    # nested provider result may be represented only as technical_invalid; it can
    # never be represented as PASS or reach complete=True/serialization as PASS.
    derived, reason = _classify(
        procedure,
        checks,
        completed_at=completed_at,
        operator_aborted=operator_aborted,
    )
    if classification is not derived or classification_reason != reason:
        raise ValueError(
            "validation artifact classification does not match procedure/check evidence: "
            f"declared={classification.value}/{classification_reason}, "
            f"derived={derived.value}/{reason}"
        )
    if not initialize:
        _validate_operator_seal(artifact, _artifact_snapshot(artifact))
    fingerprint = (
        artifact_id,
        id(procedure),
        started_at,
        completed_at,
        classification,
        classification_reason,
        tuple(id(item) for item in checks),
        operator_aborted,
        schema_version,
    )
    if initialize:
        object.__setattr__(artifact, "_binding_fingerprint", fingerprint)
        _register_operator_seal(artifact, _artifact_snapshot(artifact))
        return artifact
    try:
        bound = artifact._binding_fingerprint
    except AttributeError as exc:
        raise ValueError("validation artifact binding fingerprint is missing") from exc
    if bound != fingerprint:
        raise ValueError("validation artifact binding was mutated")
    return artifact


def _artifact_to_dict_raw(artifact: ValidationEvidenceArtifact) -> dict[str, object]:
    """validated artifact serializer that avoids public serializer recursion."""

    return {
        "schema_version": artifact.schema_version,
        "artifact_id": artifact.artifact_id,
        "procedure": _procedure_to_dict_raw(artifact.procedure),
        "started_at": artifact.started_at,
        "completed_at": artifact.completed_at,
        "classification": artifact.classification.value,
        "classification_reason": artifact.classification_reason,
        "evidence_class": _derive_evidence_class(artifact.procedure, artifact.checks).value,
        "checks": [_check_to_dict_raw(item) for item in artifact.checks],
        "operator_aborted": artifact.operator_aborted,
    }


def build_validation_artifact(
    procedure: ValidationProcedure,
    checks: Sequence[ValidationCheckEvidence],
    *,
    artifact_id: str,
    started_at: str,
    completed_at: str | None,
    operator_aborted: bool = False,
) -> ValidationEvidenceArtifact:
    """typed evidenceからclassificationを導出してartifactを構築する。"""

    if type(procedure) is not ValidationProcedure:
        raise TypeError("procedure must be ValidationProcedure")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise TypeError("checks must be a sequence")
    typed_checks = tuple(checks)
    if not all(type(item) is ValidationCheckEvidence for item in typed_checks):
        raise TypeError("checks must contain ValidationCheckEvidence values")
    classification, reason = _classify(
        procedure,
        typed_checks,
        completed_at=completed_at,
        operator_aborted=operator_aborted,
    )
    return ValidationEvidenceArtifact(
        artifact_id=artifact_id,
        procedure=procedure,
        started_at=started_at,
        completed_at=completed_at,
        classification=classification,
        classification_reason=reason,
        checks=typed_checks,
        operator_aborted=operator_aborted,
    )


def build_dry_run_validation_artifact(
    procedure: ValidationProcedure,
    checks: Sequence[ValidationCheckEvidence],
    *,
    artifact_id: str,
    started_at: str,
    completed_at: str | None,
    operator_aborted: bool = False,
) -> ValidationEvidenceArtifact:
    """software fixture専用のartifact builder。hardware pathを持たない。"""

    if type(procedure) is not ValidationProcedure:
        raise TypeError("procedure must be ValidationProcedure")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise TypeError("checks must be a sequence")
    typed_checks = tuple(checks)
    # procedure/checkの別のfieldがmalformedでも、physicalまたはmixed sourceを
    # software-only artifactへ流さない。physical sourceを一件でも含む入力は#509へ戻す。
    if _raw_physical_source_present(procedure, typed_checks):
        raise ValueError("dry-run builder rejects physical or mixed measurement sources; physical validation belongs to #509")
    try:
        _validate_validation_procedure(procedure)
    except Exception:
        # malformed procedureはdry-run gateを推測せず、technical_invalidへ閉じる。
        return build_validation_artifact(
            procedure,
            typed_checks,
            artifact_id=artifact_id,
            started_at=started_at,
            completed_at=completed_at,
            operator_aborted=operator_aborted,
        )
    if not procedure.dry_run_only:
        raise ValueError("dry-run builder requires a dry-run-only ValidationProcedure")
    if procedure.clearance.source.kind.is_physical:
        raise ValueError("dry-run builder rejects physical clearance sources; physical validation belongs to #509")
    if not all(type(item) is ValidationCheckEvidence for item in typed_checks):
        raise TypeError("checks must contain ValidationCheckEvidence values")
    if any(_check_has_physical_source(item) for item in typed_checks):
        raise ValueError("dry-run builder rejects physical measurement sources; physical validation belongs to #509")
    return build_validation_artifact(
        procedure,
        typed_checks,
        artifact_id=artifact_id,
        started_at=started_at,
        completed_at=completed_at,
        operator_aborted=operator_aborted,
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _decode_target(value: object) -> TargetIdentity:
    raw = _fields("target", value, {"target_id", "robot_id", "controller_id", "connection_id", "model_id"})
    return TargetIdentity(
        _text("target_id", raw["target_id"]),
        _text("robot_id", raw["robot_id"]),
        _text("controller_id", raw["controller_id"]),
        _text("connection_id", raw["connection_id"]),
        _text("model_id", raw["model_id"]),
    )


def _decode_operator(value: object) -> OperatorIdentity:
    raw = _fields("operator", value, {"operator_id", "role"})
    return OperatorIdentity(_text("operator_id", raw["operator_id"]), _text("role", raw["role"]))


def _decode_source(value: object) -> MeasurementSource:
    raw = _fields("measurement_source", value, {"kind", "source_id", "revision", "evidence_reference"})
    evidence = raw["evidence_reference"]
    if evidence is not None:
        evidence = _text("evidence_reference", evidence)
    return MeasurementSource(
        _enum(MeasurementSourceKind, "kind", raw["kind"]),
        _text("source_id", raw["source_id"]),
        _text("revision", raw["revision"]),
        evidence,
    )


def _decode_preflight(value: object) -> PreflightChecklist:
    raw = _fields("preflight", value, {"items", "acknowledged_by", "acknowledged_at"})
    items_raw = raw["items"]
    if not isinstance(items_raw, list):
        raise ValueError("preflight items must be an array")
    items: list[PreflightItem] = []
    for index, item in enumerate(items_raw):
        item_raw = _fields(f"preflight.items[{index}]", item, {"item_id", "description", "checked"})
        items.append(
            PreflightItem(
                _text("item_id", item_raw["item_id"]),
                _text("description", item_raw["description"]),
                item_raw["checked"],
            )
        )
    return PreflightChecklist(
        tuple(items),
        _text("acknowledged_by", raw["acknowledged_by"]),
        _timestamp("acknowledged_at", raw["acknowledged_at"]),
    )


def _decode_clearance(value: object) -> ClearanceDeclaration:
    raw = _fields("clearance", value, {"required_clearance_m", "verified_clearance_m", "source", "verified_at"})
    verified_at = raw["verified_at"]
    if verified_at is not None:
        verified_at = _timestamp("verified_at", verified_at)
    return ClearanceDeclaration(
        _finite_value("required_clearance_m", raw["required_clearance_m"]),
        None if raw["verified_clearance_m"] is None else _finite_value("verified_clearance_m", raw["verified_clearance_m"]),
        _decode_source(raw["source"]),
        verified_at,
    )


def _decode_stop(value: object) -> StopProcedure:
    raw = _fields("stop", value, {"normal_stop_steps", "emergency_stop_steps"})
    normal = raw["normal_stop_steps"]
    emergency = raw["emergency_stop_steps"]
    if not isinstance(normal, list) or not isinstance(emergency, list):
        raise ValueError("stop procedure steps must be arrays")
    return StopProcedure(tuple(_text("normal_stop_step", item) for item in normal), tuple(_text("emergency_stop_step", item) for item in emergency))


def _decode_rollback(value: object) -> RollbackProcedure:
    raw = _fields("rollback", value, {"steps", "target_state"})
    steps = raw["steps"]
    if not isinstance(steps, list):
        raise ValueError("rollback steps must be an array")
    return RollbackProcedure(tuple(_text("rollback_step", item) for item in steps), _text("target_state", raw["target_state"]))


def _decode_spec(value: object, index: int) -> ValidationCheckSpec:
    raw = _fields(f"required_checks[{index}]", value, {"check_id", "kind", "description"})
    return ValidationCheckSpec(
        _text("check_id", raw["check_id"]),
        _enum(ValidationCheckKind, "kind", raw["kind"]),
        _text("description", raw["description"]),
    )


def _decode_procedure(value: object) -> ValidationProcedure:
    raw = _fields(
        "procedure",
        value,
        {
            "procedure_id",
            "target",
            "operator",
            "software_revision",
            "created_at",
            "preflight",
            "clearance",
            "stop",
            "rollback",
            "required_checks",
            "operator_confirmed",
            "dry_run_only",
        },
    )
    required_raw = raw["required_checks"]
    if not isinstance(required_raw, list):
        raise ValueError("required_checks must be an array")
    return ValidationProcedure(
        _text("procedure_id", raw["procedure_id"]),
        _decode_target(raw["target"]),
        _decode_operator(raw["operator"]),
        _text("software_revision", raw["software_revision"]),
        _timestamp("created_at", raw["created_at"]),
        _decode_preflight(raw["preflight"]),
        _decode_clearance(raw["clearance"]),
        _decode_stop(raw["stop"]),
        _decode_rollback(raw["rollback"]),
        tuple(_decode_spec(item, index) for index, item in enumerate(required_raw)),
        raw["operator_confirmed"],
        raw["dry_run_only"],
    )


def _decode_safety_decision(value: object) -> SafetyDecisionEvidence:
    raw = _fields("safety_decision", value, {"action", "reason_identity", "provenance"})
    provenance = raw["provenance"]
    if not isinstance(provenance, list):
        raise ValueError("safety decision provenance must be an array")
    return SafetyDecisionEvidence(
        _enum(SafetyDecisionAction, "action", raw["action"]),
        _text("reason_identity", raw["reason_identity"]),
        tuple(_text("provenance", item) for item in provenance),
    )


def _decode_check(value: object, index: int) -> ValidationCheckEvidence:
    raw = _fields(
        f"checks[{index}]",
        value,
        {
            "check_id",
            "kind",
            "status",
            "expected",
            "observed",
            "measurement_source",
            "observed_at",
            "software_revision",
            "safety_decision",
            "reason",
        },
    )
    expected = _json_value(f"checks[{index}].expected", raw["expected"])
    observed = None if raw["observed"] is None else _json_value(f"checks[{index}].observed", raw["observed"])
    if not isinstance(expected, dict) or (observed is not None and not isinstance(observed, dict)):
        raise ValueError("check expected/observed must be objects")
    observed_at = raw["observed_at"]
    if observed_at is not None:
        observed_at = _timestamp("observed_at", observed_at)
    return ValidationCheckEvidence(
        _text("check_id", raw["check_id"]),
        _enum(ValidationCheckKind, "kind", raw["kind"]),
        _enum(ValidationCheckStatus, "status", raw["status"]),
        expected,
        observed,
        _decode_source(raw["measurement_source"]),
        observed_at,
        _text("software_revision", raw["software_revision"]),
        _decode_safety_decision(raw["safety_decision"]),
        _text("reason", raw["reason"]),
    )


def decode_validation_artifact(document: bytes | str | Mapping[str, object]) -> ValidationEvidenceArtifact:
    """unknown field、duplicate key、BOM、non-finite valueを拒否するstrict decoder。"""

    try:
        if isinstance(document, bytes):
            if document.startswith(b"\xef\xbb\xbf"):
                raise ValueError("validation artifact must not contain a UTF-8 BOM")
            raw_document = document.decode("utf-8")
            raw = json.loads(raw_document, object_pairs_hook=_reject_duplicate_pairs)
        elif isinstance(document, str):
            if document.startswith("\ufeff"):
                raise ValueError("validation artifact must not contain a UTF-8 BOM")
            raw = json.loads(document, object_pairs_hook=_reject_duplicate_pairs)
        else:
            raw = document
        root = _fields(
            "validation artifact",
            raw,
            {
                "schema_version",
                "artifact_id",
                "procedure",
                "started_at",
                "completed_at",
                "classification",
                "classification_reason",
                "evidence_class",
                "checks",
                "operator_aborted",
            },
        )
        checks_raw = root["checks"]
        if not isinstance(checks_raw, list):
            raise ValueError("checks must be an array")
        # Preserve the classifier identity precedence at the wire boundary:
        # duplicate actual IDs must be rejected before decoding nested decision
        # provenance, which may itself become stale after an ID rewrite.
        raw_check_ids: list[str] = []
        for index, item in enumerate(checks_raw):
            item_mapping = _mapping(f"checks[{index}]", item)
            if "check_id" in item_mapping:
                raw_check_ids.append(_text("check_id", item_mapping["check_id"]))
        if len(raw_check_ids) != len(set(raw_check_ids)):
            raise ValueError("check IDs must be unique")
        decoded_checks = tuple(
            _decode_check(item, index) for index, item in enumerate(checks_raw)
        )
        _validate_actual_check_ids(decoded_checks)
        artifact = ValidationEvidenceArtifact(
            artifact_id=_text("artifact_id", root["artifact_id"]),
            procedure=_decode_procedure(root["procedure"]),
            started_at=_timestamp("started_at", root["started_at"]),
            completed_at=None if root["completed_at"] is None else _timestamp("completed_at", root["completed_at"]),
            classification=_enum(ValidationClassification, "classification", root["classification"]),
            classification_reason=_text("classification_reason", root["classification_reason"]),
            checks=decoded_checks,
            operator_aborted=root["operator_aborted"],
            schema_version=root["schema_version"],
        )
        declared_evidence_class = _enum(EvidenceClass, "evidence_class", root["evidence_class"])
        if declared_evidence_class is not artifact.evidence_class:
            raise ValueError("evidence_class does not match check sources")
        return artifact
    except (TypeError, ValueError, OverflowError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("validation artifact"):
            raise
        raise ValueError(f"invalid validation artifact: {exc}") from exc


def validate_validation_artifact(artifact: ValidationEvidenceArtifact) -> ValidationEvidenceArtifact:
    """strict encode/decode/round-tripを通過したartifactを返す。"""

    if type(artifact) is not ValidationEvidenceArtifact:
        raise TypeError("artifact must be ValidationEvidenceArtifact")
    _validate_validation_artifact(artifact)
    encoded = artifact.to_json_bytes()
    decoded = decode_validation_artifact(encoded)
    if decoded.to_json_bytes() != encoded:
        raise ValueError("validation artifact JSON round-trip is not deterministic")
    return decoded


__all__ = [
    "ClearanceDeclaration",
    "EvidenceClass",
    "MeasurementSource",
    "MeasurementSourceKind",
    "OperatorIdentity",
    "PreflightChecklist",
    "PreflightItem",
    "ProcedureGateResult",
    "RollbackProcedure",
    "SafetyDecisionEvidence",
    "StopProcedure",
    "TargetIdentity",
    "ValidationCheckEvidence",
    "ValidationCheckKind",
    "ValidationCheckSpec",
    "ValidationCheckStatus",
    "ValidationClassification",
    "ValidationEvidenceArtifact",
    "ValidationProcedure",
    "build_dry_run_validation_artifact",
    "build_validation_artifact",
    "decode_validation_artifact",
    "validate_operator_gate",
    "validate_validation_procedure",
    "validate_validation_check_evidence",
    "validate_validation_artifact",
]
