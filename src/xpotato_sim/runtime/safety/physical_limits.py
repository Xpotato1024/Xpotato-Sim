"""Physical-limit evidence and safety-envelope contract.

このmoduleは物理的な安全値を取得しない。資料、測定、software設定など、callerが
提示したsourceとprovenanceをtyped valueへ固定し、後続のresolution / collision /
trajectory gateがunknownを安全値として扱わないためのpureな契約を提供する。
"""

from __future__ import annotations

import json
import math
import weakref
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Final


PHYSICAL_SAFETY_ENVELOPE_SCHEMA_VERSION = 1


# dataclass fieldはobject.__setattr__で書き換えられるため、ownerはDTO外部にsemantic
# sealを保持する。mutable/hashableな値自体ではなくidentityをkeyとし、sealは正規化scalarだけを
# 保持し、weak reference先のDTO回収時に削除する。
_PHYSICAL_SEALS: dict[
    int, tuple[weakref.ReferenceType[object], tuple[object, ...]]
] = {}
_PHYSICAL_SEALS_LOCK = RLock()
_PHYSICAL_CONVERSION_ORIGINS: dict[
    int, tuple[weakref.ReferenceType[object], str]
] = {}
_PHYSICAL_LIMIT_CONSTRUCTION_CONTEXT: ContextVar[object | None] = ContextVar(
    "_PHYSICAL_LIMIT_CONSTRUCTION_CONTEXT",
    default=None,
)
_PHYSICAL_LIMIT_CONSTRUCTION_TOKEN = object()
_PHYSICAL_LIMIT_ORIGINS: dict[
    int, tuple[weakref.ReferenceType[object], str]
] = {}


def _release_physical_seal(
    key: int,
    reference: weakref.ReferenceType[object],
) -> None:
    with _PHYSICAL_SEALS_LOCK:
        entry = _PHYSICAL_SEALS.get(key)
        if entry is not None and entry[0] is reference:
            _PHYSICAL_SEALS.pop(key, None)


def _register_physical_seal(
    value: object,
    snapshot: tuple[object, ...],
) -> None:
    key = id(value)
    reference = weakref.ref(
        value,
        lambda ref, key=key: _release_physical_seal(key, ref),
    )
    with _PHYSICAL_SEALS_LOCK:
        _PHYSICAL_SEALS[key] = (reference, snapshot)


def _sealed_physical_snapshot(value: object) -> tuple[object, ...]:
    key = id(value)
    with _PHYSICAL_SEALS_LOCK:
        entry = _PHYSICAL_SEALS.get(key)
        if entry is None or entry[0]() is not value:
            raise ValueError("physical DTO is not constructor-sealed")
        return entry[1]


def _release_conversion_origin(
    key: int,
    reference: weakref.ReferenceType[object],
) -> None:
    with _PHYSICAL_SEALS_LOCK:
        entry = _PHYSICAL_CONVERSION_ORIGINS.get(key)
        if entry is not None and entry[0] is reference:
            _PHYSICAL_CONVERSION_ORIGINS.pop(key, None)


def _register_conversion_origin(value: object, origin: str) -> None:
    key = id(value)
    reference = weakref.ref(
        value,
        lambda ref, key=key: _release_conversion_origin(key, ref),
    )
    with _PHYSICAL_SEALS_LOCK:
        _PHYSICAL_CONVERSION_ORIGINS[key] = (reference, origin)


def _conversion_origin(value: object) -> str:
    key = id(value)
    with _PHYSICAL_SEALS_LOCK:
        entry = _PHYSICAL_CONVERSION_ORIGINS.get(key)
        if entry is None or entry[0]() is not value:
            raise ValueError("conversion provenance has no canonical origin")
        return entry[1]


def _release_limit_origin(
    key: int,
    reference: weakref.ReferenceType[object],
) -> None:
    with _PHYSICAL_SEALS_LOCK:
        entry = _PHYSICAL_LIMIT_ORIGINS.get(key)
        if entry is not None and entry[0] is reference:
            _PHYSICAL_LIMIT_ORIGINS.pop(key, None)


def _register_limit_origin(value: object, origin: str) -> None:
    key = id(value)
    reference = weakref.ref(
        value,
        lambda ref, key=key: _release_limit_origin(key, ref),
    )
    with _PHYSICAL_SEALS_LOCK:
        _PHYSICAL_LIMIT_ORIGINS[key] = (reference, origin)


def _limit_origin(value: object) -> str:
    key = id(value)
    with _PHYSICAL_SEALS_LOCK:
        entry = _PHYSICAL_LIMIT_ORIGINS.get(key)
        if entry is None or entry[0]() is not value:
            raise ValueError("physical limit has no canonical construction origin")
        return entry[1]


@contextmanager
def _canonical_projection_scope():
    token = _PHYSICAL_LIMIT_CONSTRUCTION_CONTEXT.set(
        _PHYSICAL_LIMIT_CONSTRUCTION_TOKEN
    )
    try:
        yield
    finally:
        _PHYSICAL_LIMIT_CONSTRUCTION_CONTEXT.reset(token)


class EvidenceStatus(str, Enum):
    """Sourceまたはlimit値の証拠状態。allowへの暗黙fallbackを持たない。"""

    AUTHORITATIVE = "authoritative"
    PROVISIONAL = "provisional"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    CONFLICT = "conflict"
    INVALID = "invalid"


# Public spelling used by callers that prefer the more explicit name.
LimitEvidenceStatus = EvidenceStatus


class LimitSpace(str, Enum):
    """limitの物理的な表現空間。"""

    JOINT = "joint"
    MOTOR = "motor"
    ACTUATOR = "actuator"


class LimitQuantity(str, Enum):
    """後続gateが参照するbounded quantity。"""

    POSITION = "position"
    VELOCITY = "velocity"
    ACCELERATION = "acceleration"


FAST_ARM_JOINT_SPACE_FRAME: Final[str] = "fast_arm joint space"


def canonical_fast_arm_joint_space_frame() -> str:
    """fast_armのjoint-space limitへ要求する唯一のframe identityを返す。"""

    return FAST_ARM_JOINT_SPACE_FRAME


_SOFTWARE_ONLY_SOURCE_KINDS = frozenset(
    {
        "controller_setting",
        "joint_limit_toml",
        "mujoco_jnt_range",
        "robot_profile",
        "simulation",
        "software_config",
    }
)

_SYNTHETIC_SOURCE_KINDS = frozenset(
    {
        "example",
        "fake",
        "fixture",
        "placeholder",
        "sample",
        "simulation_snapshot",
        "synthetic",
        "test_fixture",
        "fixture_data",
        "test",
    }
)

# physical authorityはallowlistで管理する。新しいsource kindはここで明示的に
# reviewされるまでauthorityにならず、unknown / software / fixture kindのIDだけでは昇格しない。
_AUTHORITATIVE_SOURCE_KINDS = frozenset(
    {
        "lab_document",
        "manufacturer_document",
        "physical_measurement",
    }
)

_PLACEHOLDER_IDENTITIES = frozenset(
    {
        "n-a",
        "n/a",
        "na",
        "n_a",
        "nil",
        "none",
        "not-applicable",
        "not_available",
        "null",
        "placeholder",
        "sample",
        "simulation_snapshot",
        "synthetic",
        "test",
        "test_fixture",
        "fixture",
        "fixture_data",
        "unknown",
        "unavailable",
    }
)


def _text(name: str, value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _source_kind(value: object) -> str:
    kind = _text("source_kind", value)
    if not kind.isascii() or not kind[0].islower() or any(
        not (character.islower() or character.isdigit() or character == "_")
        for character in kind
    ):
        raise ValueError("source_kind must use canonical lowercase underscore notation")
    return kind


def _is_placeholder_identity(value: str) -> bool:
    return value.casefold() in _PLACEHOLDER_IDENTITIES


def validate_concrete_limit_identity(name: str, value: object) -> str:
    """具体的なlimit relationをbindするidentityを検証する。"""

    if type(value) is not str:
        raise ValueError(f"{name} must be a built-in string")
    result = _text(name, value)
    if _is_placeholder_identity(result):
        raise ValueError(f"{name} must be a concrete identity")
    return result


def _finite_or_none(name: str, value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number or None")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return 0.0 if number == 0.0 else number


def _stored_finite_or_none(name: str, value: object) -> float | None:
    """構築後の数値fieldをhookなしで検証する。"""

    if value is None:
        return None
    if type(value) is not float:
        raise TypeError(f"{name} must be a canonical float or None")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return 0.0 if value == 0.0 else value


def _enum_value(
    enum_type: type[Enum],
    name: str,
    value: object,
    *,
    normalize_string: bool = True,
) -> Enum:
    """正規メンバーidentityを保ったままenum入力を解決する。

    constructorとdecoderではexactなbuilt-in stringをenum memberへ正規化できる。
    既にenum型の値はclassが保持するsingleton memberだけを受け付ける。
    ``isinstance``だけでは``object.__new__``とfield mutationで作った偽造memberを
    受け入れるため、identityを検査する。stored DTO fieldの再検証では
    ``_stored_enum_value``を使い、raw stringを信頼済みmemberへ再正規化しない。
    """

    if type(value) is enum_type:
        if any(member is value for member in enum_type.__members__.values()):
            return value
        raise TypeError(
            f"{name} must be a canonical {enum_type.__name__} member"
        )
    if not normalize_string:
        raise TypeError(f"{name} must be a canonical {enum_type.__name__} member")
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ValueError(f"{name} must be one of: {allowed}") from exc


def _stored_enum_value(enum_type: type[Enum], name: str, value: object) -> Enum:
    """raw stringを受け付けず、stored enum fieldのsingleton identityを検証する。"""

    return _enum_value(enum_type, name, value, normalize_string=False)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class LimitSourceProvenance:
    """1つのlimit sourceを追跡するためのimmutable metadata。

    ``evidence_reference``は資料ID、測定記録ID、または検証済みartifactのlogical
    referenceであり、値そのものを意味しない。repository内のTOMLやMJCFはsourceと
    して記録できるが、physical authorityへ自動昇格しない。
    """

    source_kind: str
    source_id: str
    revision: str
    status: EvidenceStatus
    evidence_reference: str | None = None
    observed_at: str | None = None
    notes: str | None = None
    _canonical_snapshot: tuple[object, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        source_kind = _source_kind(self.source_kind)
        source_id = _text("source_id", self.source_id)
        revision = _text("revision", self.revision)
        status = _enum_value(EvidenceStatus, "status", self.status)
        if status is EvidenceStatus.AUTHORITATIVE and source_kind not in _AUTHORITATIVE_SOURCE_KINDS:
            message = (
                "synthetic limit source cannot be authoritative"
                if source_kind in _SYNTHETIC_SOURCE_KINDS
                else "software-only limit source cannot be authoritative"
                if source_kind in _SOFTWARE_ONLY_SOURCE_KINDS
                else "source kind is not approved for physical authority"
            )
            raise ValueError(
                f"{message}: {source_kind}"
            )
        for name, value in (
            ("evidence_reference", self.evidence_reference),
            ("observed_at", self.observed_at),
            ("notes", self.notes),
        ):
            if value is not None:
                _text(name, value)
        if status is EvidenceStatus.AUTHORITATIVE and not self.evidence_reference:
            raise ValueError("authoritative source requires evidence_reference")
        if status is EvidenceStatus.AUTHORITATIVE and (
            _is_placeholder_identity(source_id)
            or _is_placeholder_identity(revision)
            or _is_placeholder_identity(self.evidence_reference)
        ):
            raise ValueError("authoritative source requires concrete identities")
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "status", status)
        snapshot = _source_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_physical_seal(self, snapshot)

    @property
    def is_physical_evidence(self) -> bool:
        """authoritative statusを持つ、明示的なphysical evidenceかを返す。"""

        try:
            _validate_limit_source(self)
        except (AttributeError, TypeError, ValueError):
            return False
        return self.status is EvidenceStatus.AUTHORITATIVE

    def to_dict(self) -> dict[str, object]:
        _validate_limit_source(self)
        result: dict[str, object] = {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "revision": self.revision,
            "status": self.status.value,
        }
        for name in ("evidence_reference", "observed_at", "notes"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


@dataclass(frozen=True, slots=True, weakref_slot=True)
class LimitConversionProvenance:
    """limit valueがどのspaceから来たかを明示する変換履歴。

    P1では変換の数値を推測しない。ratio / sign / offsetは、実証済みなら入力し、
    不明なら``None``のまま保持する。P2がidentityまたは明示変換として検証する。
    """

    source_space: LimitSpace
    target_space: LimitSpace
    method: str
    relation_id: str
    gear_ratio: float | None = None
    sign: float | None = None
    offset: float | None = None
    source_name: str | None = None
    _canonical_snapshot: tuple[object, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        source_space = _enum_value(LimitSpace, "source_space", self.source_space)
        target_space = _enum_value(LimitSpace, "target_space", self.target_space)
        _text("method", self.method)
        _text("relation_id", self.relation_id)
        if self.source_name is not None:
            validate_concrete_limit_identity("source_name", self.source_name)
        ratio = _finite_or_none("gear_ratio", self.gear_ratio)
        sign = _finite_or_none("sign", self.sign)
        offset = _finite_or_none("offset", self.offset)
        if ratio is not None and ratio == 0.0:
            raise ValueError("gear_ratio must be non-zero when provided")
        if sign is not None and sign not in (-1.0, 1.0):
            raise ValueError("sign must be either -1 or 1 when provided")
        object.__setattr__(self, "source_space", source_space)
        object.__setattr__(self, "target_space", target_space)
        object.__setattr__(self, "gear_ratio", ratio)
        object.__setattr__(self, "sign", sign)
        object.__setattr__(self, "offset", offset)
        snapshot = _conversion_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_physical_seal(self, snapshot)

    @classmethod
    def identity(cls, space: LimitSpace) -> "LimitConversionProvenance":
        """同一spaceの値にもidentity provenanceを要求する。"""

        resolved = _enum_value(LimitSpace, "space", space)
        conversion = cls(
            resolved,
            resolved,
            "identity",
            f"identity:{resolved.value}",
            1.0,
            1.0,
            0.0,
        )
        _register_conversion_origin(conversion, "identity")
        return conversion

    @classmethod
    def projected(
        cls,
        *,
        source_space: LimitSpace,
        relation_id: str,
        gear_ratio: float,
        sign: float,
        offset: float,
        source_name: str,
    ) -> "LimitConversionProvenance":
        """JointSpaceConversionが生成するcanonical projection provenance。"""

        resolved = _enum_value(LimitSpace, "source_space", source_space)
        conversion = cls(
            resolved,
            LimitSpace.JOINT,
            "joint = sign * source / gear_ratio + offset",
            relation_id,
            gear_ratio,
            sign,
            offset,
            source_name,
        )
        _register_conversion_origin(conversion, "projected")
        return conversion

    def to_dict(self) -> dict[str, object]:
        validate_limit_conversion(self)
        result: dict[str, object] = {
            "source_space": self.source_space.value,
            "target_space": self.target_space.value,
            "method": self.method,
            "relation_id": self.relation_id,
            "gear_ratio": self.gear_ratio,
            "sign": self.sign,
            "offset": self.offset,
        }
        if self.source_name is not None:
            result["source_name"] = self.source_name
        return result


@dataclass(frozen=True, slots=True, weakref_slot=True)
class PhysicalLimit:
    """provenance付きのposition / velocity / acceleration interval。"""

    name: str
    quantity: LimitQuantity
    lower: float | None
    upper: float | None
    unit: str
    space: LimitSpace
    frame: str
    status: EvidenceStatus
    source: LimitSourceProvenance
    conversion: LimitConversionProvenance | None = None
    reason: str | None = None
    _canonical_snapshot: tuple[object, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        name = validate_concrete_limit_identity("name", self.name)
        quantity = _enum_value(LimitQuantity, "quantity", self.quantity)
        space = _enum_value(LimitSpace, "space", self.space)
        canonical_projection = (
            _PHYSICAL_LIMIT_CONSTRUCTION_CONTEXT.get()
            is _PHYSICAL_LIMIT_CONSTRUCTION_TOKEN
        )
        lower = _finite_or_none("lower", self.lower)
        upper = _finite_or_none("upper", self.upper)
        _text("unit", self.unit)
        _text("frame", self.frame)
        status = _enum_value(EvidenceStatus, "status", self.status)
        if type(self.source) is not LimitSourceProvenance:
            raise TypeError("source must be LimitSourceProvenance")
        if self.conversion is None:
            conversion = LimitConversionProvenance.identity(space)
        elif type(self.conversion) is not LimitConversionProvenance:
            raise TypeError("conversion must be LimitConversionProvenance or None")
        else:
            conversion = self.conversion
        _validate_limit_source(self.source)
        _validate_conversion_provenance(conversion)
        _validate_conversion_origin(conversion, source_space=space)
        if conversion.target_space is not space:
            raise ValueError("conversion target_space must match limit space")
        if (
            space is LimitSpace.JOINT
            and conversion.source_space is not LimitSpace.JOINT
            and not canonical_projection
        ):
            raise ValueError(
                "joint-space projected limit requires canonical projection origin"
            )
        if canonical_projection and (
            space is not LimitSpace.JOINT
            or conversion.source_space is LimitSpace.JOINT
        ):
            raise ValueError(
                "canonical projection must target joint space from motor or actuator"
            )
        if status is EvidenceStatus.AUTHORITATIVE and self.source.status is not EvidenceStatus.AUTHORITATIVE:
            raise ValueError("authoritative limit requires authoritative source")
        if status is EvidenceStatus.AUTHORITATIVE and (lower is None or upper is None):
            raise ValueError("authoritative limit requires finite lower and upper")
        if status in {EvidenceStatus.PROVISIONAL, EvidenceStatus.AUTHORITATIVE}:
            if lower is None or upper is None:
                raise ValueError(f"{status.value} limit requires finite lower and upper")
            if lower > upper:
                raise ValueError("limit lower must not exceed upper")
        if status in {
            EvidenceStatus.UNKNOWN,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.CONFLICT,
            EvidenceStatus.INVALID,
        }:
            if lower is not None or upper is not None:
                raise ValueError(f"{status.value} limit must not contain bounds")
            if not self.reason:
                raise ValueError(f"{status.value} limit requires reason")
        if self.reason is not None:
            _text("reason", self.reason)
        if space is not LimitSpace.JOINT and conversion.source_space is not space:
            raise ValueError("non-joint limit conversion source_space must match limit space")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "space", space)
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "conversion", conversion)
        snapshot = _limit_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_physical_seal(self, snapshot)
        _register_limit_origin(
            self,
            "projected" if canonical_projection else "direct",
        )

    @property
    def is_bounded(self) -> bool:
        try:
            _validate_physical_limit(self)
        except (AttributeError, TypeError, ValueError):
            return False
        return self.lower is not None and self.upper is not None

    @property
    def is_authoritative(self) -> bool:
        try:
            _validate_physical_limit(self)
        except (AttributeError, TypeError, ValueError):
            return False
        return self.status is EvidenceStatus.AUTHORITATIVE

    def to_dict(self) -> dict[str, object]:
        _validate_physical_limit(self)
        result: dict[str, object] = {
            "name": self.name,
            "quantity": self.quantity.value,
            "lower": self.lower,
            "upper": self.upper,
            "unit": self.unit,
            "space": self.space.value,
            "frame": self.frame,
            "status": self.status.value,
            "source": self.source.to_dict(),
            "conversion": self.conversion.to_dict(),
        }
        if self.reason is not None:
            result["reason"] = self.reason
        return result


def _construct_projected_limit(
    *,
    name: str,
    quantity: LimitQuantity,
    lower: float | None,
    upper: float | None,
    unit: str,
    frame: str,
    status: EvidenceStatus,
    source: LimitSourceProvenance,
    conversion: LimitConversionProvenance,
    reason: str | None = None,
) -> PhysicalLimit:
    """canonical projection / decoderだけがnon-identity limitを生成する。"""

    with _canonical_projection_scope():
        return PhysicalLimit(
            name=name,
            quantity=quantity,
            lower=lower,
            upper=upper,
            unit=unit,
            space=LimitSpace.JOINT,
            frame=frame,
            status=status,
            source=source,
            conversion=conversion,
            reason=reason,
        )


def _source_snapshot(source: LimitSourceProvenance) -> tuple[object, ...]:
    """constructorで正規化したsource内容を再検証用に保持する。"""

    return (
        source.source_kind,
        source.source_id,
        source.revision,
        source.status,
        source.evidence_reference,
        source.observed_at,
        source.notes,
    )


def _conversion_snapshot(
    conversion: LimitConversionProvenance,
) -> tuple[object, ...]:
    return (
        conversion.source_space,
        conversion.target_space,
        conversion.method,
        conversion.relation_id,
        conversion.gear_ratio,
        conversion.sign,
        conversion.offset,
        conversion.source_name,
    )


def _limit_snapshot(limit: PhysicalLimit) -> tuple[object, ...]:
    return (
        limit.name,
        limit.quantity,
        limit.lower,
        limit.upper,
        limit.unit,
        limit.space,
        limit.frame,
        limit.status,
        (id(limit.source), _source_snapshot(limit.source)),
        (id(limit.conversion), _conversion_snapshot(limit.conversion)),
        limit.reason,
    )


def _validate_limit_source(source: object) -> LimitSourceProvenance:
    """bypass経路も検査するsource provenanceのcanonical deep validator。"""

    if type(source) is not LimitSourceProvenance:
        raise TypeError("source must be LimitSourceProvenance")
    source_kind = _source_kind(source.source_kind)
    source_id = _text("source_id", source.source_id)
    revision = _text("revision", source.revision)
    status = _stored_enum_value(EvidenceStatus, "status", source.status)
    for name, value in (
        ("evidence_reference", source.evidence_reference),
        ("observed_at", source.observed_at),
        ("notes", source.notes),
    ):
        if value is not None:
            _text(name, value)
    if status is EvidenceStatus.AUTHORITATIVE:
        if source_kind not in _AUTHORITATIVE_SOURCE_KINDS:
            raise ValueError("source kind is not approved for physical authority")
        if not source.evidence_reference:
            raise ValueError("authoritative source requires evidence_reference")
        if (
            _is_placeholder_identity(source_id)
            or _is_placeholder_identity(revision)
            or _is_placeholder_identity(source.evidence_reference)
        ):
            raise ValueError("authoritative source requires concrete identities")
    expected = (
        source_kind,
        source_id,
        revision,
        status,
        source.evidence_reference,
        source.observed_at,
        source.notes,
    )
    if _sealed_physical_snapshot(source) != expected:
        raise ValueError("source provenance has been mutated or bypassed")
    return source


def _validate_conversion_provenance(
    conversion: object,
) -> LimitConversionProvenance:
    """conversion metadataのcanonical deep validator。"""

    if type(conversion) is not LimitConversionProvenance:
        raise TypeError("conversion must be LimitConversionProvenance")
    source_space = _stored_enum_value(LimitSpace, "source_space", conversion.source_space)
    target_space = _stored_enum_value(LimitSpace, "target_space", conversion.target_space)
    method = _text("method", conversion.method)
    relation_id = _text("relation_id", conversion.relation_id)
    source_name = (
        None
        if conversion.source_name is None
        else validate_concrete_limit_identity("source_name", conversion.source_name)
    )
    ratio = _stored_finite_or_none("gear_ratio", conversion.gear_ratio)
    sign = _stored_finite_or_none("sign", conversion.sign)
    offset = _stored_finite_or_none("offset", conversion.offset)
    if ratio is not None and ratio == 0.0:
        raise ValueError("gear_ratio must be non-zero when provided")
    if sign is not None and sign not in (-1.0, 1.0):
        raise ValueError("sign must be either -1 or 1 when provided")
    if source_space is target_space:
        identity_relation = f"identity:{source_space.value}"
        if (
            method != "identity"
            or relation_id != identity_relation
            or ratio != 1.0
            or sign != 1.0
            or offset != 0.0
            or source_name is not None
        ):
            raise ValueError("same-space conversion must be canonical identity")
    else:
        if source_space is LimitSpace.JOINT or target_space is not LimitSpace.JOINT:
            raise ValueError("conversion must target joint space from motor or actuator")
        if method != "joint = sign * source / gear_ratio + offset":
            raise ValueError("non-identity conversion method is not canonical")
        if ratio is None or sign is None or offset is None:
            raise ValueError("non-identity conversion requires concrete parameters")
        validate_concrete_limit_identity("relation_id", relation_id)
    expected = (
        source_space,
        target_space,
        method,
        relation_id,
        ratio,
        sign,
        offset,
        source_name,
    )
    if _sealed_physical_snapshot(conversion) != expected:
        raise ValueError("conversion provenance has been mutated or bypassed")
    return conversion


def _validate_conversion_origin(
    conversion: LimitConversionProvenance,
    *,
    source_space: LimitSpace,
) -> None:
    """PhysicalLimitへ接続するconversionの生成経路をcanonicalに限定する。"""

    origin = _conversion_origin(conversion)
    if conversion.target_space is not source_space:
        raise ValueError("conversion target_space must match limit space")
    if conversion.source_space is conversion.target_space:
        if origin != "identity":
            raise ValueError("same-space conversion must use canonical identity origin")
    elif origin != "projected":
        raise ValueError("cross-space conversion must use canonical projection origin")
    elif conversion.source_name is None:
        raise ValueError("non-identity conversion requires concrete source_name")


def _validate_physical_limit(limit: object) -> PhysicalLimit:
    """limitとnested provenanceを検査するcanonical deep validator。"""

    if type(limit) is not PhysicalLimit:
        raise TypeError("limit must be PhysicalLimit")
    name = validate_concrete_limit_identity("name", limit.name)
    quantity = _stored_enum_value(LimitQuantity, "quantity", limit.quantity)
    space = _stored_enum_value(LimitSpace, "space", limit.space)
    lower = _stored_finite_or_none("lower", limit.lower)
    upper = _stored_finite_or_none("upper", limit.upper)
    _text("unit", limit.unit)
    _text("frame", limit.frame)
    status = _stored_enum_value(EvidenceStatus, "status", limit.status)
    source = _validate_limit_source(limit.source)
    conversion = _validate_conversion_provenance(limit.conversion)
    _validate_conversion_origin(conversion, source_space=space)
    if conversion.target_space is not space:
        raise ValueError("conversion target_space must match limit space")
    origin = _limit_origin(limit)
    if origin not in {"direct", "projected"}:
        raise ValueError("physical limit has an unknown construction origin")
    if space is LimitSpace.JOINT:
        if conversion.source_space is LimitSpace.JOINT and origin != "direct":
            raise ValueError("joint-space identity limit has an invalid origin")
        if conversion.source_space is not LimitSpace.JOINT and origin != "projected":
            raise ValueError(
                "joint-space projected limit requires canonical projection origin"
            )
    elif origin != "direct":
        raise ValueError("non-joint limit has an invalid construction origin")
    if space is not LimitSpace.JOINT and conversion.source_space is not space:
        raise ValueError("non-joint limit conversion source_space must match limit space")
    if status is EvidenceStatus.AUTHORITATIVE:
        if source.status is not EvidenceStatus.AUTHORITATIVE:
            raise ValueError("authoritative limit requires authoritative source")
        if lower is None or upper is None:
            raise ValueError("authoritative limit requires finite lower and upper")
    if status in {EvidenceStatus.PROVISIONAL, EvidenceStatus.AUTHORITATIVE}:
        if lower is None or upper is None:
            raise ValueError(f"{status.value} limit requires finite lower and upper")
        if lower > upper:
            raise ValueError("limit lower must not exceed upper")
    if status in {
        EvidenceStatus.UNKNOWN,
        EvidenceStatus.UNAVAILABLE,
        EvidenceStatus.CONFLICT,
        EvidenceStatus.INVALID,
    }:
        if lower is not None or upper is not None:
            raise ValueError(f"{status.value} limit must not contain bounds")
        if not limit.reason:
            raise ValueError(f"{status.value} limit requires reason")
    if limit.reason is not None:
        _text("reason", limit.reason)
    expected = (
        name,
        quantity,
        lower,
        upper,
        limit.unit,
        space,
        limit.frame,
        status,
        (id(source), _source_snapshot(source)),
        (id(conversion), _conversion_snapshot(conversion)),
        limit.reason,
    )
    if _sealed_physical_snapshot(limit) != expected:
        raise ValueError("physical limit has been mutated or bypassed")
    return limit


def validate_limit_source(source: LimitSourceProvenance) -> LimitSourceProvenance:
    """Public canonical revalidation route for nested source provenance."""

    return _validate_limit_source(source)


def source_identity(
    source: LimitSourceProvenance,
    *,
    unit: str,
) -> str:
    """Return the canonical typed source identity used by parity records."""

    validated = _validate_limit_source(source)
    return (
        f"{validated.source_kind}:{validated.source_id}"
        f"@{validated.revision}[unit={_text('unit', unit)}]"
    )


def validate_limit_conversion(
    conversion: LimitConversionProvenance,
) -> LimitConversionProvenance:
    """Public canonical revalidation route for conversion provenance."""

    validated = _validate_conversion_provenance(conversion)
    _validate_conversion_origin(validated, source_space=validated.target_space)
    return validated


def validate_physical_limit(limit: PhysicalLimit) -> PhysicalLimit:
    """Public canonical revalidation route for a physical limit."""

    return _validate_physical_limit(limit)


_EFFECTIVE_STATUS_PRECEDENCE = (
    EvidenceStatus.INVALID,
    EvidenceStatus.CONFLICT,
    EvidenceStatus.UNAVAILABLE,
    EvidenceStatus.UNKNOWN,
)


def effective_limit_status(limit: PhysicalLimit) -> EvidenceStatus:
    """値とsource provenanceを合わせたcanonicalなeffective statusを返す。

    ``PhysicalLimit.status``だけを見てsourceの欠落・衝突をbounded valueへ昇格
    させない。unresolved statusはtypedな優先順位で閉じ、authorityはlimitとsource
    の両方が明示的に``AUTHORITATIVE``の場合だけ成立する。
    """

    if type(limit) is not PhysicalLimit:
        raise TypeError("limit must be PhysicalLimit")
    try:
        _validate_physical_limit(limit)
    except (AttributeError, TypeError, ValueError):
        return EvidenceStatus.INVALID
    statuses = (limit.status, limit.source.status)
    try:
        statuses = tuple(
            _stored_enum_value(EvidenceStatus, "status", status)
            for status in statuses
        )
    except (TypeError, ValueError):
        return EvidenceStatus.INVALID
    for status in _EFFECTIVE_STATUS_PRECEDENCE:
        if status in statuses:
            return status
    if (
        limit.status is EvidenceStatus.AUTHORITATIVE
        and limit.source.status is EvidenceStatus.AUTHORITATIVE
    ):
        return EvidenceStatus.AUTHORITATIVE
    return EvidenceStatus.PROVISIONAL


@dataclass(frozen=True, slots=True, weakref_slot=True)
class PhysicalSafetyEnvelope:
    """後続のphysical-safety gateが参照するversioned envelope。"""

    envelope_id: str
    envelope_version: int
    robot_id: str
    model_id: str
    limits: tuple[PhysicalLimit, ...]
    source_summary: str | None = None
    schema_version: int = PHYSICAL_SAFETY_ENVELOPE_SCHEMA_VERSION
    _canonical_snapshot: tuple[object, ...] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        _validate_physical_safety_envelope(self, initialize=True)

    @property
    def statuses(self) -> frozenset[EvidenceStatus]:
        _validate_physical_safety_envelope(self)
        return frozenset(effective_limit_status(limit) for limit in self.limits)

    @property
    def has_unresolved_evidence(self) -> bool:
        _validate_physical_safety_envelope(self)
        return any(
            effective_limit_status(limit)
            in {
                EvidenceStatus.UNKNOWN,
                EvidenceStatus.UNAVAILABLE,
                EvidenceStatus.CONFLICT,
                EvidenceStatus.INVALID,
            }
            for limit in self.limits
        )

    def limit_for(
        self,
        name: str,
        *,
        quantity: LimitQuantity = LimitQuantity.POSITION,
        space: LimitSpace = LimitSpace.JOINT,
    ) -> PhysicalLimit:
        _validate_physical_safety_envelope(self)
        name = validate_concrete_limit_identity("name", name)
        quantity = _enum_value(LimitQuantity, "quantity", quantity)  # type: ignore[assignment]
        space = _enum_value(LimitSpace, "space", space)  # type: ignore[assignment]
        for limit in self.limits:
            if limit.name == name and limit.quantity is quantity and limit.space is space:
                return limit
        raise KeyError((name, quantity.value, space.value))

    def to_dict(self) -> dict[str, object]:
        _validate_physical_safety_envelope(self)
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "envelope_id": self.envelope_id,
            "envelope_version": self.envelope_version,
            "robot_id": self.robot_id,
            "model_id": self.model_id,
            "limits": [limit.to_dict() for limit in self.limits],
        }
        if self.source_summary is not None:
            result["source_summary"] = self.source_summary
        return result

    def to_json_bytes(self) -> bytes:
        """UTF-8 without BOMの決定的なwire representation。"""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, data: bytes) -> "PhysicalSafetyEnvelope":
        if type(data) is not bytes:
            raise TypeError("physical safety envelope bytes must be bytes")
        if data.startswith(b"\xef\xbb\xbf"):
            raise ValueError("physical safety envelope must not contain a UTF-8 BOM")
        try:
            raw = json.loads(
                data.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid physical safety envelope JSON") from exc
        return _envelope_from_mapping(raw)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """JSON object hook that fails closed on duplicate field names."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _optional_text(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    return _text(name, value)


def _source_from_mapping(value: object) -> LimitSourceProvenance:
    raw = _mapping(value, "source")
    allowed = {"source_kind", "source_id", "revision", "status", "evidence_reference", "observed_at", "notes"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"source contains unknown fields: {sorted(unknown)!r}")
    return LimitSourceProvenance(
        source_kind=_text("source_kind", raw.get("source_kind")),
        source_id=_text("source_id", raw.get("source_id")),
        revision=_text("revision", raw.get("revision")),
        status=_enum_value(EvidenceStatus, "status", raw.get("status")),  # type: ignore[arg-type]
        evidence_reference=_optional_text(raw, "evidence_reference"),
        observed_at=_optional_text(raw, "observed_at"),
        notes=_optional_text(raw, "notes"),
    )


def _conversion_from_mapping(value: object) -> LimitConversionProvenance:
    raw = _mapping(value, "conversion")
    allowed = {
        "source_space",
        "target_space",
        "method",
        "relation_id",
        "gear_ratio",
        "sign",
        "offset",
        "source_name",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"conversion contains unknown fields: {sorted(unknown)!r}")
    source_space = _enum_value(LimitSpace, "source_space", raw.get("source_space"))
    target_space = _enum_value(LimitSpace, "target_space", raw.get("target_space"))
    method = _text("method", raw.get("method"))
    relation_id = _text("relation_id", raw.get("relation_id"))
    source_name = raw.get("source_name")
    # PythonではJSON booleanが``int``のsubclassとして扱われるため、identity判定より前に
    # constructorと共有するstrict numeric validatorで全数値fieldを正規化する。
    # これにより``true``/``false``が1/0と等値になる経路を閉じる。
    ratio = _finite_or_none("gear_ratio", raw.get("gear_ratio"))
    sign = _finite_or_none("sign", raw.get("sign"))
    offset = _finite_or_none("offset", raw.get("offset"))
    if (
        source_space is target_space
        and method == "identity"
        and relation_id == f"identity:{source_space.value}"
        and ratio == 1.0
        and sign == 1.0
        and offset == 0.0
    ):
        if source_name is not None:
            raise ValueError("identity conversion must not declare source_name")
        return LimitConversionProvenance.identity(source_space)
    if (
        target_space is LimitSpace.JOINT
        and source_space in {LimitSpace.MOTOR, LimitSpace.ACTUATOR}
        and method == "joint = sign * source / gear_ratio + offset"
    ):
        # non-identity provenanceはcanonical projection factoryで再構成し、
        # decoded limitにもPhysicalLimitが要求するorigin sealを付与する。
        return LimitConversionProvenance.projected(
            source_space=source_space,
            relation_id=relation_id,
            gear_ratio=ratio,
            sign=sign,
            offset=offset,
            source_name=validate_concrete_limit_identity("source_name", source_name),
        )
    return LimitConversionProvenance(
        source_space=source_space,
        target_space=target_space,
        method=method,
        relation_id=relation_id,
        gear_ratio=ratio,
        sign=sign,
        offset=offset,
        source_name=source_name,  # type: ignore[arg-type]
    )


def _limit_from_mapping(value: object) -> PhysicalLimit:
    raw = _mapping(value, "limit")
    allowed = {"name", "quantity", "lower", "upper", "unit", "space", "frame", "status", "source", "conversion", "reason"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"limit contains unknown fields: {sorted(unknown)!r}")
    name = validate_concrete_limit_identity("name", raw.get("name"))
    quantity = _enum_value(LimitQuantity, "quantity", raw.get("quantity"))
    lower = raw.get("lower")
    upper = raw.get("upper")
    unit = _text("unit", raw.get("unit"))
    space = _enum_value(LimitSpace, "space", raw.get("space"))
    frame = _text("frame", raw.get("frame"))
    status = _enum_value(EvidenceStatus, "status", raw.get("status"))
    source = _source_from_mapping(raw.get("source"))
    conversion = _conversion_from_mapping(raw.get("conversion"))
    reason = _optional_text(raw, "reason")
    if space is LimitSpace.JOINT and conversion.source_space is not LimitSpace.JOINT:
        return _construct_projected_limit(
            name=name,
            quantity=quantity,
            lower=lower,
            upper=upper,
            unit=unit,
            frame=frame,
            status=status,
            source=source,
            conversion=conversion,
            reason=reason,
        )
    return PhysicalLimit(
        name=name,
        quantity=quantity,
        lower=lower,
        upper=upper,
        unit=unit,
        space=space,
        frame=frame,
        status=status,
        source=source,
        conversion=conversion,
        reason=reason,
    )


def _envelope_from_mapping(value: object) -> PhysicalSafetyEnvelope:
    raw = _mapping(value, "physical safety envelope")
    allowed = {"schema_version", "envelope_id", "envelope_version", "robot_id", "model_id", "limits", "source_summary"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"physical safety envelope contains unknown fields: {sorted(unknown)!r}")
    limits_raw = raw.get("limits")
    if not isinstance(limits_raw, Sequence) or isinstance(limits_raw, (str, bytes, bytearray)):
        raise ValueError("limits must be an array")
    return PhysicalSafetyEnvelope(
        envelope_id=_text("envelope_id", raw.get("envelope_id")),
        envelope_version=raw.get("envelope_version"),  # type: ignore[arg-type]
        robot_id=validate_concrete_limit_identity("robot_id", raw.get("robot_id")),
        model_id=_text("model_id", raw.get("model_id")),
        limits=tuple(_limit_from_mapping(item) for item in limits_raw),
        source_summary=_optional_text(raw, "source_summary"),
        schema_version=raw.get("schema_version"),  # type: ignore[arg-type]
    )


def _envelope_snapshot(envelope: PhysicalSafetyEnvelope) -> tuple[object, ...]:
    """Envelope内容とnested limit identityをowner外部sealへ固定する。"""

    return (
        envelope.envelope_id,
        envelope.envelope_version,
        envelope.robot_id,
        envelope.model_id,
        tuple((id(limit), _limit_snapshot(limit)) for limit in envelope.limits),
        envelope.source_summary,
        envelope.schema_version,
    )


def _validate_physical_safety_envelope(
    envelope: object,
    *,
    initialize: bool = False,
) -> PhysicalSafetyEnvelope:
    """Envelope constructor/accessors/decoderで共有するdeep validator。"""

    if type(envelope) is not PhysicalSafetyEnvelope:
        raise TypeError("envelope must be PhysicalSafetyEnvelope")
    _text("envelope_id", envelope.envelope_id)
    if (
        type(envelope.envelope_version) is not int
        or envelope.envelope_version < 1
    ):
        raise ValueError("envelope_version must be a positive integer")
    validate_concrete_limit_identity("robot_id", envelope.robot_id)
    _text("model_id", envelope.model_id)
    if (
        type(envelope.schema_version) is not int
        or envelope.schema_version != PHYSICAL_SAFETY_ENVELOPE_SCHEMA_VERSION
    ):
        raise ValueError(
            f"unsupported physical safety envelope schema version: {envelope.schema_version!r}"
        )
    if type(envelope.limits) is not tuple:
        raise TypeError("limits must be a built-in tuple")
    if not envelope.limits:
        raise ValueError("limits must be non-empty")
    names: set[tuple[str, LimitQuantity, LimitSpace]] = set()
    for limit in envelope.limits:
        if type(limit) is not PhysicalLimit:
            raise TypeError("limits must contain PhysicalLimit values")
        _validate_physical_limit(limit)
        key = (limit.name, limit.quantity, limit.space)
        if key in names:
            raise ValueError(f"duplicate physical limit: {key!r}")
        names.add(key)
    if envelope.source_summary is not None:
        _text("source_summary", envelope.source_summary)
    expected = _envelope_snapshot(envelope)
    if initialize:
        object.__setattr__(envelope, "_canonical_snapshot", expected)
        _register_physical_seal(envelope, expected)
    elif _sealed_physical_snapshot(envelope) != expected:
        raise ValueError("physical safety envelope has been mutated or bypassed")
    return envelope


def validate_envelope(envelope: PhysicalSafetyEnvelope) -> PhysicalSafetyEnvelope:
    """既にtypedなenvelopeを再検証し、同じobjectを返す。"""

    return _validate_physical_safety_envelope(envelope)


def classify_source_status(
    *,
    source_kind: str,
    evidence_reference: str | None,
    authority_asserted: bool,
) -> EvidenceStatus:
    """sourceの性質を分類する補助関数。

    software設定をauthorityへ昇格させず、physical authorityはcallerが明示した
    evidence referenceとassertionの両方がある場合だけ返す。
    """

    kind = _source_kind(source_kind)
    if type(authority_asserted) is not bool:
        raise TypeError("authority_asserted must be bool")
    reference = None if evidence_reference is None else _text("evidence_reference", evidence_reference)
    if authority_asserted and kind in _SYNTHETIC_SOURCE_KINDS:
        raise ValueError("synthetic limit source cannot be authoritative")
    if authority_asserted and (
        reference is None or _is_placeholder_identity(reference)
    ):
        if reference is not None:
            raise ValueError("authoritative source requires concrete identities")
        if kind not in _SOFTWARE_ONLY_SOURCE_KINDS:
            return EvidenceStatus.UNKNOWN
    if kind in _SOFTWARE_ONLY_SOURCE_KINDS:
        return EvidenceStatus.PROVISIONAL
    if authority_asserted and kind not in _AUTHORITATIVE_SOURCE_KINDS:
        raise ValueError("source kind is not approved for physical authority")
    if authority_asserted and reference:
        return EvidenceStatus.AUTHORITATIVE
    if reference:
        return EvidenceStatus.PROVISIONAL
    return EvidenceStatus.UNKNOWN


def make_unknown_limit(
    *,
    name: str,
    quantity: LimitQuantity,
    space: LimitSpace,
    unit: str,
    frame: str,
    reason: str,
    source_kind: str = "unknown",
    source_id: str = "unknown",
    revision: str = "unknown",
) -> PhysicalLimit:
    """値が存在しないsourceを明示的に保持するfixture/helper。"""

    source = LimitSourceProvenance(
        source_kind=source_kind,
        source_id=source_id,
        revision=revision,
        status=EvidenceStatus.UNKNOWN,
    )
    return PhysicalLimit(
        name=name,
        quantity=quantity,
        lower=None,
        upper=None,
        unit=unit,
        space=space,
        frame=frame,
        status=EvidenceStatus.UNKNOWN,
        source=source,
        reason=reason,
    )


__all__ = [
    "EvidenceStatus",
    "FAST_ARM_JOINT_SPACE_FRAME",
    "LimitEvidenceStatus",
    "LimitQuantity",
    "LimitSpace",
    "LimitSourceProvenance",
    "LimitConversionProvenance",
    "PhysicalLimit",
    "PhysicalSafetyEnvelope",
    "PHYSICAL_SAFETY_ENVELOPE_SCHEMA_VERSION",
    "classify_source_status",
    "canonical_fast_arm_joint_space_frame",
    "effective_limit_status",
    "make_unknown_limit",
    "source_identity",
    "validate_concrete_limit_identity",
    "validate_limit_conversion",
    "validate_limit_source",
    "validate_physical_limit",
    "validate_envelope",
]
