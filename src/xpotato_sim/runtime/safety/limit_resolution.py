"""Joint / motor / actuator limit projection and parity resolution.

P1の``physical_limits``を入力として、conversion relationを明示的に適用し、
profile・TOML・MuJoCoのsoftware projectionとphysical sourceを混同しないread-only
resultを返す。ここではMuJoCoや設定値をphysical authorityへ昇格させない。
"""

from __future__ import annotations

import math
import operator
import weakref
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real
from threading import RLock
from typing import Any

from xpotato_sim.runtime.safety.physical_limits import (
    FAST_ARM_JOINT_SPACE_FRAME,
    EvidenceStatus,
    LimitConversionProvenance,
    LimitQuantity,
    LimitSourceProvenance,
    LimitSpace,
    PhysicalLimit,
    PhysicalSafetyEnvelope,
    _construct_projected_limit,
    _enum_value,
    _stored_enum_value,
    canonical_fast_arm_joint_space_frame,
    effective_limit_status,
    make_unknown_limit,
    source_identity,
    validate_concrete_limit_identity,
    validate_limit_conversion,
    validate_limit_source,
    validate_physical_limit,
)


class LimitResolutionStatus(str, Enum):
    """normalized joint boundの解決状態。"""

    RESOLVED_AUTHORITATIVE = "resolved_authoritative"
    RESOLVED_PROVISIONAL = "resolved_provisional"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class ParityStatus(str, Enum):
    """各software projectionとsourceのparity状態。"""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


DEFAULT_COMPARISON_TOLERANCE_RAD = 1e-9


# resolution DTOのauthorityはdataclass fieldの外部でsealする。public contentとprivate hintを
# callerが同時に書き換えても、外部sealは更新できない。
_RESOLUTION_SEALS: dict[
    int, tuple[weakref.ReferenceType[object], tuple[object, ...]]
] = {}
_RESOLUTION_SEALS_LOCK = RLock()


def _release_resolution_seal(
    key: int,
    reference: weakref.ReferenceType[object],
) -> None:
    with _RESOLUTION_SEALS_LOCK:
        entry = _RESOLUTION_SEALS.get(key)
        if entry is not None and entry[0] is reference:
            _RESOLUTION_SEALS.pop(key, None)


def _register_resolution_seal(
    value: object,
    snapshot: tuple[object, ...],
) -> None:
    key = id(value)
    reference = weakref.ref(
        value,
        lambda ref, key=key: _release_resolution_seal(key, ref),
    )
    with _RESOLUTION_SEALS_LOCK:
        _RESOLUTION_SEALS[key] = (reference, snapshot)


def _sealed_resolution_snapshot(value: object) -> tuple[object, ...]:
    key = id(value)
    with _RESOLUTION_SEALS_LOCK:
        entry = _RESOLUTION_SEALS.get(key)
        if entry is None or entry[0]() is not value:
            raise ValueError("resolution DTO is not constructor-sealed")
        return entry[1]


def _text(name: str, value: object) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be a built-in string")
    if not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def validate_limit_resolution_identity(name: str, value: object) -> str:
    """P2のrobot / joint identityをplaceholderから保護する。"""

    return validate_concrete_limit_identity(name, value)


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return 0.0 if number == 0.0 else number


def _stored_finite(name: str, value: object) -> float:
    """構築後の数値fieldをhookなしで検証する。"""

    if type(value) is not float:
        raise TypeError(f"{name} must be a canonical float")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return 0.0 if value == 0.0 else value


def _builtin_tuple(name: str, value: object) -> tuple[object, ...]:
    """aggregate fieldにbuilt-in tuple実装だけを要求する。"""

    if type(value) is not tuple:
        raise TypeError(f"{name} must be a built-in tuple")
    return value


def _exact_integer(name: str, value: object) -> int:
    """Python boolを除く整数プロトコルscalarだけを受け付ける。"""

    if isinstance(value, bool):
        raise TypeError(f"{name} must be an exact integer scalar")
    try:
        return operator.index(value)
    except TypeError as exc:
        raise TypeError(f"{name} must be an exact integer scalar") from exc


def _exact_binary(name: str, value: object) -> int:
    """numpy.bool_を含む0/1の整数・bool scalarだけを受け付ける。"""

    if type(value) is bool:
        return int(value)
    value_type = type(value)
    if value_type.__module__ == "numpy" and value_type.__name__ in {
        "bool",
        "bool_",
    }:
        return int(bool(value))
    integer = _exact_integer(name, value)
    if integer not in (0, 1):
        raise ValueError(f"{name} must be exactly 0 or 1")
    return integer


def _range(value: object, name: str) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) != 2:
        raise ValueError(f"{name} must contain exactly two values")
    lower, upper = _finite(f"{name}[0]", value[0]), _finite(f"{name}[1]", value[1])
    if lower > upper:
        raise ValueError(f"{name} lower must not exceed upper")
    return lower, upper


def _comparison_tolerance(value: object) -> float:
    tolerance = _finite("comparison_tolerance_rad", value)
    if tolerance != DEFAULT_COMPARISON_TOLERANCE_RAD:
        raise ValueError(
            "comparison_tolerance_rad must equal the canonical default"
        )
    return tolerance


def _stored_comparison_tolerance(value: object) -> float:
    tolerance = _stored_finite("comparison_tolerance_rad", value)
    if tolerance != DEFAULT_COMPARISON_TOLERANCE_RAD:
        raise ValueError(
            "comparison_tolerance_rad must equal the canonical default"
        )
    return tolerance


@dataclass(frozen=True, slots=True, weakref_slot=True)
class JointSpaceConversion:
    """motor / actuator値をjoint値へ射影する一意な関係。"""

    source_space: LimitSpace
    joint_name: str
    source_name: str
    gear_ratio: float
    sign: float
    offset: float
    relation_id: str
    unit: str
    _canonical_snapshot: tuple[object, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        try:
            source_space = _enum_value(LimitSpace, "source_space", self.source_space)
        except (TypeError, ValueError) as exc:
            raise ValueError("source_space must be motor or actuator") from exc
        if source_space is LimitSpace.JOINT:
            raise ValueError("source_space must be motor or actuator")
        object.__setattr__(self, "source_space", source_space)
        validate_limit_resolution_identity("joint_name", self.joint_name)
        validate_concrete_limit_identity("source_name", self.source_name)
        ratio = _finite("gear_ratio", self.gear_ratio)
        sign = _finite("sign", self.sign)
        offset = _finite("offset", self.offset)
        validate_concrete_limit_identity("relation_id", self.relation_id)
        _text("unit", self.unit)
        if ratio == 0.0:
            raise ValueError("gear_ratio must be non-zero")
        if sign not in (-1.0, 1.0):
            raise ValueError("sign must be either -1 or 1")
        object.__setattr__(self, "gear_ratio", ratio)
        object.__setattr__(self, "sign", sign)
        object.__setattr__(self, "offset", offset)
        snapshot = _conversion_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_resolution_seal(self, snapshot)

    @property
    def target_space(self) -> LimitSpace:
        return LimitSpace.JOINT

    def provenance(self) -> LimitConversionProvenance:
        _validate_joint_conversion(self)
        return LimitConversionProvenance.projected(
            source_space=self.source_space,
            relation_id=self.relation_id,
            gear_ratio=self.gear_ratio,
            sign=self.sign,
            offset=self.offset,
            source_name=self.source_name,
        )

    def source_to_joint(self, value: float) -> float:
        _validate_joint_conversion(self)
        try:
            projected = self.sign * (
                _finite("source value", value) / self.gear_ratio
            ) + self.offset
        except OverflowError as exc:
            raise ValueError("joint value must be finite") from exc
        return _finite("joint value", projected)

    def project_range(self, lower: float, upper: float) -> tuple[float, float]:
        _validate_joint_conversion(self)
        source_lower, source_upper = _range((lower, upper), "source range")
        projected = (self.source_to_joint(source_lower), self.source_to_joint(source_upper))
        return (min(projected), max(projected))


@dataclass(frozen=True, slots=True, weakref_slot=True)
class LimitParityRecord:
    """1つのjointについて、sourceごとのparityをmachine-readableに保持する。"""

    joint_name: str
    source_name: str
    status: ParityStatus
    lower: float | None
    upper: float | None
    unit: str
    reason: str | None = None
    source: LimitSourceProvenance | None = None
    source_status: EvidenceStatus | None = None
    conversion: LimitConversionProvenance | None = None
    frame: str = FAST_ARM_JOINT_SPACE_FRAME
    _canonical_snapshot: tuple[object, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        validate_limit_resolution_identity("joint_name", self.joint_name)
        _text("source_name", self.source_name)
        try:
            status = _enum_value(ParityStatus, "status", self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("status must be a valid ParityStatus") from exc
        object.__setattr__(self, "status", status)
        _text("unit", self.unit)
        _text("frame", self.frame)
        lower = _finite("lower", self.lower) if self.lower is not None else None
        upper = _finite("upper", self.upper) if self.upper is not None else None
        if (lower is None) != (upper is None):
            raise ValueError("parity lower and upper must be provided together")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("parity lower must not exceed upper")
        if self.reason is not None:
            _text("reason", self.reason)
        if self.source is not None and type(self.source) is not LimitSourceProvenance:
            raise TypeError("source must be LimitSourceProvenance or None")
        if self.source is not None:
            # 自由文字列からauthorityを推測せず、P2 source validatorを再利用する。
            validate_limit_source(self.source)
            if self.source_name != source_identity(self.source, unit=self.unit):
                raise ValueError("parity source_name must match typed source identity")
        else:
            raise ValueError("parity requires typed source provenance")
        conversion = self.conversion
        if conversion is not None:
            validate_limit_conversion(conversion)
            if conversion.target_space is not LimitSpace.JOINT:
                raise ValueError("parity conversion must target joint space")
        source_status = self.source_status
        if source_status is not None:
            try:
                source_status = _enum_value(
                    EvidenceStatus,
                    "source_status",
                    source_status,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("source_status must be a valid EvidenceStatus") from exc
            if self.source is not None and source_status is not self.source.status:
                raise ValueError("source_status must match typed source status")
        elif self.source is not None:
            source_status = self.source.status
        if self.status is ParityStatus.MATCH:
            if lower is None or upper is None:
                raise ValueError("matched parity requires both lower and upper")
            if self.reason is not None:
                raise ValueError("matched parity must not have a reason")
            if self.source is None:
                raise ValueError("matched parity requires typed source provenance")
            if source_status in {
                EvidenceStatus.UNKNOWN,
                EvidenceStatus.UNAVAILABLE,
                EvidenceStatus.CONFLICT,
                EvidenceStatus.INVALID,
            }:
                raise ValueError("matched parity requires a usable typed source status")
        elif self.reason is None:
            raise ValueError(f"{self.status.value} parity requires a reason")
        if self.status in {
            ParityStatus.UNKNOWN,
            ParityStatus.UNAVAILABLE,
            ParityStatus.INVALID,
        } and (lower is not None or upper is not None):
            raise ValueError(f"{self.status.value} parity must not contain bounds")
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "source_status", source_status)
        object.__setattr__(self, "conversion", conversion)
        snapshot = _parity_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_resolution_seal(self, snapshot)
        _validate_parity_record(self)

    def to_dict(self) -> dict[str, object]:
        _validate_parity_record(self)
        result: dict[str, object] = {
            "joint_name": self.joint_name,
            "source_name": self.source_name,
            "status": self.status.value,
            "lower": self.lower,
            "upper": self.upper,
            "unit": self.unit,
            "frame": self.frame,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        if self.source is not None:
            result["source"] = self.source.to_dict()
        if self.source_status is not None and self.source is None:
            result["source_status"] = self.source_status.value
        if self.conversion is not None:
            result["conversion"] = self.conversion.to_dict()
        return result

@dataclass(frozen=True, slots=True, weakref_slot=True)
class ResolvedJointBound:
    """normalized joint-space rangeとsource identity。"""

    joint_name: str
    lower_rad: float | None
    upper_rad: float | None
    status: LimitResolutionStatus
    source_names: tuple[str, ...]
    parity: tuple[LimitParityRecord, ...]
    reason: str | None = None
    comparison_tolerance_rad: float = DEFAULT_COMPARISON_TOLERANCE_RAD
    _canonical_snapshot: tuple[object, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        validate_limit_resolution_identity("joint_name", self.joint_name)
        try:
            status = _enum_value(LimitResolutionStatus, "status", self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("status must be a valid LimitResolutionStatus") from exc
        object.__setattr__(self, "status", status)
        _builtin_tuple("source_names", self.source_names)
        if not self.source_names:
            raise ValueError("resolved bound requires at least one source name")
        for source_name in self.source_names:
            _text("source_name", source_name)
        if len(set(self.source_names)) != len(self.source_names):
            raise ValueError("resolved bound source names must be unique")
        lower_rad = _finite("lower_rad", self.lower_rad) if self.lower_rad is not None else None
        upper_rad = _finite("upper_rad", self.upper_rad) if self.upper_rad is not None else None
        if (lower_rad is None) != (upper_rad is None):
            raise ValueError("resolved bound lower_rad and upper_rad must be provided together")
        if lower_rad is not None and upper_rad is not None and lower_rad > upper_rad:
            raise ValueError("resolved bound lower_rad must not exceed upper_rad")
        _builtin_tuple("parity", self.parity)
        if not all(type(item) is LimitParityRecord for item in self.parity):
            raise TypeError("parity must contain LimitParityRecord values")
        if not self.parity:
            raise ValueError("resolved bound parity must be non-empty")
        if len(self.source_names) != len(self.parity):
            raise ValueError("resolved bound source_names and parity must have equal length")
        for item in self.parity:
            _validate_parity_record(item)
        if tuple(item.source_name for item in self.parity) != self.source_names:
            raise ValueError("resolved bound source_names must exactly match parity identities")
        if any(item.joint_name != self.joint_name for item in self.parity):
            raise ValueError("resolved bound parity joint identity must match bound joint")
        tolerance = _comparison_tolerance(self.comparison_tolerance_rad)
        if self.reason is not None:
            _text("reason", self.reason)
        if self.status in {
            LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
            LimitResolutionStatus.RESOLVED_PROVISIONAL,
        }:
            if lower_rad is None or upper_rad is None:
                raise ValueError("resolved bound requires both lower_rad and upper_rad")
            if any(item.status is not ParityStatus.MATCH for item in self.parity):
                raise ValueError("resolved bound parity statuses must all be match")
            if any(item.unit != "rad" for item in self.parity):
                raise ValueError("resolved bound parity units must be rad")
            if any(
                item.frame != canonical_fast_arm_joint_space_frame()
                for item in self.parity
            ):
                raise ValueError(
                    "resolved bound parity frames must use the canonical "
                    "fast_arm joint-space frame"
                )
            for item in self.parity:
                if item.lower is None or item.upper is None:
                    raise ValueError("resolved bound parity requires finite ranges")
                if (
                    abs(item.lower - lower_rad) > tolerance
                    or abs(item.upper - upper_rad) > tolerance
                ):
                    raise ValueError("resolved bound parity ranges must match normalized bounds")
            if self.reason is not None:
                raise ValueError("resolved bound must not have a reason")
            if self.status is LimitResolutionStatus.RESOLVED_AUTHORITATIVE and not any(
                item.source is not None
                and item.source.status is EvidenceStatus.AUTHORITATIVE
                for item in self.parity
            ):
                raise ValueError(
                    "resolved authoritative bound requires typed authoritative source provenance"
                )
        else:
            if lower_rad is not None or upper_rad is not None:
                raise ValueError(f"{self.status.value} bound must not contain bounds")
            if not self.reason:
                raise ValueError(f"{self.status.value} bound requires a reason")
        object.__setattr__(self, "lower_rad", lower_rad)
        object.__setattr__(self, "upper_rad", upper_rad)
        object.__setattr__(self, "comparison_tolerance_rad", tolerance)
        snapshot = _bound_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_resolution_seal(self, snapshot)
        _validate_resolved_bound(self)

    @property
    def authoritative(self) -> bool:
        try:
            _validate_resolved_bound(self)
        except (AttributeError, TypeError, ValueError):
            return False
        return self.status is LimitResolutionStatus.RESOLVED_AUTHORITATIVE

    @property
    def bounded(self) -> bool:
        try:
            _validate_resolved_bound(self)
        except (AttributeError, TypeError, ValueError):
            return False
        return self.lower_rad is not None and self.upper_rad is not None

    def to_dict(self) -> dict[str, object]:
        _validate_resolved_bound(self)
        result: dict[str, object] = {
            "joint_name": self.joint_name,
            "lower_rad": self.lower_rad,
            "upper_rad": self.upper_rad,
            "status": self.status.value,
            "source_names": list(self.source_names),
            "parity": [item.to_dict() for item in self.parity],
            "comparison_tolerance_rad": self.comparison_tolerance_rad,
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True, slots=True, weakref_slot=True)
class LimitResolutionResult:
    """全jointのnormalized result。mutable simulatorや設定を書き換えない。"""

    schema_version: int
    robot_id: str
    bounds: tuple[ResolvedJointBound, ...]
    conversion_relations: tuple[JointSpaceConversion, ...]
    expected_joint_names: tuple[str, ...]
    comparison_tolerance_rad: float = DEFAULT_COMPARISON_TOLERANCE_RAD
    _canonical_snapshot: tuple[object, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError(f"unsupported limit resolution schema version: {self.schema_version!r}")
        validate_limit_resolution_identity("robot_id", self.robot_id)
        _builtin_tuple("bounds", self.bounds)
        if not self.bounds:
            raise ValueError("limit resolution requires at least one bound")
        if any(type(item) is not ResolvedJointBound for item in self.bounds):
            raise TypeError("bounds must contain ResolvedJointBound values")
        for bound in self.bounds:
            _validate_resolved_bound(bound)
        names = tuple(item.joint_name for item in self.bounds)
        if len(set(names)) != len(names):
            raise ValueError("limit resolution joint names must be unique")
        _builtin_tuple("expected_joint_names", self.expected_joint_names)
        expected_names = tuple(
            validate_limit_resolution_identity("expected_joint_name", name)
            for name in self.expected_joint_names
        )
        if not expected_names:
            raise ValueError("expected_joint_names must be non-empty")
        if len(set(expected_names)) != len(expected_names):
            raise ValueError("expected_joint_names must be unique")
        if expected_names != names:
            raise ValueError("bounds must exactly cover expected_joint_names in canonical order")
        _builtin_tuple("conversion_relations", self.conversion_relations)
        relation_sources: set[str] = set()
        relation_ids: set[str] = set()
        for relation in self.conversion_relations:
            _validate_joint_conversion(relation)
            if relation.joint_name not in expected_names:
                raise ValueError(
                    "conversion relation target joint is not expected: "
                    f"{relation.joint_name}"
                )
            if relation.source_name in relation_sources:
                raise ValueError(
                    f"duplicate conversion relation for source: {relation.source_name}"
                )
            if relation.relation_id in relation_ids:
                raise ValueError(
                    f"duplicate conversion relation id: {relation.relation_id}"
                )
            relation_sources.add(relation.source_name)
            relation_ids.add(relation.relation_id)
        tolerance = _comparison_tolerance(self.comparison_tolerance_rad)
        if any(
            bound.comparison_tolerance_rad != tolerance
            for bound in self.bounds
        ):
            raise ValueError("bound comparison tolerance must match result tolerance")
        object.__setattr__(self, "expected_joint_names", expected_names)
        object.__setattr__(self, "comparison_tolerance_rad", tolerance)
        snapshot = _result_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_resolution_seal(self, snapshot)
        _validate_limit_resolution_result(self)

    @property
    def all_resolved(self) -> bool:
        try:
            _validate_limit_resolution_result(self)
        except (AttributeError, TypeError, ValueError):
            return False
        return all(item.bounded for item in self.bounds)

    @property
    def authoritative(self) -> bool:
        try:
            _validate_limit_resolution_result(self)
        except (AttributeError, TypeError, ValueError):
            return False
        return all(item.authoritative for item in self.bounds)

    def bound_for(self, joint_name: str) -> ResolvedJointBound:
        _validate_limit_resolution_result(self)
        joint_name = validate_limit_resolution_identity("joint_name", joint_name)
        for bound in self.bounds:
            if bound.joint_name == joint_name:
                return bound
        raise KeyError(joint_name)

    def to_dict(self) -> dict[str, object]:
        _validate_limit_resolution_result(self)
        return {
            "schema_version": self.schema_version,
            "robot_id": self.robot_id,
            "expected_joint_names": list(self.expected_joint_names),
            "comparison_tolerance_rad": self.comparison_tolerance_rad,
            "bounds": [item.to_dict() for item in self.bounds],
            "conversion_relations": [
                {
                    "source_space": item.source_space.value,
                    "joint_name": item.joint_name,
                    "source_name": item.source_name,
                    "gear_ratio": item.gear_ratio,
                    "sign": item.sign,
                    "offset": item.offset,
                    "relation_id": item.relation_id,
                    "unit": item.unit,
                }
                for item in self.conversion_relations
            ],
        }


def authoritative_position_bound_violations(
    result: LimitResolutionResult,
    *,
    joint_names: tuple[str, ...],
    qpos_rad: tuple[float, ...],
) -> tuple[str, ...]:
    """実評価candidateをauthoritativeなjoint-position boundへ照合する。"""

    _validate_limit_resolution_result(result)
    _builtin_tuple("joint_names", joint_names)
    canonical_joint_names = tuple(
        validate_limit_resolution_identity("joint_name", name)
        for name in joint_names
    )
    if canonical_joint_names != result.expected_joint_names:
        raise ValueError(
            "candidate joint order must exactly match resolved limit inventory"
        )
    _builtin_tuple("qpos_rad", qpos_rad)
    if len(qpos_rad) != len(canonical_joint_names):
        raise ValueError("candidate qpos dimension must match resolved limit inventory")
    positions = tuple(
        _finite(f"qpos_rad[{index}]", value)
        for index, value in enumerate(qpos_rad)
    )
    violations: list[str] = []
    for joint_name, position, bound in zip(
        canonical_joint_names,
        positions,
        result.bounds,
        strict=True,
    ):
        if bound.status is not LimitResolutionStatus.RESOLVED_AUTHORITATIVE:
            raise ValueError(
                "candidate position check requires authoritative resolved bounds"
            )
        if bound.lower_rad is None or bound.upper_rad is None:
            raise ValueError(
                "candidate position check requires bounded authoritative ranges"
            )
        if position < bound.lower_rad or position > bound.upper_rad:
            violations.append(joint_name)
    return tuple(violations)


def _conversion_snapshot(
    relation: JointSpaceConversion,
) -> tuple[object, ...]:
    return (
        relation.source_space,
        relation.joint_name,
        relation.source_name,
        relation.gear_ratio,
        relation.sign,
        relation.offset,
        relation.relation_id,
        relation.unit,
    )


def _parity_snapshot(
    parity: LimitParityRecord,
) -> tuple[object, ...]:
    source_snapshot = None
    if parity.source is not None:
        source_snapshot = (
            id(parity.source),
            parity.source.source_kind,
            parity.source.source_id,
            parity.source.revision,
            parity.source.status,
            parity.source.evidence_reference,
            parity.source.observed_at,
            parity.source.notes,
        )
    conversion_snapshot = None
    if parity.conversion is not None:
        conversion_snapshot = (
            id(parity.conversion),
            parity.conversion.source_space,
            parity.conversion.target_space,
            parity.conversion.method,
            parity.conversion.relation_id,
            parity.conversion.gear_ratio,
            parity.conversion.sign,
            parity.conversion.offset,
        )
    return (
        parity.joint_name,
        parity.source_name,
        parity.status,
        parity.lower,
        parity.upper,
        parity.unit,
        parity.frame,
        parity.reason,
        source_snapshot,
        parity.source_status,
        conversion_snapshot,
    )


def _bound_snapshot(
    bound: ResolvedJointBound,
) -> tuple[object, ...]:
    return (
        bound.joint_name,
        bound.lower_rad,
        bound.upper_rad,
        bound.status,
        bound.source_names,
        tuple((id(item), _parity_snapshot(item)) for item in bound.parity),
        bound.reason,
        bound.comparison_tolerance_rad,
    )


def _result_snapshot(
    result: LimitResolutionResult,
) -> tuple[object, ...]:
    return (
        result.schema_version,
        result.robot_id,
        tuple((id(item), _bound_snapshot(item)) for item in result.bounds),
        tuple(
            (id(item), _conversion_snapshot(item))
            for item in result.conversion_relations
        ),
        result.expected_joint_names,
        result.comparison_tolerance_rad,
    )


def _validate_joint_conversion(
    relation: object,
) -> JointSpaceConversion:
    if type(relation) is not JointSpaceConversion:
        raise TypeError("conversion relation must be JointSpaceConversion")
    source_space = _stored_enum_value(
        LimitSpace,
        "source_space",
        relation.source_space,
    )
    if source_space is LimitSpace.JOINT:
        raise ValueError("source_space must be motor or actuator")
    validate_limit_resolution_identity("joint_name", relation.joint_name)
    validate_concrete_limit_identity("source_name", relation.source_name)
    ratio = _stored_finite("gear_ratio", relation.gear_ratio)
    sign = _stored_finite("sign", relation.sign)
    offset = _stored_finite("offset", relation.offset)
    validate_concrete_limit_identity("relation_id", relation.relation_id)
    _text("unit", relation.unit)
    if ratio == 0.0:
        raise ValueError("gear_ratio must be non-zero")
    if sign not in (-1.0, 1.0):
        raise ValueError("sign must be either -1 or 1")
    expected = (
        source_space,
        relation.joint_name,
        relation.source_name,
        ratio,
        sign,
        offset,
        relation.relation_id,
        relation.unit,
    )
    if _sealed_resolution_snapshot(relation) != expected:
        raise ValueError("conversion relation has been mutated or bypassed")
    return relation


def _validate_parity_record(
    parity: object,
) -> LimitParityRecord:
    if type(parity) is not LimitParityRecord:
        raise TypeError("parity must contain LimitParityRecord values")
    joint_name = validate_limit_resolution_identity("joint_name", parity.joint_name)
    source_name = _text("source_name", parity.source_name)
    status = _stored_enum_value(ParityStatus, "status", parity.status)
    _text("unit", parity.unit)
    _text("frame", parity.frame)
    lower = _stored_finite("lower", parity.lower) if parity.lower is not None else None
    upper = _stored_finite("upper", parity.upper) if parity.upper is not None else None
    if (lower is None) != (upper is None):
        raise ValueError("parity lower and upper must be provided together")
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("parity lower must not exceed upper")
    if parity.reason is not None:
        _text("reason", parity.reason)
    source_status = (
        None
        if parity.source_status is None
        else _stored_enum_value(
            EvidenceStatus,
            "source_status",
            parity.source_status,
        )
    )
    if parity.source is not None:
        validate_limit_source(parity.source)
        canonical_name = source_identity(parity.source, unit=parity.unit)
        if source_name != canonical_name:
            raise ValueError("parity source_name must match typed source identity")
        if source_status is not parity.source.status:
            raise ValueError("source_status must match typed source status")
    if parity.conversion is not None:
        validate_limit_conversion(parity.conversion)
        if parity.conversion.target_space is not LimitSpace.JOINT:
            raise ValueError("parity conversion must target joint space")
    if status is ParityStatus.MATCH:
        if lower is None or upper is None:
            raise ValueError("matched parity requires both lower and upper")
        if parity.reason is not None:
            raise ValueError("matched parity must not have a reason")
        if parity.source is None:
            raise ValueError("matched parity requires typed source provenance")
        if source_status in {
            EvidenceStatus.UNKNOWN,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.CONFLICT,
            EvidenceStatus.INVALID,
        }:
            raise ValueError("matched parity requires a usable typed source status")
    elif parity.reason is None:
        raise ValueError(f"{status.value} parity requires a reason")
    if status in {
        ParityStatus.UNKNOWN,
        ParityStatus.UNAVAILABLE,
        ParityStatus.INVALID,
    } and (lower is not None or upper is not None):
        raise ValueError(f"{status.value} parity must not contain bounds")
    expected = _parity_snapshot(parity)
    if _sealed_resolution_snapshot(parity) != expected:
        raise ValueError("parity has been mutated or bypassed")
    return parity


def _validate_resolved_bound(
    bound: object,
) -> ResolvedJointBound:
    if type(bound) is not ResolvedJointBound:
        raise TypeError("bound must be ResolvedJointBound")
    joint_name = validate_limit_resolution_identity("joint_name", bound.joint_name)
    status = _stored_enum_value(
        LimitResolutionStatus,
        "status",
        bound.status,
    )
    _builtin_tuple("source_names", bound.source_names)
    if not bound.source_names:
        raise ValueError("resolved bound source names must be a non-empty tuple")
    for source_name in bound.source_names:
        _text("source_name", source_name)
    if len(set(bound.source_names)) != len(bound.source_names):
        raise ValueError("resolved bound source names must be unique")
    lower = _stored_finite("lower_rad", bound.lower_rad) if bound.lower_rad is not None else None
    upper = _stored_finite("upper_rad", bound.upper_rad) if bound.upper_rad is not None else None
    if (lower is None) != (upper is None):
        raise ValueError("resolved bound lower_rad and upper_rad must be provided together")
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("resolved bound lower_rad must not exceed upper_rad")
    _builtin_tuple("parity", bound.parity)
    if not bound.parity:
        raise ValueError("resolved bound parity must be non-empty")
    for item in bound.parity:
        _validate_parity_record(item)
    if tuple(item.source_name for item in bound.parity) != bound.source_names:
        raise ValueError("resolved bound source_names must exactly match parity identities")
    if any(item.joint_name != joint_name for item in bound.parity):
        raise ValueError("resolved bound parity joint identity must match bound joint")
    tolerance = _stored_comparison_tolerance(bound.comparison_tolerance_rad)
    if status in {
        LimitResolutionStatus.RESOLVED_AUTHORITATIVE,
        LimitResolutionStatus.RESOLVED_PROVISIONAL,
    }:
        if lower is None or upper is None:
            raise ValueError("resolved bound requires both lower_rad and upper_rad")
        if any(item.status is not ParityStatus.MATCH for item in bound.parity):
            raise ValueError("resolved bound parity statuses must all be match")
        if any(item.unit != "rad" for item in bound.parity):
            raise ValueError("resolved bound parity units must be rad")
        if any(
            item.frame != canonical_fast_arm_joint_space_frame()
            for item in bound.parity
        ):
            raise ValueError(
                "resolved bound parity frames must use the canonical "
                "fast_arm joint-space frame"
            )
        for item in bound.parity:
            if item.lower is None or item.upper is None:
                raise ValueError("resolved bound parity requires finite ranges")
            if abs(item.lower - lower) > tolerance or abs(item.upper - upper) > tolerance:
                raise ValueError("resolved bound parity ranges must match normalized bounds")
        if bound.reason is not None:
            raise ValueError("resolved bound must not have a reason")
        if status is LimitResolutionStatus.RESOLVED_AUTHORITATIVE and not any(
            item.source is not None
            and item.source.status is EvidenceStatus.AUTHORITATIVE
            for item in bound.parity
        ):
            raise ValueError("resolved authoritative bound requires typed authoritative source provenance")
    else:
        if lower is not None or upper is not None:
            raise ValueError(f"{status.value} bound must not contain bounds")
        if not bound.reason:
            raise ValueError(f"{status.value} bound requires a reason")
    if bound.reason is not None:
        _text("reason", bound.reason)
    expected = _bound_snapshot(bound)
    if _sealed_resolution_snapshot(bound) != expected:
        raise ValueError("resolved bound has been mutated or bypassed")
    return bound


def _validate_limit_resolution_result(
    result: object,
) -> LimitResolutionResult:
    if type(result) is not LimitResolutionResult:
        raise TypeError("result must be LimitResolutionResult")
    if type(result.schema_version) is not int or result.schema_version != 1:
        raise ValueError("unsupported limit resolution schema version")
    validate_limit_resolution_identity("robot_id", result.robot_id)
    _builtin_tuple("bounds", result.bounds)
    if not result.bounds:
        raise ValueError("limit resolution requires at least one bound")
    for bound in result.bounds:
        _validate_resolved_bound(bound)
    bound_names = tuple(bound.joint_name for bound in result.bounds)
    if len(set(bound_names)) != len(bound_names):
        raise ValueError("limit resolution joint names must be unique")
    _builtin_tuple("expected_joint_names", result.expected_joint_names)
    if not result.expected_joint_names:
        raise ValueError("expected_joint_names must be a non-empty tuple")
    expected_names = tuple(
        validate_limit_resolution_identity("expected_joint_name", name)
        for name in result.expected_joint_names
    )
    if len(set(expected_names)) != len(expected_names) or bound_names != expected_names:
        raise ValueError("bounds must exactly cover expected_joint_names in canonical order")
    _builtin_tuple("conversion_relations", result.conversion_relations)
    for relation in result.conversion_relations:
        _validate_joint_conversion(relation)
    relations_by_id = {
        relation.relation_id: relation for relation in result.conversion_relations
    }
    required_relation_ids: set[str] = set()
    for bound in result.bounds:
        for parity in bound.parity:
            conversion = parity.conversion
            if conversion is None or conversion.source_space is LimitSpace.JOINT:
                continue
            required_relation_ids.add(conversion.relation_id)
            relation = relations_by_id.get(conversion.relation_id)
            if relation is None:
                raise ValueError("parity conversion relation is missing from result")
            if (
                relation.source_space is not conversion.source_space
                or relation.joint_name != bound.joint_name
                or relation.source_name != conversion.source_name
                or relation.unit != parity.unit
                or relation.gear_ratio != conversion.gear_ratio
                or relation.sign != conversion.sign
                or relation.offset != conversion.offset
            ):
                raise ValueError("parity conversion relation binding is inconsistent")
    if required_relation_ids != set(relations_by_id):
        raise ValueError("result conversion relations must exactly cover projected parity")
    tolerance = _stored_comparison_tolerance(result.comparison_tolerance_rad)
    if any(bound.comparison_tolerance_rad != tolerance for bound in result.bounds):
        raise ValueError("bound comparison tolerance must match result tolerance")
    expected = _result_snapshot(result)
    if _sealed_resolution_snapshot(result) != expected:
        raise ValueError("limit resolution result has been mutated or bypassed")
    return result


def validate_resolved_joint_bound(bound: ResolvedJointBound) -> ResolvedJointBound:
    return _validate_resolved_bound(bound)


def validate_limit_parity_record(
    parity: LimitParityRecord,
) -> LimitParityRecord:
    return _validate_parity_record(parity)


def validate_limit_resolution_result(
    result: LimitResolutionResult,
) -> LimitResolutionResult:
    return _validate_limit_resolution_result(result)


def project_limit_to_joint_space(
    limit: PhysicalLimit,
    conversion: JointSpaceConversion,
    *,
    joint_name: str | None = None,
) -> PhysicalLimit:
    """1つのsource rangeを明示conversionでjoint spaceへ投影する。"""

    validate_physical_limit(limit)
    _validate_joint_conversion(conversion)
    if limit.space is not conversion.source_space:
        raise ValueError(
            "limit/conversion source space mismatch: "
            f"{limit.space.value} != {conversion.source_space.value}"
        )
    if limit.name != conversion.source_name:
        raise ValueError(
            "limit/conversion source identity mismatch: "
            f"{limit.name} != {conversion.source_name}"
        )
    if limit.unit != conversion.unit:
        raise ValueError(
            "limit/conversion unit mismatch: "
            f"{limit.unit} != {conversion.unit}"
        )
    if joint_name is not None and validate_limit_resolution_identity("joint_name", joint_name) != conversion.joint_name:
        raise ValueError(
            "conversion target joint identity mismatch: "
            f"{joint_name} != {conversion.joint_name}"
        )
    target_name = conversion.joint_name
    lower = upper = None
    if limit.lower is not None and limit.upper is not None:
        lower, upper = conversion.project_range(limit.lower, limit.upper)
    status = limit.status
    return _construct_projected_limit(
        name=target_name,
        quantity=limit.quantity,
        lower=lower,
        upper=upper,
        unit=limit.unit,
        frame=canonical_fast_arm_joint_space_frame(),
        status=status,
        source=limit.source,
        conversion=conversion.provenance(),
        reason=limit.reason,
    )


def _unknown_projected_limit(
    limit: PhysicalLimit,
    *,
    name: str,
    reason: str,
) -> PhysicalLimit:
    """conversion不能時も元source provenanceを失わずunknownへ閉じる。"""

    return PhysicalLimit(
        name=name,
        quantity=limit.quantity,
        lower=None,
        upper=None,
        unit=limit.unit,
        space=LimitSpace.JOINT,
        frame=canonical_fast_arm_joint_space_frame(),
        status=EvidenceStatus.UNKNOWN,
        source=limit.source,
        conversion=LimitConversionProvenance.identity(LimitSpace.JOINT),
        reason=reason,
    )


def _status_for_limit(limit: PhysicalLimit) -> tuple[ParityStatus, str | None]:
    status = effective_limit_status(limit)
    if status is EvidenceStatus.INVALID:
        return ParityStatus.INVALID, limit.reason or "invalid limit/source status"
    if status is EvidenceStatus.CONFLICT:
        return ParityStatus.MISMATCH, limit.reason or "conflicting limit/source status"
    if status is EvidenceStatus.UNAVAILABLE:
        return ParityStatus.UNAVAILABLE, limit.reason or "limit source unavailable"
    if status is EvidenceStatus.UNKNOWN:
        return ParityStatus.UNKNOWN, limit.reason or "limit source unknown"
    return ParityStatus.MATCH, None


def _joint_space_frame_reason(limit: PhysicalLimit) -> str | None:
    """fast_arm joint-space frameのbindingを検証する。"""

    if limit.space is not LimitSpace.JOINT:
        return None
    canonical_frame = canonical_fast_arm_joint_space_frame()
    if limit.frame == canonical_frame:
        return None
    return (
        "joint limit frame does not match the canonical fast_arm joint-space "
        f"frame: {limit.frame!r} != {canonical_frame!r}"
    )


def resolve_joint_space_bounds(
    limits: Sequence[PhysicalLimit],
    *,
    expected_joint_names: Sequence[str],
    robot_id: str,
    tolerance_rad: float = DEFAULT_COMPARISON_TOLERANCE_RAD,
    conversion_relations: Sequence[JointSpaceConversion] = (),
) -> LimitResolutionResult:
    """同一jointの複数sourceを比較し、fail-closedなnormalized resultを作る。"""

    tolerance_rad = _comparison_tolerance(tolerance_rad)
    names = tuple(
        validate_limit_resolution_identity("expected_joint_name", name)
        for name in expected_joint_names
    )
    if not names or len(set(names)) != len(names):
        raise ValueError("expected_joint_names must be unique and non-empty")
    validate_limit_resolution_identity("robot_id", robot_id)
    source_relation_map: dict[str, JointSpaceConversion] = {}
    relation_ids: set[str] = set()
    for relation in conversion_relations:
        _validate_joint_conversion(relation)
        if relation.joint_name not in names:
            raise ValueError(
                "conversion relation target joint is not expected: "
                f"{relation.joint_name}"
            )
        if relation.source_name in source_relation_map:
            raise ValueError(
                "duplicate conversion relation for source: "
                f"{relation.source_name}"
            )
        if relation.relation_id in relation_ids:
            raise ValueError(f"duplicate conversion relation id: {relation.relation_id}")
        source_relation_map[relation.source_name] = relation
        relation_ids.add(relation.relation_id)
    projected: list[PhysicalLimit] = []
    validated_limits: list[PhysicalLimit] = []
    for limit in limits:
        validate_physical_limit(limit)
        validated_limits.append(limit)
        if limit.quantity is not LimitQuantity.POSITION:
            if limit.space in {LimitSpace.MOTOR, LimitSpace.ACTUATOR}:
                raise ValueError(
                    "every motor/actuator limit supplied to resolution must be a position limit"
                )
            continue
        if limit.space is LimitSpace.JOINT:
            if limit.name not in names:
                raise ValueError(
                    "joint limit target identity is not expected: "
                    f"{limit.name}"
                )
            projected.append(limit)
            continue
        relation = source_relation_map.get(limit.name)
        if relation is None:
            raise ValueError(
                "conversion relation missing for "
                f"{limit.space.value} source {limit.name}"
            )
        try:
            projected.append(project_limit_to_joint_space(limit, relation))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"conversion failed for {limit.name}: {exc}"
            )
    provided_non_joint_names = {
        limit.name
        for limit in validated_limits
        if limit.quantity is LimitQuantity.POSITION
        and limit.space in {LimitSpace.MOTOR, LimitSpace.ACTUATOR}
    }
    relation_names = set(source_relation_map)
    extra_relations = relation_names - provided_non_joint_names
    if extra_relations:
        raise ValueError(
            "conversion relation source has no matching provided limit: "
            f"{sorted(extra_relations)!r}"
        )
    by_joint: dict[str, list[PhysicalLimit]] = {name: [] for name in names}
    for limit in projected:
        if limit.name in by_joint:
            by_joint[limit.name].append(limit)
    bounds: list[ResolvedJointBound] = []
    for joint_name in names:
        candidates = by_joint[joint_name]
        if not candidates:
            unknown = make_unknown_limit(
                name=joint_name,
                quantity=LimitQuantity.POSITION,
                space=LimitSpace.JOINT,
                unit="rad",
                frame=canonical_fast_arm_joint_space_frame(),
                reason="no limit source was supplied",
            )
            candidates = [unknown]
        parity: list[LimitParityRecord] = []
        source_names: list[str] = []
        for limit in candidates:
            source_name = source_identity(limit.source, unit=limit.unit)
            source_names.append(source_name)
            parity_status, reason = _status_for_limit(limit)
            frame_reason = _joint_space_frame_reason(limit)
            if frame_reason is not None and parity_status is ParityStatus.MATCH:
                parity_status = ParityStatus.MISMATCH
                reason = frame_reason
            parity_lower = limit.lower
            parity_upper = limit.upper
            if parity_status in {
                ParityStatus.UNKNOWN,
                ParityStatus.UNAVAILABLE,
                ParityStatus.INVALID,
            }:
                parity_lower = None
                parity_upper = None
            parity.append(
                LimitParityRecord(
                    joint_name=joint_name,
                    source_name=source_name,
                    status=parity_status,
                    lower=parity_lower,
                    upper=parity_upper,
                    unit=limit.unit,
                    reason=reason,
                    source=limit.source,
                    conversion=limit.conversion,
                    frame=limit.frame,
                )
            )
        effective_statuses = tuple(effective_limit_status(limit) for limit in candidates)
        if EvidenceStatus.INVALID in effective_statuses:
            bounds.append(ResolvedJointBound(joint_name, None, None, LimitResolutionStatus.INVALID, tuple(source_names), tuple(parity), "invalid limit/source status", comparison_tolerance_rad=tolerance_rad))
            continue
        if EvidenceStatus.CONFLICT in effective_statuses:
            bounds.append(ResolvedJointBound(joint_name, None, None, LimitResolutionStatus.MISMATCH, tuple(source_names), tuple(parity), "conflicting limit/source status", comparison_tolerance_rad=tolerance_rad))
            continue
        if EvidenceStatus.UNAVAILABLE in effective_statuses:
            bounds.append(ResolvedJointBound(joint_name, None, None, LimitResolutionStatus.UNAVAILABLE, tuple(source_names), tuple(parity), "limit source unavailable", comparison_tolerance_rad=tolerance_rad))
            continue
        if EvidenceStatus.UNKNOWN in effective_statuses:
            bounds.append(ResolvedJointBound(joint_name, None, None, LimitResolutionStatus.UNKNOWN, tuple(source_names), tuple(parity), "limit/source status unknown", comparison_tolerance_rad=tolerance_rad))
            continue
        frame_reasons = tuple(
            reason
            for reason in (_joint_space_frame_reason(limit) for limit in candidates)
            if reason is not None
        )
        if frame_reasons:
            bounds.append(
                ResolvedJointBound(
                    joint_name,
                    None,
                    None,
                    LimitResolutionStatus.MISMATCH,
                    tuple(source_names),
                    tuple(parity),
                    frame_reasons[0],
                    comparison_tolerance_rad=tolerance_rad,
                )
            )
            continue
        known = [limit for limit in candidates if limit.lower is not None and limit.upper is not None]
        if len(known) != len(candidates):
            bounds.append(ResolvedJointBound(joint_name, None, None, LimitResolutionStatus.UNKNOWN, tuple(source_names), tuple(parity), "limit source is not bounded", comparison_tolerance_rad=tolerance_rad))
            continue
        first = known[0]
        assert first.lower is not None and first.upper is not None
        if any(limit.unit != first.unit for limit in known[1:]):
            bounds.append(ResolvedJointBound(joint_name, None, None, LimitResolutionStatus.MISMATCH, tuple(source_names), tuple(parity), "limit units disagree", comparison_tolerance_rad=tolerance_rad))
            continue
        if first.unit != "rad":
            bounds.append(
                ResolvedJointBound(
                    joint_name,
                    None,
                    None,
                    LimitResolutionStatus.UNKNOWN,
                    tuple(source_names),
                    tuple(parity),
                    "non-rad joint limit unit cannot be normalized; implicit unit conversion is disabled",
                    tolerance_rad,
                )
            )
            continue
        if any(abs(limit.lower - first.lower) > tolerance_rad or abs(limit.upper - first.upper) > tolerance_rad for limit in known[1:]):
            bounds.append(ResolvedJointBound(joint_name, None, None, LimitResolutionStatus.MISMATCH, tuple(source_names), tuple(parity), "limit ranges disagree", comparison_tolerance_rad=tolerance_rad))
            continue
        authoritative = any(
            status is EvidenceStatus.AUTHORITATIVE
            for status in effective_statuses
        )
        status = LimitResolutionStatus.RESOLVED_AUTHORITATIVE if authoritative else LimitResolutionStatus.RESOLVED_PROVISIONAL
        bounds.append(ResolvedJointBound(joint_name, first.lower, first.upper, status, tuple(source_names), tuple(parity), comparison_tolerance_rad=tolerance_rad))
    return LimitResolutionResult(
        schema_version=1,
        robot_id=robot_id,
        bounds=tuple(bounds),
        conversion_relations=tuple(conversion_relations),
        expected_joint_names=names,
        comparison_tolerance_rad=tolerance_rad,
    )


def fast_arm_mujoco_limits_to_physical_limits(
    model: object | None,
    *,
    joint_names: Sequence[str],
) -> tuple[PhysicalLimit, ...]:
    """MuJoCo rangeをsoftware projectionとして収集する。

    modelがない、jointがunlimited、またはidentityが解決できない場合はunknownを返し、
    qpos TOMLやzeroを代入しない。
    """

    result: list[PhysicalLimit] = []
    names = tuple(
        validate_limit_resolution_identity("joint_name", name)
        for name in joint_names
    )
    source = LimitSourceProvenance(
        source_kind="mujoco_jnt_range",
        source_id="loaded-model",
        revision="model-instance",
        status=EvidenceStatus.PROVISIONAL,
        evidence_reference="software-model-only",
    )
    if model is None:
        return tuple(
            make_unknown_limit(
                name=name,
                quantity=LimitQuantity.POSITION,
                space=LimitSpace.JOINT,
                unit="rad",
                frame=canonical_fast_arm_joint_space_frame(),
                reason="MuJoCo model is unavailable",
                source_kind="mujoco_jnt_range",
                source_id="unavailable-model",
                revision="unknown",
            )
            for name in names
        )
    try:
        import mujoco

        for name in names:
            joint_id = _exact_integer(
                f"MuJoCo joint id for {name}",
                mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name),
            )
            if joint_id < 0:
                if joint_id != -1:
                    raise ValueError(
                        f"MuJoCo joint id for {name} must be -1 or non-negative"
                    )
                result.append(make_unknown_limit(name=name, quantity=LimitQuantity.POSITION, space=LimitSpace.JOINT, unit="rad", frame=canonical_fast_arm_joint_space_frame(), reason="joint is missing from MuJoCo model", source_kind="mujoco_jnt_range", source_id="model", revision="unknown"))
                continue
            limited = _exact_binary(
                f"MuJoCo jnt_limited for {name}",
                model.jnt_limited[joint_id],
            )
            if limited == 0:
                result.append(make_unknown_limit(name=name, quantity=LimitQuantity.POSITION, space=LimitSpace.JOINT, unit="rad", frame=canonical_fast_arm_joint_space_frame(), reason="MuJoCo joint has no limited range", source_kind="mujoco_jnt_range", source_id="model", revision="model-instance"))
                continue
            lower, upper = _range(
                tuple(model.jnt_range[joint_id]),
                f"MuJoCo range for {name}",
            )
            result.append(PhysicalLimit(name=name, quantity=LimitQuantity.POSITION, lower=lower, upper=upper, unit="rad", space=LimitSpace.JOINT, frame=canonical_fast_arm_joint_space_frame(), status=EvidenceStatus.PROVISIONAL, source=source))
    except Exception as exc:
        return tuple(
            make_unknown_limit(name=name, quantity=LimitQuantity.POSITION, space=LimitSpace.JOINT, unit="rad", frame=canonical_fast_arm_joint_space_frame(), reason=f"MuJoCo range inspection failed: {exc}", source_kind="mujoco_jnt_range", source_id="invalid-model", revision="unknown")
            for name in names
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class FastArmResolvedBoundsProvider:
    """resolved resultを再計算・書換えせず提供するread-only adapter。"""

    result: LimitResolutionResult

    def __post_init__(self) -> None:
        if type(self.result) is not LimitResolutionResult:
            raise TypeError("result must be LimitResolutionResult")
        validate_limit_resolution_result(self.result)

    def resolve(self) -> LimitResolutionResult:
        validate_limit_resolution_result(self.result)
        return self.result

    def bound_for(self, joint_name: str) -> ResolvedJointBound:
        validate_limit_resolution_result(self.result)
        validate_limit_resolution_identity("joint_name", joint_name)
        return self.result.bound_for(joint_name)


__all__ = [
    "DEFAULT_COMPARISON_TOLERANCE_RAD",
    "FastArmResolvedBoundsProvider",
    "JointSpaceConversion",
    "LimitParityRecord",
    "LimitResolutionResult",
    "LimitResolutionStatus",
    "ParityStatus",
    "ResolvedJointBound",
    "authoritative_position_bound_violations",
    "fast_arm_mujoco_limits_to_physical_limits",
    "project_limit_to_joint_space",
    "resolve_joint_space_bounds",
    "validate_limit_resolution_identity",
    "validate_limit_parity_record",
    "validate_limit_resolution_result",
    "validate_resolved_joint_bound",
]
