"""Deterministic self-interference and environment-clearance policy.

MuJoCo geometry inventoryと明示的なpair policyを入力に取り、collision evidenceを
typed resultへ投影する。implicit global ignoreやviewer-side collision判定は持たない。
"""

from __future__ import annotations

import itertools
import math
import operator
import weakref
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock

from xpotato_sim.runtime.safety.evaluated_candidate import EvaluatedCandidate, EvaluatedJointRoute


# Observation producerだけが登録する。DTOの再構築やIDの付替えでは継承しない。
_COLLISION_CANDIDATES: dict[
    int, tuple[weakref.ReferenceType[object], EvaluatedCandidate]
] = {}
_COLLISION_CANDIDATES_LOCK = RLock()


def _register_collision_candidate(result: object, candidate: EvaluatedCandidate) -> None:
    key = id(result)

    def release(reference: weakref.ReferenceType[object]) -> None:
        with _COLLISION_CANDIDATES_LOCK:
            entry = _COLLISION_CANDIDATES.get(key)
            if entry is not None and entry[0] is reference:
                _COLLISION_CANDIDATES.pop(key, None)

    with _COLLISION_CANDIDATES_LOCK:
        _COLLISION_CANDIDATES[key] = (weakref.ref(result, release), candidate)


class GeometryRole(str, Enum):
    ROBOT = "robot"
    ENVIRONMENT = "environment"
    TASK_OBJECT = "task_object"
    TOOL = "tool"
    UNKNOWN = "unknown"


class CollisionKind(str, Enum):
    SELF_INTERFERENCE = "self_interference"
    STRUCTURAL_PROXIMITY = "structural_proximity"
    ENVIRONMENT_COLLISION = "environment_collision"
    TASK_OBJECT_CONTACT = "task_object_contact"
    UNKNOWN = "unknown"


class CollisionStatus(str, Enum):
    CLEAR = "clear"
    NEAR_COLLISION = "near_collision"
    COLLISION = "collision"
    CONTACT = "contact"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


_PENETRATION_REASON_BY_KIND = {
    CollisionKind.SELF_INTERFERENCE: "self_interference_penetration",
    CollisionKind.STRUCTURAL_PROXIMITY: "structural_proximity_penetration",
    CollisionKind.ENVIRONMENT_COLLISION: "environment_penetration",
    CollisionKind.TASK_OBJECT_CONTACT: "task_object_penetration",
}


class CollisionContractViolation(ValueError):
    """collision contextから安全なtyped bindingを復元できない契約違反。"""


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
        "unknown",
        "unavailable",
    }
)


# provider観測を伴わない内部fail-closed resultだけが、INVALID evaluationの
# provenance省略を許可される。新しいinternal reasonはこの集合へ明示追加する。
_INTERNAL_INVALID_REASON_CODES = frozenset(
    {
        "body_role_overlap",
        "collision_context_binding_invalid",
        "collision_context_inventory_binding_mismatch",
        "collision_context_inventory_mismatch",
        "collision_context_pair_coverage_mismatch",
        "collision_context_policy_binding_mismatch",
        "collision_context_policy_mismatch",
        "collision_exclusion_pair_not_in_inventory",
        "collision_inventory_binding_invalid",
        "collision_observation_pair_not_in_inventory",
        "collision_pair_inventory_empty",
        "collision_policy_binding_invalid",
        "duplicate_collision_observation",
        "environment_collision_exclusion_forbidden",
        "invalid_collision_observation",
        "observations_not_iterable",
        "robot_geometry_missing",
        "self_interference_exclusion_forbidden",
        "trajectory_observations_must_be_non_empty",
        "unknown_geometry_role",
    }
)


# Collision DTOのauthorityは、object.__setattr__でprivate fingerprintまで更新される
# Pythonのdataclass fieldだけに依存しない。ownerが正規化semantic snapshotをobject
# identityへ外部sealとして登録し、weak reference回収時に解放する。
_COLLISION_SEALS: dict[
    int, tuple[weakref.ReferenceType[object], tuple[object, ...]]
] = {}
_COLLISION_SEALS_LOCK = RLock()


def _release_collision_seal(
    key: int,
    reference: weakref.ReferenceType[object],
) -> None:
    with _COLLISION_SEALS_LOCK:
        entry = _COLLISION_SEALS.get(key)
        if entry is not None and entry[0] is reference:
            _COLLISION_SEALS.pop(key, None)


def _register_collision_seal(
    value: object,
    snapshot: tuple[object, ...],
) -> None:
    key = id(value)
    reference = weakref.ref(
        value,
        lambda ref, key=key: _release_collision_seal(key, ref),
    )
    with _COLLISION_SEALS_LOCK:
        _COLLISION_SEALS[key] = (reference, snapshot)


def _sealed_collision_snapshot(value: object) -> tuple[object, ...]:
    key = id(value)
    with _COLLISION_SEALS_LOCK:
        entry = _COLLISION_SEALS.get(key)
        if entry is None or entry[0]() is not value:
            raise ValueError("collision DTO is not constructor-sealed")
        return entry[1]


def _text(name: str, value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _identity_text(name: str, value: object) -> str:
    text = _text(name, value)
    if text.casefold() in _PLACEHOLDER_IDENTITIES:
        raise ValueError(f"{name} must be an explicit non-placeholder identity")
    return text


def _finite(name: str, value: object) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return 0.0 if number == 0.0 else number


def _mujoco_index(
    name: str,
    value: object,
    *,
    upper_bound: int | None = None,
) -> int:
    """MuJoCo count/indexを暗黙変換せず、厳密な整数protocolで正規化する。"""

    value_type = type(value)
    if isinstance(value, bool) or (
        value_type.__module__ == "numpy"
        and value_type.__name__ in {"bool", "bool_"}
    ):
        raise TypeError(f"{name} must be an integer-like scalar")
    try:
        normalized = operator.index(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be an integer-like scalar") from exc
    if normalized < 0:
        raise ValueError(f"{name} must be non-negative")
    if upper_bound is not None and normalized >= upper_bound:
        raise IndexError(f"{name} is out of range")
    return normalized


def _pair_id(name: str, value: object) -> str:
    pair_id = _text(name, value)
    if "*" in pair_id:
        raise ValueError(f"{name} must identify one explicit pair")
    parts = pair_id.split("|")
    if len(parts) != 2 or any(not part or part != part.strip() for part in parts):
        raise ValueError(f"{name} must contain two geometry names")
    if any(part.casefold() in _PLACEHOLDER_IDENTITIES for part in parts):
        raise ValueError(f"{name} must contain concrete geometry identities")
    if parts[0] == parts[1]:
        raise ValueError(f"{name} must identify two different geometries")
    if pair_id != "|".join(sorted(parts)):
        raise ValueError(f"{name} must be name-ordered")
    return pair_id


def _exact_type(value: object, expected: type, name: str) -> None:
    if type(value) is not expected:
        raise TypeError(f"{name} must be an exact {expected.__name__} value")


def _geometry_identity_snapshot(value: GeometryIdentity) -> tuple[object, ...]:
    return (value.geom_name, value.body_name, value.role, value.source_id)


def _collision_exclusion_snapshot(value: CollisionExclusion) -> tuple[object, ...]:
    return (
        value.pair_id,
        value.reason,
        value.evidence_reference,
        value.classification,
    )


def _collision_observation_snapshot(value: CollisionObservation) -> tuple[object, ...]:
    return (value.pair_id, value.distance_m, value.source_id, value.contact)


def _pair_id_parts(value: object) -> tuple[str, str]:
    """canonicalなpair identityを検証し、二つのgeom nameへ分解する。"""

    pair_id = _pair_id("pair_id", value)
    first, second = pair_id.split("|")
    return first, second


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CollisionContext:
    """collision resultを同じrobot/model・policy・inventoryへbindするidentity。"""

    robot_id: str
    model_id: str
    policy_id: str
    policy_revision: str
    inventory_id: str
    inventory_revision: str
    expected_pair_ids: tuple[str, ...]
    inventory_fingerprint: tuple[tuple[str, str, str, str], ...]
    policy_fingerprint: tuple[
        str,
        float,
        float,
        tuple[tuple[str, str, str, str], ...],
    ]
    _binding_fingerprint: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _validate_collision_context(self, initialize=True)


def _canonical_inventory_fingerprint(
    value: object,
) -> tuple[tuple[str, str, str, str], ...]:
    if type(value) is not tuple or not value:
        raise ValueError("inventory_fingerprint must be a non-empty tuple")
    roles = {role.value for role in GeometryRole}
    normalized: list[tuple[str, str, str, str]] = []
    for item in value:
        if type(item) is not tuple or len(item) != 4:
            raise TypeError(
                "inventory_fingerprint must contain geometry identity tuples"
            )
        geom_name = _identity_text("inventory geometry name", item[0])
        body_name = _identity_text("inventory body name", item[1])
        role = item[2]
        if type(role) is not str or role not in roles:
            raise ValueError("inventory geometry role is invalid")
        source_id = _identity_text("inventory source identity", item[3])
        normalized.append((geom_name, body_name, role, source_id))
    if len({item[0] for item in normalized}) != len(normalized):
        raise ValueError("inventory_fingerprint geometry names must be unique")
    return tuple(normalized)


def _pair_ids_from_inventory_fingerprint(
    fingerprint: tuple[tuple[str, str, str, str], ...],
) -> tuple[str, ...]:
    robot_roles = {GeometryRole.ROBOT.value, GeometryRole.TOOL.value}
    pair_ids = tuple(
        "|".join(sorted((first[0], second[0])))
        for first, second in itertools.combinations(fingerprint, 2)
        if first[2] in robot_roles or second[2] in robot_roles
    )
    if pair_ids:
        return pair_ids
    fallback = tuple(
        "|".join(sorted((first[0], second[0])))
        for first, second in itertools.combinations(fingerprint, 2)
    )
    return fallback or ("__invalid_geometry__|__missing_geometry__",)


def _inventory_has_evaluable_pairs(
    fingerprint: tuple[tuple[str, str, str, str], ...],
) -> bool:
    robot_roles = {GeometryRole.ROBOT.value, GeometryRole.TOOL.value}
    return any(
        first[2] in robot_roles or second[2] in robot_roles
        for first, second in itertools.combinations(fingerprint, 2)
    )


def _kind_from_inventory_fingerprint(
    fingerprint: tuple[tuple[str, str, str, str], ...],
    pair_id: str,
) -> CollisionKind:
    by_name = {item[0]: item for item in fingerprint}
    first_name, second_name = pair_id.split("|")
    first = by_name.get(first_name)
    second = by_name.get(second_name)
    if first is None or second is None:
        return CollisionKind.UNKNOWN
    roles = {first[2], second[2]}
    if GeometryRole.UNKNOWN.value in roles:
        return CollisionKind.UNKNOWN
    if GeometryRole.TASK_OBJECT.value in roles:
        return CollisionKind.TASK_OBJECT_CONTACT
    if GeometryRole.ENVIRONMENT.value in roles:
        return CollisionKind.ENVIRONMENT_COLLISION
    if first[1] == second[1]:
        return CollisionKind.STRUCTURAL_PROXIMITY
    return CollisionKind.SELF_INTERFERENCE


def _canonical_policy_fingerprint(
    value: object,
) -> tuple[str, float, float, tuple[tuple[str, str, str, str], ...]]:
    if type(value) is not tuple or len(value) != 4:
        raise TypeError(
            "policy_fingerprint must contain policy identity, thresholds, and exclusions"
        )
    policy_id = _identity_text("policy fingerprint policy_id", value[0])
    if type(value[1]) is not float or type(value[2]) is not float:
        raise TypeError("policy fingerprint thresholds must be float values")
    clearance = _finite("policy fingerprint clearance_m", value[1])
    margin = _finite("policy fingerprint near_collision_margin_m", value[2])
    if clearance < 0.0 or margin < 0.0:
        raise ValueError("policy fingerprint thresholds must be non-negative")
    exclusions_value = value[3]
    if type(exclusions_value) is not tuple:
        raise TypeError("policy fingerprint exclusions must be a tuple")
    exclusions: list[tuple[str, str, str, str]] = []
    for item in exclusions_value:
        if type(item) is not tuple or len(item) != 4:
            raise TypeError(
                "policy fingerprint exclusions must contain identity tuples"
            )
        pair_id = _pair_id("policy fingerprint exclusion pair_id", item[0])
        reason = _text("policy fingerprint exclusion reason", item[1])
        evidence_reference = _identity_text(
            "policy fingerprint exclusion evidence_reference", item[2]
        )
        classification = item[3]
        if classification != CollisionKind.STRUCTURAL_PROXIMITY.value:
            raise ValueError("policy fingerprint exclusion classification is invalid")
        exclusions.append((pair_id, reason, evidence_reference, classification))
    if len({item[0] for item in exclusions}) != len(exclusions):
        raise ValueError("policy fingerprint exclusion pair IDs must be unique")
    return (policy_id, clearance, margin, tuple(sorted(exclusions)))


def _validate_collision_context(
    context: CollisionContext,
    *,
    initialize: bool = False,
) -> None:
    """contextのidentityと、constructor後の変更を一つの規則で検証する。"""

    _exact_type(context, CollisionContext, "context")
    try:
        for name, value in (
            ("robot_id", context.robot_id),
            ("model_id", context.model_id),
            ("policy_id", context.policy_id),
            ("policy_revision", context.policy_revision),
            ("inventory_id", context.inventory_id),
            ("inventory_revision", context.inventory_revision),
        ):
            _identity_text(name, value)
        if type(context.expected_pair_ids) is not tuple:
            raise TypeError("expected_pair_ids must be a tuple")
        expected_pair_ids = tuple(
            _pair_id("expected_pair_id", pair_id)
            for pair_id in context.expected_pair_ids
        )
        if not expected_pair_ids:
            raise ValueError("expected_pair_ids must be non-empty")
        if len(expected_pair_ids) != len(set(expected_pair_ids)):
            raise ValueError("expected_pair_ids must be unique")
        inventory_fingerprint = _canonical_inventory_fingerprint(
            context.inventory_fingerprint
        )
        _validate_context_inventory_semantics(inventory_fingerprint)
        policy_fingerprint = _canonical_policy_fingerprint(context.policy_fingerprint)
        canonical_pair_ids = _pair_ids_from_inventory_fingerprint(inventory_fingerprint)
        expected_pair_id_set = set(canonical_pair_ids)
        for pair_id, _, _, classification in policy_fingerprint[3]:
            if pair_id not in expected_pair_id_set:
                raise ValueError(
                    "policy fingerprint exclusion pair must be in inventory expected pairs"
                )
            if (
                _kind_from_inventory_fingerprint(inventory_fingerprint, pair_id)
                is not CollisionKind.STRUCTURAL_PROXIMITY
                or classification != CollisionKind.STRUCTURAL_PROXIMITY.value
            ):
                raise ValueError(
                    "policy fingerprint exclusion must target inventory-derived structural proximity"
                )
    except AttributeError as exc:
        raise ValueError("collision context binding is incomplete") from exc
    if context.policy_id != policy_fingerprint[0]:
        raise ValueError("context policy_id must match policy fingerprint identity")
    if initialize and expected_pair_ids != canonical_pair_ids:
        raise ValueError(
            "expected_pair_ids must exactly match inventory fingerprint pairs"
        )
    fingerprint = _collision_context_snapshot(context)
    if initialize:
        object.__setattr__(context, "expected_pair_ids", expected_pair_ids)
        object.__setattr__(context, "inventory_fingerprint", inventory_fingerprint)
        object.__setattr__(context, "policy_fingerprint", policy_fingerprint)
        fingerprint = _collision_context_snapshot(context)
        object.__setattr__(context, "_binding_fingerprint", fingerprint)
        _register_collision_seal(context, fingerprint)
        return
    try:
        original_fingerprint = context._binding_fingerprint
    except AttributeError as exc:
        raise ValueError("collision context binding fingerprint is missing") from exc
    if original_fingerprint != fingerprint:
        raise ValueError("collision context binding was mutated")
    if expected_pair_ids != canonical_pair_ids:
        raise ValueError(
            "expected_pair_ids must exactly match inventory fingerprint pairs"
        )
    _validate_collision_seal(context, fingerprint)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class GeometryIdentity:
    """MuJoCo geomのlogical identityとsemantic role。"""

    geom_name: str
    body_name: str
    role: GeometryRole
    source_id: str = "mujoco-model"

    def __post_init__(self) -> None:
        _exact_type(self, GeometryIdentity, "geometry identity")
        _identity_text("geom_name", self.geom_name)
        _identity_text("body_name", self.body_name)
        if type(self.role) is not GeometryRole:
            object.__setattr__(self, "role", GeometryRole(self.role))
        _identity_text("source_id", self.source_id)
        _register_collision_seal(self, _geometry_identity_snapshot(self))


@dataclass(frozen=True, slots=True, weakref_slot=True)
class GeometryInventory:
    """policyで検査するexplicit geom集合。"""

    geometries: tuple[GeometryIdentity, ...]
    inventory_id: str = "geometry-inventory/v1"

    def __post_init__(self) -> None:
        _exact_type(self, GeometryInventory, "geometry inventory")
        if type(self.geometries) is not tuple:
            raise TypeError("geometries must be a tuple")
        _identity_text("inventory_id", self.inventory_id)
        if not self.geometries:
            raise ValueError("geometry inventory must not be empty")
        if not all(type(geom) is GeometryIdentity for geom in self.geometries):
            raise TypeError("geometries must contain GeometryIdentity values")
        for geom in self.geometries:
            _validate_geometry_identity(geom)
        names = tuple(geom.geom_name for geom in self.geometries)
        if len(names) != len(set(names)):
            raise ValueError("geometry names must be unique")
        _register_collision_seal(self, _geometry_inventory_snapshot(self))

    def by_name(self) -> dict[str, GeometryIdentity]:
        _validate_geometry_inventory(self)
        return {geom.geom_name: geom for geom in self.geometries}

    def pairs(self) -> tuple["CollisionPair", ...]:
        _validate_geometry_inventory(self)
        return tuple(
            CollisionPair(first, second)
            for first, second in itertools.combinations(self.geometries, 2)
            if (
                first.role in {GeometryRole.ROBOT, GeometryRole.TOOL}
                or second.role in {GeometryRole.ROBOT, GeometryRole.TOOL}
            )
        )


def _inventory_fingerprint(
    inventory: GeometryInventory,
) -> tuple[tuple[str, str, str, str], ...]:
    return _canonical_inventory_fingerprint(
        tuple(
            (geom.geom_name, geom.body_name, geom.role.value, geom.source_id)
            for geom in inventory.geometries
        )
    )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CollisionPair:
    """wildcardを許さないordered-by-name geom pair。"""

    first: GeometryIdentity
    second: GeometryIdentity

    def __post_init__(self) -> None:
        _exact_type(self, CollisionPair, "collision pair")
        if type(self.first) is not GeometryIdentity or type(self.second) is not GeometryIdentity:
            raise TypeError("collision pair members must be GeometryIdentity")
        if self.first.geom_name == self.second.geom_name:
            raise ValueError("collision pair cannot contain the same geometry")
        _register_collision_seal(self, _collision_pair_snapshot(self))

    @property
    def pair_id(self) -> str:
        _validate_collision_pair(self)
        return "|".join(sorted((self.first.geom_name, self.second.geom_name)))

    @property
    def kind(self) -> CollisionKind:
        _validate_collision_pair(self)
        roles = {self.first.role, self.second.role}
        if GeometryRole.UNKNOWN in roles:
            return CollisionKind.UNKNOWN
        if GeometryRole.TASK_OBJECT in roles:
            return CollisionKind.TASK_OBJECT_CONTACT
        if GeometryRole.ENVIRONMENT in roles:
            return CollisionKind.ENVIRONMENT_COLLISION
        if self.first.body_name == self.second.body_name:
            return CollisionKind.STRUCTURAL_PROXIMITY
        return CollisionKind.SELF_INTERFERENCE


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CollisionExclusion:
    """根拠付きのsingle-pair structural exclusion。"""

    pair_id: str
    reason: str
    evidence_reference: str
    classification: CollisionKind = CollisionKind.STRUCTURAL_PROXIMITY

    def __post_init__(self) -> None:
        _exact_type(self, CollisionExclusion, "collision exclusion")
        pair_id = _pair_id("pair_id", self.pair_id)
        _text("reason", self.reason)
        _identity_text("evidence_reference", self.evidence_reference)
        if type(self.classification) is not CollisionKind:
            object.__setattr__(self, "classification", CollisionKind(self.classification))
        if self.classification is not CollisionKind.STRUCTURAL_PROXIMITY:
            raise ValueError("only structural_proximity pairs may be excluded")
        object.__setattr__(self, "pair_id", pair_id)
        _register_collision_seal(self, _collision_exclusion_snapshot(self))


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CollisionPolicy:
    """clearance / near-collision thresholdsとexplicit exclusions。"""

    policy_id: str
    clearance_m: float
    near_collision_margin_m: float
    exclusions: tuple[CollisionExclusion, ...] = ()

    def __post_init__(self) -> None:
        _exact_type(self, CollisionPolicy, "collision policy")
        _identity_text("policy_id", self.policy_id)
        clearance = _finite("clearance_m", self.clearance_m)
        margin = _finite("near_collision_margin_m", self.near_collision_margin_m)
        if clearance < 0.0 or margin < 0.0:
            raise ValueError("clearance and near-collision margin must be non-negative")
        if type(self.exclusions) is not tuple:
            raise TypeError("exclusions must be a tuple")
        if not all(type(item) is CollisionExclusion for item in self.exclusions):
            raise TypeError("exclusions must contain CollisionExclusion values")
        for item in self.exclusions:
            _validate_collision_exclusion(item)
        pair_ids = tuple(item.pair_id for item in self.exclusions)
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("collision exclusion pair IDs must be unique")
        object.__setattr__(self, "clearance_m", clearance)
        object.__setattr__(self, "near_collision_margin_m", margin)
        _register_collision_seal(self, _collision_policy_snapshot(self))

    def exclusion_for(self, pair_id: str) -> CollisionExclusion | None:
        _validate_collision_policy(self)
        for exclusion in self.exclusions:
            if exclusion.pair_id == pair_id:
                return exclusion
        return None


def _policy_fingerprint(
    policy: CollisionPolicy,
) -> tuple[str, float, float, tuple[tuple[str, str, str, str], ...]]:
    return _canonical_policy_fingerprint(
        (
            policy.policy_id,
            _finite("clearance_m", policy.clearance_m),
            _finite("near_collision_margin_m", policy.near_collision_margin_m),
            tuple(
                (
                    exclusion.pair_id,
                    exclusion.reason,
                    exclusion.evidence_reference,
                    exclusion.classification.value,
                )
                for exclusion in policy.exclusions
            ),
        )
    )


def _policy_fingerprint_without_exclusions(
    policy: CollisionPolicy,
) -> tuple[str, float, float, tuple[tuple[str, str, str, str], ...]]:
    """invalid policyをtyped INVALID bindingへ閉じるための最小fingerprint。"""

    return _canonical_policy_fingerprint(
        (
            policy.policy_id,
            _finite("clearance_m", policy.clearance_m),
            _finite("near_collision_margin_m", policy.near_collision_margin_m),
            (),
        )
    )


def _collision_pair_snapshot(value: CollisionPair) -> tuple[object, ...]:
    return (
        id(value.first),
        _geometry_identity_snapshot(value.first),
        id(value.second),
        _geometry_identity_snapshot(value.second),
    )


def _geometry_inventory_snapshot(value: GeometryInventory) -> tuple[object, ...]:
    return (
        value.inventory_id,
        tuple(
            (id(geometry), _geometry_identity_snapshot(geometry))
            for geometry in value.geometries
        ),
    )


def _collision_policy_snapshot(value: CollisionPolicy) -> tuple[object, ...]:
    return (
        value.policy_id,
        value.clearance_m,
        value.near_collision_margin_m,
        tuple(
            (id(exclusion), _collision_exclusion_snapshot(exclusion))
            for exclusion in value.exclusions
        ),
    )


def _validate_geometry_identity(
    value: GeometryIdentity,
    *,
    require_seal: bool = True,
) -> None:
    _exact_type(value, GeometryIdentity, "geometry identity")
    try:
        _identity_text("geom_name", value.geom_name)
        _identity_text("body_name", value.body_name)
        if type(value.role) is not GeometryRole:
            raise TypeError("geometry role must be GeometryRole")
        _identity_text("source_id", value.source_id)
        snapshot = _geometry_identity_snapshot(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("geometry identity is invalid") from exc
    if require_seal:
        _validate_collision_seal(value, snapshot)


def _validate_geometry_inventory(
    value: GeometryInventory,
    *,
    require_seal: bool = True,
) -> None:
    _exact_type(value, GeometryInventory, "geometry inventory")
    try:
        if type(value.geometries) is not tuple or not value.geometries:
            raise ValueError("geometry inventory must not be empty")
        _identity_text("inventory_id", value.inventory_id)
        for geometry in value.geometries:
            _validate_geometry_identity(geometry)
        names = tuple(geometry.geom_name for geometry in value.geometries)
        if len(names) != len(set(names)):
            raise ValueError("geometry names must be unique")
        snapshot = _geometry_inventory_snapshot(value)
    except (AttributeError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) in {
            "geometry names must be unique",
            "geometry inventory must not be empty",
        }:
            raise
        raise ValueError("geometry inventory is invalid") from exc
    if require_seal:
        _validate_collision_seal(value, snapshot)


def _validate_collision_pair(value: CollisionPair) -> None:
    _exact_type(value, CollisionPair, "collision pair")
    try:
        _validate_geometry_identity(value.first)
        _validate_geometry_identity(value.second)
        if value.first.geom_name == value.second.geom_name:
            raise ValueError("collision pair cannot contain the same geometry")
        snapshot = _collision_pair_snapshot(value)
    except (AttributeError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).endswith(
            "same geometry"
        ):
            raise
        raise ValueError("collision pair is invalid") from exc
    _validate_collision_seal(value, snapshot)


def _validate_collision_exclusion(value: CollisionExclusion) -> None:
    _exact_type(value, CollisionExclusion, "collision exclusion")
    try:
        _pair_id("pair_id", value.pair_id)
        _text("reason", value.reason)
        _identity_text("evidence_reference", value.evidence_reference)
        if type(value.classification) is not CollisionKind:
            raise TypeError("exclusion classification must be CollisionKind")
        if value.classification is not CollisionKind.STRUCTURAL_PROXIMITY:
            raise ValueError("only structural_proximity pairs may be excluded")
        snapshot = _collision_exclusion_snapshot(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("collision exclusion is invalid") from exc
    _validate_collision_seal(value, snapshot)


def _validate_collision_policy(value: CollisionPolicy) -> None:
    _exact_type(value, CollisionPolicy, "collision policy")
    try:
        _identity_text("policy_id", value.policy_id)
        clearance = _finite("clearance_m", value.clearance_m)
        margin = _finite("near_collision_margin_m", value.near_collision_margin_m)
        if clearance < 0.0 or margin < 0.0:
            raise ValueError("collision thresholds must be non-negative")
        if type(value.exclusions) is not tuple:
            raise TypeError("exclusions must be a tuple")
        for exclusion in value.exclusions:
            _validate_collision_exclusion(exclusion)
        pair_ids = tuple(exclusion.pair_id for exclusion in value.exclusions)
        if len(pair_ids) != len(set(pair_ids)):
            raise ValueError("collision exclusion pair IDs must be unique")
        snapshot = _collision_policy_snapshot(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("collision policy is invalid") from exc
    _validate_collision_seal(value, snapshot)


def _validate_collision_observation(value: CollisionObservation) -> None:
    _exact_type(value, CollisionObservation, "collision observation")
    try:
        _pair_id("pair_id", value.pair_id)
        _identity_text("source_id", value.source_id)
        if value.distance_m is not None:
            _finite("distance_m", value.distance_m)
        if type(value.contact) is not bool:
            raise TypeError("contact must be bool")
        snapshot = _collision_observation_snapshot(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("collision observation is invalid") from exc
    _validate_collision_seal(value, snapshot)


def _collision_context_snapshot(
    context: CollisionContext,
) -> tuple[object, ...]:
    return (
        context.robot_id,
        context.model_id,
        context.policy_id,
        context.policy_revision,
        context.inventory_id,
        context.inventory_revision,
        context.expected_pair_ids,
        context.inventory_fingerprint,
        context.policy_fingerprint,
    )


def _collision_evaluation_snapshot(
    evaluation: CollisionEvaluation,
) -> tuple[object, ...]:
    return (
        evaluation.pair_id,
        evaluation.kind,
        evaluation.status,
        evaluation.distance_m,
        evaluation.clearance_m,
        evaluation.reason_code,
        evaluation.provenance,
        evaluation.near_collision_margin_m,
    )


def _collision_check_result_snapshot(
    result: CollisionCheckResult,
) -> tuple[object, ...]:
    try:
        return (
            id(result.context),
            _collision_context_snapshot(result.context),
            result.status,
            tuple(
                (id(item), _collision_evaluation_snapshot(item))
                for item in result.evaluations
            ),
            result.reason_code,
        )
    except (AttributeError, TypeError):
        raise ValueError("collision result binding is incomplete") from None


def _bounded_collision_trajectory_snapshot(
    result: BoundedCollisionTrajectoryResult,
) -> tuple[object, ...]:
    return (
        result.status,
        tuple(
            (id(item), _collision_check_result_snapshot(item))
            for item in result.sample_results
        ),
        result.sample_indices,
        result.failed_sample_index,
    )


def _validate_collision_seal(
    value: object,
    snapshot: tuple[object, ...],
) -> None:
    if _sealed_collision_snapshot(value) != snapshot:
        raise ValueError("collision DTO has been mutated or bypassed")


def _validate_context_inventory_semantics(
    fingerprint: tuple[tuple[str, str, str, str], ...],
) -> None:
    roles_by_body: dict[str, set[str]] = {}
    for _, body_name, role, _ in fingerprint:
        if role == GeometryRole.UNKNOWN.value:
            raise ValueError("inventory geometry role must be known")
        roles_by_body.setdefault(body_name, set()).add(role)
    if any(len(roles) > 1 for roles in roles_by_body.values()):
        raise ValueError("body role sets must be disjoint")
    if not any(role == GeometryRole.ROBOT.value for _, _, role, _ in fingerprint):
        raise ValueError("inventory must contain required robot geometry")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CollisionObservation:
    """1 pairのdistance evidence。distanceはgeom surface間のmeter。"""

    pair_id: str
    distance_m: float | None
    source_id: str
    contact: bool = False

    def __post_init__(self) -> None:
        _exact_type(self, CollisionObservation, "collision observation")
        pair_id = _pair_id("pair_id", self.pair_id)
        _identity_text("source_id", self.source_id)
        object.__setattr__(self, "pair_id", pair_id)
        if self.distance_m is not None:
            object.__setattr__(self, "distance_m", _finite("distance_m", self.distance_m))
        if not isinstance(self.contact, bool):
            raise TypeError("contact must be bool")
        _register_collision_seal(self, _collision_observation_snapshot(self))


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CollisionEvaluation:
    """pairごとのoperator / machine-readable result。"""

    pair_id: str
    kind: CollisionKind
    status: CollisionStatus
    distance_m: float | None
    clearance_m: float
    reason_code: str
    provenance: str | None = None
    near_collision_margin_m: float = 0.0
    _canonical_snapshot: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _exact_type(self, CollisionEvaluation, "collision evaluation")
        pair_id = _pair_id("pair_id", self.pair_id)
        if type(self.kind) is not CollisionKind:
            object.__setattr__(self, "kind", CollisionKind(self.kind))
        if type(self.status) is not CollisionStatus:
            object.__setattr__(self, "status", CollisionStatus(self.status))
        if self.distance_m is not None:
            object.__setattr__(self, "distance_m", _finite("distance_m", self.distance_m))
        clearance = _finite("clearance_m", self.clearance_m)
        if clearance < 0.0:
            raise ValueError("clearance_m must be non-negative")
        near_margin = _finite(
            "near_collision_margin_m", self.near_collision_margin_m
        )
        if near_margin < 0.0:
            raise ValueError("near_collision_margin_m must be non-negative")
        object.__setattr__(self, "pair_id", pair_id)
        object.__setattr__(self, "clearance_m", clearance)
        object.__setattr__(self, "near_collision_margin_m", near_margin)
        _text("reason_code", self.reason_code)
        if self.provenance is not None:
            _identity_text("provenance", self.provenance)
        inconsistency = _collision_evaluation_inconsistency(
            self,
            require_seal=False,
        )
        if inconsistency is not None:
            raise ValueError(inconsistency)
        snapshot = _collision_evaluation_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_collision_seal(self, snapshot)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CollisionCheckResult:
    """configuration collision checkのaggregate。"""

    context: CollisionContext
    status: CollisionStatus
    evaluations: tuple[CollisionEvaluation, ...]
    reason_code: str
    _canonical_snapshot: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _exact_type(self, CollisionCheckResult, "collision result")
        _validate_collision_context(self.context)
        if type(self.status) is not CollisionStatus:
            object.__setattr__(self, "status", CollisionStatus(self.status))
        if type(self.evaluations) is not tuple:
            raise TypeError("evaluations must be a tuple")
        _text("reason_code", self.reason_code)
        status, reason_code = _derive_collision_status_reason(
            self.context, self.evaluations
        )
        if self.status is not status or self.reason_code != reason_code:
            raise ValueError(
                "collision aggregate status/reason must match canonical derivation"
            )
        snapshot = _collision_check_result_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_collision_seal(self, snapshot)

    @property
    def clear(self) -> bool:
        try:
            _validate_collision_check_result(self)
        except Exception:
            return False
        return self.status is CollisionStatus.CLEAR

    @property
    def evaluated_candidate(self) -> EvaluatedCandidate | None:
        """実際にMuJoCo observationを生成したstateの値identity。"""
        _validate_collision_check_result(self)
        with _COLLISION_CANDIDATES_LOCK:
            entry = _COLLISION_CANDIDATES.get(id(self))
            return entry[1] if entry is not None and entry[0]() is self else None


def _validate_collision_check_result(result: CollisionCheckResult) -> None:
    """aggregateとnested evaluationをpublic clear accessでも再検証する。"""

    _exact_type(result, CollisionCheckResult, "collision result")
    _validate_collision_context(result.context)
    if type(result.status) is not CollisionStatus:
        raise TypeError("collision result status is invalid")
    if type(result.evaluations) is not tuple:
        raise TypeError("collision result evaluations must be a tuple")
    _text("reason_code", result.reason_code)
    status, reason_code = _derive_collision_status_reason(
        result.context, result.evaluations
    )
    if result.status is not status or result.reason_code != reason_code:
        raise ValueError("collision aggregate status/reason is inconsistent")
    _validate_collision_seal(result, _collision_check_result_snapshot(result))


def _collision_evaluation_inconsistency(
    evaluation: CollisionEvaluation,
    *,
    require_seal: bool = True,
) -> str | None:
    """1 pairのstatusとevidenceの整合性をcanonicalに検証する。"""

    if type(evaluation) is not CollisionEvaluation:
        return "collision evaluation has an invalid type"
    try:
        pair_id = evaluation.pair_id
        kind = evaluation.kind
        status = evaluation.status
        distance = evaluation.distance_m
        clearance = evaluation.clearance_m
        near_margin = evaluation.near_collision_margin_m
        reason_code = evaluation.reason_code
        provenance = evaluation.provenance
    except Exception:
        return "collision evaluation is structurally incomplete"
    try:
        _pair_id("pair_id", pair_id)
    except (TypeError, ValueError):
        return "collision evaluation pair identity is invalid"
    if type(kind) is not CollisionKind:
        return "collision evaluation kind is invalid"
    if type(status) is not CollisionStatus:
        return "collision evaluation status is invalid"
    try:
        clearance = _finite("clearance_m", clearance)
    except (TypeError, ValueError):
        return "collision evaluation clearance is invalid"
    if clearance < 0.0:
        return "collision evaluation clearance is invalid"
    try:
        near_margin = _finite("near_collision_margin_m", near_margin)
    except (TypeError, ValueError):
        return "collision evaluation near-collision margin is invalid"
    if near_margin < 0.0:
        return "collision evaluation near-collision margin is invalid"
    if distance is not None:
        try:
            distance = _finite("distance_m", distance)
        except (TypeError, ValueError):
            return "collision evaluation distance is invalid"
    if type(reason_code) is not str or not reason_code or reason_code != reason_code.strip():
        return "collision evaluation reason is invalid"
    if provenance is not None and (
        type(provenance) is not str
        or not provenance
        or provenance != provenance.strip()
        or provenance.casefold() in _PLACEHOLDER_IDENTITIES
    ):
        return "collision evaluation provenance is invalid"

    if kind is CollisionKind.UNKNOWN:
        return "unknown collision kind cannot produce evidence"
    if status is CollisionStatus.CLEAR:
        if reason_code == "explicit_structural_exclusion":
            if kind is not CollisionKind.STRUCTURAL_PROXIMITY:
                return "structural exclusion clear evidence has the wrong kind"
            if distance is not None or provenance is None:
                return "structural exclusion clear evidence is incomplete"
        elif reason_code == "pair_clear":
            if distance is None or distance <= clearance + near_margin:
                return (
                    "pair_clear evidence is not beyond clearance and near-collision margin"
                )
            if provenance is None:
                return "pair_clear evidence has no provenance"
        else:
            return "clear collision evidence has an unsupported reason"
    elif status is CollisionStatus.CONTACT:
        if provenance is None:
            return "contact evidence has no provenance"
        if kind is not CollisionKind.TASK_OBJECT_CONTACT:
            return "only task-object pairs may produce contact evidence"
        if distance is None:
            return "contact evidence requires distance"
        if distance < 0.0:
            return "contact evidence requires non-negative distance"
        if reason_code != "task_object_contact":
            return "contact evidence has an unsupported reason"
    elif status is CollisionStatus.NEAR_COLLISION:
        if provenance is None:
            return "near-collision evidence has no provenance"
        if distance is None or distance < 0.0 or distance > clearance + near_margin:
            return "near-collision evidence is outside the clearance margin"
    elif status is CollisionStatus.COLLISION:
        if provenance is None:
            return "collision evidence has no provenance"
        if distance is None or distance >= 0.0:
            return "collision evidence requires negative distance"
        if reason_code != _PENETRATION_REASON_BY_KIND.get(kind):
            return "collision evidence reason is inconsistent with collision kind"
    elif status is CollisionStatus.UNKNOWN:
        if distance is not None:
            return "unknown or unavailable evidence must omit distance"
        if provenance is None:
            return "unknown collision evidence has no provenance"
    elif status is CollisionStatus.UNAVAILABLE:
        if distance is not None:
            return "unknown or unavailable evidence must omit distance"
    elif status is CollisionStatus.INVALID:
        if provenance is None and (
            distance is not None or reason_code not in _INTERNAL_INVALID_REASON_CODES
        ):
            return "invalid collision evidence has no provenance"
    if require_seal:
        try:
            _validate_collision_seal(
                evaluation,
                _collision_evaluation_snapshot(evaluation),
            )
        except (AttributeError, TypeError, ValueError):
            return "collision evaluation has been mutated or bypassed"
    return None


def _declared_exclusion_inconsistency(
    evaluation: CollisionEvaluation,
    declared: tuple[str, str] | None,
) -> str | None:
    """declared exclusion pairのvalid-path clear evidenceを検証する。"""

    if declared is None:
        return None
    if evaluation.status is CollisionStatus.INVALID:
        # An internal fail-closed result must be INVALID for every expected
        # pair, including a pair that would be an explicit CLEAR exclusion on
        # a valid path. The exclusion remains bound in the context policy
        # fingerprint and is still emitted as clear evidence by valid paths.
        return None
    evidence_reference, classification = declared
    if (
        evaluation.kind is not CollisionKind.STRUCTURAL_PROXIMITY
        or evaluation.status is not CollisionStatus.CLEAR
        or evaluation.distance_m is not None
        or evaluation.reason_code != "explicit_structural_exclusion"
        or evaluation.provenance != evidence_reference
        or classification != CollisionKind.STRUCTURAL_PROXIMITY.value
    ):
        return "declared structural exclusion must be an exact clear evaluation"
    return None


def _derive_collision_status_reason(
    context: CollisionContext,
    evaluations: Sequence[CollisionEvaluation],
) -> tuple[CollisionStatus, str]:
    """coverage、pair evidence、aggregateを一つのcanonical pathで導出する。"""

    _validate_collision_context(context)
    if type(evaluations) is not tuple:
        raise TypeError("evaluations must be a tuple")
    if not evaluations:
        raise ValueError("evaluations must exactly cover expected_pair_ids")
    pair_ids: list[str] = []
    for evaluation in evaluations:
        if type(evaluation) is not CollisionEvaluation:
            raise TypeError("evaluations must contain CollisionEvaluation values")
        try:
            pair_ids.append(evaluation.pair_id)
        except (AttributeError, TypeError):
            raise ValueError(
                "evaluations must contain complete CollisionEvaluation values"
            ) from None
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("collision evaluations must have unique pair IDs")
    if set(pair_ids) != set(context.expected_pair_ids):
        raise ValueError("evaluations must exactly cover expected_pair_ids")

    by_pair_id = {evaluation.pair_id: evaluation for evaluation in evaluations}
    ordered = tuple(by_pair_id[pair_id] for pair_id in context.expected_pair_ids)
    declared_exclusions = {
        item[0]: (item[2], item[3])
        for item in context.policy_fingerprint[3]
    }
    for evaluation in ordered:
        inconsistency = _collision_evaluation_inconsistency(evaluation)
        if inconsistency is not None:
            if getattr(evaluation, "pair_id", None) in declared_exclusions:
                raise ValueError(
                    "declared structural exclusion evaluation is inconsistent"
                )
            return CollisionStatus.INVALID, "collision_result_inconsistent"
        exclusion_inconsistency = _declared_exclusion_inconsistency(
            evaluation,
            declared_exclusions.get(evaluation.pair_id),
        )
        if exclusion_inconsistency is not None:
            raise ValueError(exclusion_inconsistency)
    policy_clearance = context.policy_fingerprint[1]
    policy_near_margin = context.policy_fingerprint[2]
    if any(
        type(item.clearance_m) is not float
        or type(item.near_collision_margin_m) is not float
        or item.clearance_m != policy_clearance
        or item.near_collision_margin_m != policy_near_margin
        for item in ordered
    ):
        if any(
            evaluation.pair_id in declared_exclusions
            for evaluation in ordered
            if (
                type(evaluation.clearance_m) is not float
                or type(evaluation.near_collision_margin_m) is not float
                or evaluation.clearance_m != policy_clearance
                or evaluation.near_collision_margin_m != policy_near_margin
            )
        ):
            raise ValueError(
                "declared structural exclusion evaluation is inconsistent"
            )
        return CollisionStatus.INVALID, "collision_result_inconsistent"
    inventory_fingerprint = context.inventory_fingerprint
    if not _inventory_has_evaluable_pairs(inventory_fingerprint) and any(
        item.status is not CollisionStatus.INVALID for item in ordered
    ):
        return CollisionStatus.INVALID, "collision_result_inconsistent"
    for evaluation in ordered:
        if evaluation.status is CollisionStatus.INVALID:
            continue
        inventory_kind = _kind_from_inventory_fingerprint(
            inventory_fingerprint,
            evaluation.pair_id,
        )
        if evaluation.reason_code == "explicit_structural_exclusion":
            if (
                inventory_kind is not CollisionKind.STRUCTURAL_PROXIMITY
                or evaluation.kind is not CollisionKind.STRUCTURAL_PROXIMITY
            ):
                return CollisionStatus.INVALID, "collision_result_inconsistent"
        elif evaluation.kind is not inventory_kind:
            return CollisionStatus.INVALID, "collision_result_inconsistent"
    for evaluation in ordered:
        if evaluation.reason_code != "explicit_structural_exclusion":
            continue
        declared = declared_exclusions.get(evaluation.pair_id)
        if (
            declared is None
            or evaluation.provenance != declared[0]
            or declared[1] != CollisionKind.STRUCTURAL_PROXIMITY.value
        ):
            return CollisionStatus.INVALID, "collision_result_inconsistent"

    precedence = (
        CollisionStatus.INVALID,
        CollisionStatus.COLLISION,
        CollisionStatus.NEAR_COLLISION,
        CollisionStatus.CONTACT,
        CollisionStatus.UNAVAILABLE,
        CollisionStatus.UNKNOWN,
    )
    for status in precedence:
        found = next((item for item in ordered if item.status is status), None)
        if found is not None:
            return status, found.reason_code
    return CollisionStatus.CLEAR, "collision_clear"


def _validate_inventory(inventory: GeometryInventory) -> str | None:
    try:
        _validate_geometry_inventory(inventory)
        names = {geom.geom_name: geom for geom in inventory.geometries}
        if any(geom.role is GeometryRole.UNKNOWN for geom in names.values()):
            return "unknown_geometry_role"
        roles_by_body: dict[str, set[GeometryRole]] = {}
        for geometry in names.values():
            roles_by_body.setdefault(geometry.body_name, set()).add(geometry.role)
        if any(len(roles) > 1 for roles in roles_by_body.values()):
            return "body_role_overlap"
        if not any(geom.role is GeometryRole.ROBOT for geom in names.values()):
            return "robot_geometry_missing"
        return None
    except (AttributeError, TypeError, ValueError):
        return "collision_inventory_binding_invalid"


def _validate_policy(inventory: GeometryInventory, policy: CollisionPolicy) -> str | None:
    try:
        _validate_geometry_inventory(inventory)
        _validate_collision_policy(policy)
        pair_by_id = {pair.pair_id: pair for pair in inventory.pairs()}
        for exclusion in policy.exclusions:
            pair = pair_by_id.get(exclusion.pair_id)
            if pair is None:
                return "collision_exclusion_pair_not_in_inventory"
            if pair.kind is CollisionKind.SELF_INTERFERENCE:
                return "self_interference_exclusion_forbidden"
            if pair.kind is not CollisionKind.STRUCTURAL_PROXIMITY:
                return "environment_collision_exclusion_forbidden"
        return None
    except (AttributeError, TypeError, ValueError):
        return "collision_policy_binding_invalid"


def _context_expected_pair_ids(inventory: GeometryInventory) -> tuple[str, ...]:
    """inventoryがinvalidでもfail-closed resultをbindできるpair identityを返す。"""

    pair_ids = tuple(pair.pair_id for pair in inventory.pairs())
    if pair_ids:
        return pair_ids
    fallback = tuple(
        "|".join(sorted((first.geom_name, second.geom_name)))
        for first, second in itertools.combinations(inventory.geometries, 2)
    )
    if fallback:
        return fallback
    return ("__invalid_geometry__|__missing_geometry__",)


def _invalid_inventory_context(
    inventory: GeometryInventory,
    policy: CollisionPolicy,
    *,
    robot_id: str,
    model_id: str,
    policy_revision: str,
    inventory_revision: str,
) -> CollisionContext:
    """invalid inventoryをtyped INVALID resultへ閉じるための安全なbinding。"""

    # context contract自体はsemanticにvalidとし、実inventoryは呼び出し元の
    # INVALID reasonを決める前段で検査する。unknown identityを発明せずpairを
    # dropもしないため、全geometryを一時的にrobot roleとしてbindingする。
    try:
        geometries = inventory.geometries
        if type(geometries) is not tuple or not geometries:
            raise CollisionContractViolation(
                "collision inventory identity is unavailable"
            )
    except AttributeError as exc:
        raise CollisionContractViolation(
            "collision inventory identity is unavailable"
        ) from exc
    try:
        fingerprint = tuple(
            (geom.geom_name, geom.body_name, GeometryRole.ROBOT.value, geom.source_id)
            for geom in geometries
        )
        return CollisionContext(
            robot_id=robot_id,
            model_id=model_id,
            policy_id=policy.policy_id,
            policy_revision=policy_revision,
            inventory_id=inventory.inventory_id,
            inventory_revision=inventory_revision,
            expected_pair_ids=_pair_ids_from_inventory_fingerprint(fingerprint),
            inventory_fingerprint=fingerprint,
            policy_fingerprint=_policy_fingerprint_without_exclusions(policy),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise CollisionContractViolation(
            "collision inventory identity is unavailable"
        ) from exc


def _resolve_collision_context(
    inventory: GeometryInventory,
    policy: CollisionPolicy,
    context: CollisionContext | None,
    *,
    robot_id: str | None,
    model_id: str | None,
    policy_revision: str | None,
    inventory_revision: str | None,
) -> CollisionContext:
    if context is not None:
        if type(context) is not CollisionContext:
            raise TypeError("context must be CollisionContext")
        try:
            _validate_collision_context(context)
            return context
        except (AttributeError, TypeError, ValueError) as exc:
            # A tampered but previously sealed context can be reconstructed from
            # the owner snapshot; never trust its current fields for authority.
            try:
                snapshot = _sealed_collision_snapshot(context)
                if len(snapshot) != 9:
                    raise ValueError("collision context seal is malformed")
                recovered = CollisionContext(
                    snapshot[0],
                    snapshot[1],
                    snapshot[2],
                    snapshot[3],
                    snapshot[4],
                    snapshot[5],
                    snapshot[6],
                    snapshot[7],
                    snapshot[8],
                )
                return recovered
            except (AttributeError, TypeError, ValueError) as recovery_error:
                if all(
                    value is not None
                    for value in (
                        robot_id,
                        model_id,
                        policy_revision,
                        inventory_revision,
                    )
                ):
                    try:
                        return CollisionContext(
                            robot_id=robot_id,
                            model_id=model_id,
                            policy_id=policy.policy_id,
                            policy_revision=policy_revision,
                            inventory_id=inventory.inventory_id,
                            inventory_revision=inventory_revision,
                            expected_pair_ids=_context_expected_pair_ids(inventory),
                            inventory_fingerprint=_inventory_fingerprint(inventory),
                            policy_fingerprint=_policy_fingerprint(policy),
                        )
                    except (AttributeError, TypeError, ValueError) as explicit_error:
                        raise CollisionContractViolation(
                            "collision context binding is unavailable"
                        ) from explicit_error
                raise CollisionContractViolation(
                    "collision context binding is unavailable"
                ) from recovery_error
    if any(
        value is None
        for value in (robot_id, model_id, policy_revision, inventory_revision)
    ):
        raise TypeError(
            "typed context or explicit collision identity values are required"
        )
    try:
        return CollisionContext(
            robot_id=robot_id,
            model_id=model_id,
            policy_id=policy.policy_id,
            policy_revision=policy_revision,
            inventory_id=inventory.inventory_id,
            inventory_revision=inventory_revision,
            expected_pair_ids=_context_expected_pair_ids(inventory),
            inventory_fingerprint=_inventory_fingerprint(inventory),
            policy_fingerprint=_policy_fingerprint(policy),
        )
    except ValueError:
        if _validate_inventory(inventory) is None:
            if _validate_policy(inventory, policy) is None:
                raise
            return CollisionContext(
                robot_id=robot_id,
                model_id=model_id,
                policy_id=policy.policy_id,
                policy_revision=policy_revision,
                inventory_id=inventory.inventory_id,
                inventory_revision=inventory_revision,
                expected_pair_ids=_context_expected_pair_ids(inventory),
                inventory_fingerprint=_inventory_fingerprint(inventory),
                policy_fingerprint=_policy_fingerprint_without_exclusions(policy),
            )
        return _invalid_inventory_context(
            inventory,
            policy,
            robot_id=robot_id,
            model_id=model_id,
            policy_revision=policy_revision,
            inventory_revision=inventory_revision,
        )


def _safe_pair_mapping(
    inventory: GeometryInventory,
) -> dict[str, CollisionPair]:
    try:
        return {pair.pair_id: pair for pair in inventory.pairs()}
    except (AttributeError, TypeError, ValueError):
        return {}


def _invalid_collision_result(
    context: CollisionContext,
    policy: CollisionPolicy,
    reason_code: str,
    inventory: GeometryInventory | None = None,
) -> CollisionCheckResult:
    clearance_m = context.policy_fingerprint[1]
    near_collision_margin_m = context.policy_fingerprint[2]
    if reason_code not in _INTERNAL_INVALID_REASON_CODES:
        raise ValueError("unknown internal collision invalid reason")
    pair_by_id = {} if inventory is None else _safe_pair_mapping(inventory)
    evaluations: list[CollisionEvaluation] = []
    for pair_id in context.expected_pair_ids:
        try:
            kind = _kind_from_inventory_fingerprint(
                context.inventory_fingerprint,
                pair_id,
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            # 正常なcontextではこの導出はtotalになる。既にinvalidなinventoryを
            # bind済みfingerprintから投影できない場合だけ、利用可能なtyped kindを残す。
            pair = pair_by_id.get(pair_id)
            kind = pair.kind if pair is not None else CollisionKind.SELF_INTERFERENCE
        if kind is CollisionKind.UNKNOWN:
            pair = pair_by_id.get(pair_id)
            kind = pair.kind if pair is not None else CollisionKind.SELF_INTERFERENCE
        evaluations.append(
            CollisionEvaluation(
                pair_id,
                kind,
                CollisionStatus.INVALID,
                None,
                clearance_m,
                reason_code,
                near_collision_margin_m=near_collision_margin_m,
            )
        )
    return CollisionCheckResult(
        context,
        CollisionStatus.INVALID,
        tuple(evaluations),
        reason_code,
    )


def evaluate_collision_configuration(
    inventory: GeometryInventory,
    observations: Iterable[CollisionObservation],
    policy: CollisionPolicy,
    context: CollisionContext | None = None,
    *,
    robot_id: str | None = None,
    model_id: str | None = None,
    policy_revision: str | None = None,
    inventory_revision: str | None = None,
) -> CollisionCheckResult:
    """1 configurationのcollision evidenceを決定的に評価する。"""

    if type(inventory) is not GeometryInventory or type(policy) is not CollisionPolicy:
        raise TypeError("inventory and policy must use typed contracts")
    context_was_invalid = False
    if context is not None:
        try:
            _validate_collision_context(context)
        except (AttributeError, TypeError, ValueError):
            context_was_invalid = True
    context = _resolve_collision_context(
        inventory,
        policy,
        context,
        robot_id=robot_id,
        model_id=model_id,
        policy_revision=policy_revision,
        inventory_revision=inventory_revision,
    )
    if context_was_invalid:
        return _invalid_collision_result(
            context,
            policy,
            "collision_context_binding_invalid",
            inventory,
        )
    invalid_reason = _validate_inventory(inventory)
    if invalid_reason is not None:
        return _invalid_collision_result(context, policy, invalid_reason, inventory)
    invalid_policy = _validate_policy(inventory, policy)
    if invalid_policy is not None:
        return _invalid_collision_result(context, policy, invalid_policy, inventory)
    try:
        inventory_fingerprint = _inventory_fingerprint(inventory)
        policy_fingerprint = _policy_fingerprint(policy)
    except (AttributeError, TypeError, ValueError):
        return _invalid_collision_result(
            context,
            policy,
            "collision_context_binding_invalid",
            inventory,
        )
    if context.inventory_fingerprint != inventory_fingerprint:
        return _invalid_collision_result(
            context,
            policy,
            "collision_context_inventory_binding_mismatch",
            inventory,
        )
    if context.policy_fingerprint != policy_fingerprint:
        return _invalid_collision_result(
            context,
            policy,
            "collision_context_policy_binding_mismatch",
            inventory,
        )
    expected_pair_ids = tuple(pair.pair_id for pair in inventory.pairs())
    if not expected_pair_ids:
        return _invalid_collision_result(
            context,
            policy,
            "collision_pair_inventory_empty",
            inventory,
        )
    if context.policy_id != policy.policy_id:
        return _invalid_collision_result(
            context,
            policy,
            "collision_context_policy_mismatch",
            inventory,
        )
    if context.inventory_id != inventory.inventory_id:
        return _invalid_collision_result(
            context,
            policy,
            "collision_context_inventory_mismatch",
            inventory,
        )
    if context.expected_pair_ids != expected_pair_ids:
        return _invalid_collision_result(
            context,
            policy,
            "collision_context_pair_coverage_mismatch",
            inventory,
        )
    by_pair: dict[str, CollisionObservation] = {}
    try:
        for observation in observations:
            if type(observation) is not CollisionObservation:
                return _invalid_collision_result(
                    context,
                    policy,
                    "invalid_collision_observation",
                    inventory,
                )
            try:
                _validate_collision_observation(observation)
            except (AttributeError, TypeError, ValueError):
                return _invalid_collision_result(
                    context,
                    policy,
                    "invalid_collision_observation",
                    inventory,
                )
            if observation.pair_id in by_pair:
                return _invalid_collision_result(
                    context,
                    policy,
                    "duplicate_collision_observation",
                    inventory,
                )
            by_pair[observation.pair_id] = observation
    except Exception:
        return _invalid_collision_result(
            context,
            policy,
            "observations_not_iterable",
            inventory,
        )

    unexpected_pair_ids = set(by_pair).difference(expected_pair_ids)
    if unexpected_pair_ids:
        return _invalid_collision_result(
            context,
            policy,
            "collision_observation_pair_not_in_inventory",
            inventory,
        )

    evaluations: list[CollisionEvaluation] = []
    for pair in inventory.pairs():
        observation = by_pair.get(pair.pair_id)
        if observation is not None and observation.contact:
            if (
                pair.kind is not CollisionKind.TASK_OBJECT_CONTACT
                or observation.distance_m is None
            ):
                return _invalid_collision_result(
                    context,
                    policy,
                    "invalid_collision_observation",
                    inventory,
                )
        exclusion = policy.exclusion_for(pair.pair_id)
        if exclusion is not None:
            evaluations.append(
                CollisionEvaluation(
                    pair.pair_id,
                    CollisionKind.STRUCTURAL_PROXIMITY,
                    CollisionStatus.CLEAR,
                    None,
                    policy.clearance_m,
                    "explicit_structural_exclusion",
                    exclusion.evidence_reference,
                    policy.near_collision_margin_m,
                )
            )
            continue
        if observation is None:
            evaluations.append(
                CollisionEvaluation(
                    pair.pair_id,
                    pair.kind,
                    CollisionStatus.UNAVAILABLE,
                    None,
                    policy.clearance_m,
                    "collision_observation_unavailable",
                    near_collision_margin_m=policy.near_collision_margin_m,
                )
            )
            continue
        if pair.kind is CollisionKind.UNKNOWN:
            evaluations.append(
                CollisionEvaluation(
                    pair.pair_id,
                    pair.kind,
                    CollisionStatus.INVALID,
                    observation.distance_m,
                    policy.clearance_m,
                    "unknown_collision_pair_role",
                    observation.source_id,
                    policy.near_collision_margin_m,
                )
            )
            continue
        distance = observation.distance_m
        if distance is None:
            evaluations.append(
                CollisionEvaluation(
                    pair.pair_id,
                    pair.kind,
                    CollisionStatus.UNKNOWN,
                    None,
                    policy.clearance_m,
                    "collision_distance_unknown",
                    observation.source_id,
                    policy.near_collision_margin_m,
                )
            )
            continue
        if distance < 0.0:
            status = CollisionStatus.COLLISION
            reason = _PENETRATION_REASON_BY_KIND[pair.kind]
        elif observation.contact:
            status = CollisionStatus.CONTACT
            reason = "task_object_contact"
        elif distance <= policy.clearance_m:
            status = CollisionStatus.NEAR_COLLISION
            reason = "near_collision_clearance"
        elif distance <= policy.clearance_m + policy.near_collision_margin_m:
            status = CollisionStatus.NEAR_COLLISION
            reason = "near_collision_clearance"
        else:
            status = CollisionStatus.CLEAR
            reason = "pair_clear"
        evaluations.append(
            CollisionEvaluation(
                pair.pair_id,
                pair.kind,
                status,
                distance,
                policy.clearance_m,
                reason,
                observation.source_id,
                policy.near_collision_margin_m,
            )
        )
    status, reason = _derive_collision_status_reason(context, tuple(evaluations))
    return CollisionCheckResult(context, status, tuple(evaluations), reason)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class BoundedCollisionTrajectoryResult:
    """bounded trajectory各sampleの結果。"""

    status: CollisionStatus
    sample_results: tuple[CollisionCheckResult, ...]
    sample_indices: tuple[int, ...]
    failed_sample_index: int | None
    _canonical_snapshot: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _exact_type(self, BoundedCollisionTrajectoryResult, "trajectory result")
        if type(self.status) is not CollisionStatus:
            object.__setattr__(self, "status", CollisionStatus(self.status))
        if type(self.sample_results) is not tuple:
            raise TypeError("sample_results must be a tuple")
        if not self.sample_results:
            raise ValueError("sample_results must be non-empty")
        if not all(type(item) is CollisionCheckResult for item in self.sample_results):
            raise TypeError("sample_results must contain CollisionCheckResult values")
        for item in self.sample_results:
            try:
                _validate_collision_context(item.context)
                _validate_collision_check_result(item)
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError("trajectory sample result is invalid") from exc
        if type(self.sample_indices) is not tuple:
            raise TypeError("sample_indices must be a tuple")
        if any(
            isinstance(index, bool) or not isinstance(index, int)
            for index in self.sample_indices
        ):
            raise TypeError("sample_indices must contain integer values")
        expected_sample_indices = tuple(range(len(self.sample_results)))
        if self.sample_indices != expected_sample_indices:
            raise ValueError(
                "sample_indices must exactly match sample_results order and length"
            )
        first_context = self.sample_results[0].context
        if any(item.context is not first_context for item in self.sample_results[1:]):
            raise ValueError(
                "trajectory samples must share identical collision context binding"
            )
        if self.failed_sample_index is not None and (
            isinstance(self.failed_sample_index, bool)
            or not isinstance(self.failed_sample_index, int)
            or self.failed_sample_index < 0
        ):
            raise ValueError("failed_sample_index must be a non-negative integer or None")
        first_non_clear = next(
            (
                index
                for index, result in enumerate(self.sample_results)
                if result.status is not CollisionStatus.CLEAR
            ),
            None,
        )
        if first_non_clear is None:
            if self.status is not CollisionStatus.CLEAR:
                raise ValueError(
                    "trajectory aggregate status must be CLEAR when every sample is CLEAR"
                )
            if self.failed_sample_index is not None:
                raise ValueError("CLEAR trajectory must not have a failed sample index")
            snapshot = _bounded_collision_trajectory_snapshot(self)
            object.__setattr__(self, "_canonical_snapshot", snapshot)
            _register_collision_seal(self, snapshot)
            return
        if self.failed_sample_index != self.sample_indices[first_non_clear]:
            raise ValueError(
                "failed_sample_index must identify the first non-clear sample"
            )
        if len(self.sample_results) != first_non_clear + 1:
            raise ValueError("trajectory samples must stop at the first non-clear sample")
        if self.status is CollisionStatus.CLEAR:
            raise ValueError("trajectory aggregate status cannot be synthetic CLEAR")
        if self.status is not self.sample_results[first_non_clear].status:
            raise ValueError(
                "trajectory aggregate status must match the first non-clear sample"
            )
        snapshot = _bounded_collision_trajectory_snapshot(self)
        object.__setattr__(self, "_canonical_snapshot", snapshot)
        _register_collision_seal(self, snapshot)

    @property
    def clear(self) -> bool:
        try:
            _validate_bounded_collision_trajectory_result(self)
        except Exception:
            return False
        return self.status is CollisionStatus.CLEAR

    @property
    def context(self) -> CollisionContext:
        try:
            _validate_bounded_collision_trajectory_result(self)
        except Exception as exc:
            raise ValueError("trajectory result binding is invalid") from exc
        return self.sample_results[0].context


def _validate_bounded_collision_trajectory_result(
    result: BoundedCollisionTrajectoryResult,
) -> None:
    """trajectoryとnested configuration resultをpublic accessでも再検証する。"""

    _exact_type(result, BoundedCollisionTrajectoryResult, "trajectory result")
    if type(result.status) is not CollisionStatus:
        raise TypeError("trajectory status is invalid")
    if type(result.sample_results) is not tuple:
        raise TypeError("sample_results must be a tuple")
    if not result.sample_results:
        raise ValueError("sample_results must be non-empty")
    if not all(type(item) is CollisionCheckResult for item in result.sample_results):
        raise TypeError("sample_results must contain CollisionCheckResult values")
    for item in result.sample_results:
        _validate_collision_check_result(item)
    if type(result.sample_indices) is not tuple:
        raise TypeError("sample_indices must be a tuple")
    if any(
        isinstance(index, bool) or not isinstance(index, int)
        for index in result.sample_indices
    ):
        raise TypeError("sample_indices must contain integer values")
    expected_sample_indices = tuple(range(len(result.sample_results)))
    if result.sample_indices != expected_sample_indices:
        raise ValueError(
            "sample_indices must exactly match sample_results order and length"
        )
    first_context = result.sample_results[0].context
    if any(item.context is not first_context for item in result.sample_results[1:]):
        raise ValueError(
            "trajectory samples must share identical collision context binding"
        )
    if result.failed_sample_index is not None and (
        isinstance(result.failed_sample_index, bool)
        or not isinstance(result.failed_sample_index, int)
        or result.failed_sample_index < 0
    ):
        raise ValueError("failed_sample_index must be a non-negative integer or None")
    first_non_clear = next(
        (
            index
            for index, sample in enumerate(result.sample_results)
            if sample.status is not CollisionStatus.CLEAR
        ),
        None,
    )
    if first_non_clear is None:
        if result.status is not CollisionStatus.CLEAR:
            raise ValueError(
                "trajectory aggregate status must be CLEAR when every sample is CLEAR"
            )
        if result.failed_sample_index is not None:
            raise ValueError("CLEAR trajectory must not have a failed sample index")
        _validate_collision_seal(
            result,
            _bounded_collision_trajectory_snapshot(result),
        )
        return
    if result.failed_sample_index != result.sample_indices[first_non_clear]:
        raise ValueError(
            "failed_sample_index must identify the first non-clear sample"
        )
    if len(result.sample_results) != first_non_clear + 1:
        raise ValueError("trajectory samples must stop at the first non-clear sample")
    if result.status is CollisionStatus.CLEAR:
        raise ValueError("trajectory aggregate status cannot be synthetic CLEAR")
    if result.status is not result.sample_results[first_non_clear].status:
        raise ValueError(
            "trajectory aggregate status must match the first non-clear sample"
        )
    _validate_collision_seal(
        result,
        _bounded_collision_trajectory_snapshot(result),
    )


def validate_collision_context(context: CollisionContext) -> CollisionContext:
    """Public canonical revalidation route for a collision context."""

    _validate_collision_context(context)
    return context


def validate_collision_evaluation(
    evaluation: CollisionEvaluation,
) -> CollisionEvaluation:
    """Public canonical revalidation route for one pair evaluation."""

    inconsistency = _collision_evaluation_inconsistency(evaluation)
    if inconsistency is not None:
        raise ValueError(inconsistency)
    return evaluation


def validate_collision_check_result(
    result: CollisionCheckResult,
) -> CollisionCheckResult:
    """Public canonical revalidation route for an aggregate collision result."""

    _validate_collision_check_result(result)
    return result


def validate_bounded_collision_trajectory_result(
    result: BoundedCollisionTrajectoryResult,
) -> BoundedCollisionTrajectoryResult:
    """Public canonical revalidation route for a bounded collision trajectory."""

    _validate_bounded_collision_trajectory_result(result)
    return result


def evaluate_bounded_collision_trajectory(
    inventory: GeometryInventory,
    trajectory_observations: Sequence[Iterable[CollisionObservation]],
    policy: CollisionPolicy,
    context: CollisionContext | None = None,
    *,
    robot_id: str | None = None,
    model_id: str | None = None,
    policy_revision: str | None = None,
    inventory_revision: str | None = None,
) -> BoundedCollisionTrajectoryResult:
    """有限sampleを順序通り検査し、first failureを保持する。"""

    if type(inventory) is not GeometryInventory or type(policy) is not CollisionPolicy:
        raise TypeError("inventory and policy must use typed contracts")
    context_was_invalid = False
    if context is not None:
        try:
            _validate_collision_context(context)
        except (AttributeError, TypeError, ValueError):
            context_was_invalid = True
    context = _resolve_collision_context(
        inventory,
        policy,
        context,
        robot_id=robot_id,
        model_id=model_id,
        policy_revision=policy_revision,
        inventory_revision=inventory_revision,
    )
    if context_was_invalid:
        invalid = _invalid_collision_result(
            context,
            policy,
            "collision_context_binding_invalid",
            inventory,
        )
        return BoundedCollisionTrajectoryResult(
            CollisionStatus.INVALID,
            (invalid,),
            (0,),
            0,
        )
    if not isinstance(trajectory_observations, Sequence) or not trajectory_observations:
        invalid = _invalid_collision_result(
            context,
            policy,
            "trajectory_observations_must_be_non_empty",
            inventory,
        )
        return BoundedCollisionTrajectoryResult(
            CollisionStatus.INVALID,
            (invalid,),
            (0,),
            0,
        )
    results: list[CollisionCheckResult] = []
    for index, observations in enumerate(trajectory_observations):
        result = evaluate_collision_configuration(
            inventory,
            observations,
            policy,
            context,
        )
        results.append(result)
        if result.status is not CollisionStatus.CLEAR:
            return BoundedCollisionTrajectoryResult(
                result.status,
                tuple(results),
                tuple(range(len(results))),
                index,
            )
    return BoundedCollisionTrajectoryResult(
        CollisionStatus.CLEAR,
        tuple(results),
        tuple(range(len(results))),
        None,
    )


def build_mujoco_geometry_inventory(
    model: object,
    *,
    robot_body_names: Sequence[str],
    environment_body_names: Sequence[str] = (),
    task_object_body_names: Sequence[str] = (),
    tool_body_names: Sequence[str] = (),
) -> GeometryInventory:
    """MuJoCo modelからexplicit body-role inventoryを作る。"""

    if model is None:
        raise ValueError("MuJoCo model is required")
    robot = set(robot_body_names)
    environment = set(environment_body_names)
    task = set(task_object_body_names)
    tool = set(tool_body_names)
    if not robot:
        raise ValueError("robot_body_names must not be empty")
    role_sets = (robot, environment, task, tool)
    if any(
        left.intersection(right)
        for index, left in enumerate(role_sets)
        for right in role_sets[index + 1 :]
    ):
        raise ValueError("body role sets must be disjoint")
    try:
        import mujoco

        ngeom = _mujoco_index("MuJoCo model.ngeom", model.ngeom)
        body_count: int | None = None
        if hasattr(model, "nbody"):
            body_count = _mujoco_index("MuJoCo model.nbody", model.nbody)
        elif hasattr(model, "body_names"):
            body_count = len(model.body_names)
        geometries: list[GeometryIdentity] = []
        for geom_id in range(ngeom):
            geom_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id)
            body_id = _mujoco_index(
                "MuJoCo geom body index",
                model.geom_bodyid[geom_id],
                upper_bound=body_count,
            )
            body_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
            if not geom_name or not body_name:
                raise ValueError("MuJoCo geom/body name is missing")
            if body_name in tool:
                role = GeometryRole.TOOL
            elif body_name in robot:
                role = GeometryRole.ROBOT
            elif body_name in environment:
                role = GeometryRole.ENVIRONMENT
            elif body_name in task:
                role = GeometryRole.TASK_OBJECT
            else:
                role = GeometryRole.UNKNOWN
            geometries.append(GeometryIdentity(geom_name, body_name, role))
    except Exception as exc:
        # MuJoCoのmodel accessor / library callは、実装やproviderによって
        # RuntimeError等の通常例外を返し得る。ここは明示的なadapter境界
        # なので、Exceptionだけをtyped ValueErrorへ正規化する。KeyboardInterrupt
        # 等のBaseExceptionはプロセス制御として伝播させる。
        raise ValueError(f"MuJoCo geometry inventory failed: {exc}") from exc
    return GeometryInventory(tuple(geometries))


def evaluate_mujoco_collision_configuration(
    model: object,
    data: object,
    inventory: GeometryInventory,
    policy: CollisionPolicy,
    context: CollisionContext,
    *,
    joint_route: EvaluatedJointRoute,
) -> CollisionCheckResult:
    """同じMuJoCo stateでforward・観測・既存P3評価を行い、候補を保持する。"""
    import mujoco

    if not isinstance(model, mujoco.MjModel) or not isinstance(data, mujoco.MjData):
        raise TypeError("candidate observations require MuJoCo model/data")
    if type(joint_route) is not EvaluatedJointRoute:
        raise TypeError("candidate requires a runtime joint route")
    joint_names = joint_route.joint_names
    joint_ids = tuple(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        for name in joint_names
    )
    if any(index < 0 or model.jnt_type[index] != mujoco.mjtJoint.mjJNT_HINGE for index in joint_ids):
        raise ValueError("candidate requires named scalar angular joints")
    mujoco.mj_forward(model, data)
    candidate = EvaluatedCandidate(
        joint_names,
        ((
            tuple(_finite("candidate qpos", float(data.qpos[model.jnt_qposadr[index]])) for index in joint_ids),
            tuple(_finite("candidate qvel", float(data.qvel[model.jnt_dofadr[index]])) for index in joint_ids),
        ),),
        joint_route=joint_route,
    )
    observations = read_mujoco_contact_observations(model, data, inventory)
    result = evaluate_collision_configuration(inventory, observations, policy, context)
    _register_collision_candidate(result, candidate)
    return result


def read_mujoco_contact_observations(
    model: object,
    data: object,
    inventory: GeometryInventory,
) -> tuple[CollisionObservation, ...]:
    """現在のMuJoCo contact listをrole-awareなdistance evidenceへ投影する。"""

    if model is None or data is None:
        raise ValueError("MuJoCo model and data are required")
    if type(inventory) is not GeometryInventory:
        raise TypeError("inventory must use the typed GeometryInventory contract")
    try:
        _validate_geometry_inventory(inventory)
        if any(
            geometry.role is GeometryRole.UNKNOWN
            for geometry in inventory.geometries
        ):
            raise ValueError("MuJoCo contact inventory contains an unknown geometry role")
        pair_by_id = {pair.pair_id: pair for pair in inventory.pairs()}
        contacts_by_pair: dict[str, float] = {}
        import mujoco

        ngeom = _mujoco_index("MuJoCo model.ngeom", model.ngeom)
        ncon = _mujoco_index("MuJoCo data.ncon", data.ncon)
        for index in range(ncon):
            contact = data.contact[index]
            first_id = _mujoco_index(
                "MuJoCo contact geom1 index",
                contact.geom1,
                upper_bound=ngeom,
            )
            second_id = _mujoco_index(
                "MuJoCo contact geom2 index",
                contact.geom2,
                upper_bound=ngeom,
            )
            first = mujoco.mj_id2name(
                model,
                mujoco.mjtObj.mjOBJ_GEOM,
                first_id,
            )
            second = mujoco.mj_id2name(
                model,
                mujoco.mjtObj.mjOBJ_GEOM,
                second_id,
            )
            if not first or not second:
                raise ValueError("MuJoCo contact references unknown geom")
            pair_id = "|".join(sorted((first, second)))
            if pair_id not in pair_by_id:
                raise ValueError(
                    "MuJoCo contact pair is not represented by the geometry inventory"
                )
            distance = _finite("contact distance", contact.dist)
            previous = contacts_by_pair.get(pair_id)
            if previous is None or distance < previous:
                contacts_by_pair[pair_id] = distance
        return tuple(
            CollisionObservation(
                pair_id,
                distance,
                "mujoco-contact",
                contact=pair_by_id[pair_id].kind is CollisionKind.TASK_OBJECT_CONTACT,
            )
            for pair_id, distance in sorted(contacts_by_pair.items())
        )
    except Exception as exc:
        # contact count/index/arrayとmj_id2nameを同じ明示的adapter境界で
        # fail-closedにする。通常のExceptionだけを捕捉し、BaseExceptionは
        # 捕捉しない。
        raise ValueError(f"MuJoCo contact observation failed: {exc}") from exc


__all__ = [
    "BoundedCollisionTrajectoryResult",
    "CollisionContractViolation",
    "CollisionContext",
    "CollisionCheckResult",
    "CollisionEvaluation",
    "CollisionExclusion",
    "CollisionKind",
    "CollisionObservation",
    "CollisionPair",
    "CollisionPolicy",
    "CollisionStatus",
    "GeometryIdentity",
    "GeometryInventory",
    "GeometryRole",
    "build_mujoco_geometry_inventory",
    "evaluate_bounded_collision_trajectory",
    "evaluate_collision_configuration",
    "evaluate_mujoco_collision_configuration",
    "read_mujoco_contact_observations",
    "validate_bounded_collision_trajectory_result",
    "validate_collision_check_result",
    "validate_collision_context",
    "validate_collision_evaluation",
]
