"""MuJoCo measured contact evidence owned by the contact runtime.

The extractor reads the compiled MuJoCo model/data directly.  mjData.contact
is the physical contact source of truth and mj_contactForce is the only force
extraction path.  Viewer geometry, force filtering, reaction-force estimation,
and task outcome logic are intentionally outside this module.
"""

from __future__ import annotations

from xpotato_sim.mujoco_backend.contact_geometry import read_contact_geometry
from xpotato_sim.mujoco_backend.contact_wrench import read_contact_wrench

import json
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from xpotato_sim.runtime.composition.robot_bundle import CONTACT_EVIDENCE_V1
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidence,
    EvidenceStatus,
    VersionedIdentity,
)
from xpotato_sim.runtime.contact.manifest import (
    CONTACT_OBJECT_IDENTITY,
    ContactTaskManifest,
    contact_manifest_digest,
)
from xpotato_sim.runtime.contact.scene import CONTACT_SCENE_IDENTITY


CONTACT_EVIDENCE_SCHEMA_VERSION: Final[str] = "contact-evidence/v1"
CONTACT_EVIDENCE_PROVENANCE: Final[str] = "mujoco_contact_measurement/v1"
CONTACT_FORCE_UNIT: Final[str] = "newton"
CONTACT_TORQUE_UNIT: Final[str] = "newton_meter"
CONTACT_DISTANCE_UNIT: Final[str] = "meter"
CONTACT_POINT_FRAME: Final[str] = "mujoco_world"
CONTACT_FRAME_NAME: Final[str] = "mujoco_contact"
CONTACT_EVIDENCE_IDENTITY: Final[VersionedIdentity] = CONTACT_EVIDENCE_V1
_MANIFEST_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"sha256:[0-9a-f]{64}\Z"
)
_ZERO3: Final[tuple[float, float, float]] = (0.0, 0.0, 0.0)
_ZERO6: Final[tuple[float, float, float, float, float, float]] = (
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
)
_FRAME_TOLERANCE: Final[float] = 1e-8
_DERIVED_VALUE_REL_TOLERANCE: Final[float] = 1e-8
_DERIVED_VALUE_ABS_TOLERANCE: Final[float] = 1e-9


class ContactEvidenceStatus(str, Enum):
    """Raw contact measurement state.

    NO_CONTACT is a valid measured absence of target contact.  The other
    non-measured states never carry a synthetic zero force.
    """

    NO_CONTACT = "no_contact"
    MEASURED = "measured"
    MEASUREMENT_UNAVAILABLE = "measurement_unavailable"
    INVALID_CONTACT = "invalid_contact"
    SOLVER_INVALID = "solver_invalid"


class ContactPairClassification(str, Enum):
    """Boundary classification for one compiled MuJoCo contact pair."""

    TARGET_OBJECT = "target_object"
    SELF_CONTACT = "self_contact"
    ENVIRONMENT_CONTACT = "environment_contact"
    OTHER_OBJECT = "other_object"
    UNCLASSIFIED = "unclassified"


ContactMeasurementStatus = ContactEvidenceStatus
ContactClassification = ContactPairClassification


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return 0.0 if result == 0.0 else result


def _vector(name: str, value: object, *, length: int) -> tuple[float, ...]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not hasattr(value, "__len__")
        or not hasattr(value, "__getitem__")
    ):
        raise ValueError(f"{name} must contain exactly {length} finite numbers")
    if len(value) != length:
        raise ValueError(f"{name} must contain exactly {length} finite numbers")
    return tuple(_finite(f"{name}[{index}]", item) for index, item in enumerate(value))


def _vector3(name: str, value: object) -> tuple[float, float, float]:
    return _vector(name, value, length=3)  # type: ignore[return-value]


def _vector6(name: str, value: object) -> tuple[float, float, float, float, float, float]:
    return _vector(name, value, length=6)  # type: ignore[return-value]


def _magnitude(value: Sequence[float]) -> float:
    return math.sqrt(math.fsum(component * component for component in value))


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


def _sub(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right, strict=True))  # type: ignore[return-value]


def _scale(value: Sequence[float], factor: float) -> tuple[float, float, float]:
    return tuple(factor * component for component in value)  # type: ignore[return-value]


def _add_vectors(values: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    if not values:
        return _ZERO3
    return tuple(
        math.fsum(value[index] for value in values)
        for index in range(3)
    )  # type: ignore[return-value]


def _close_scalar(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=_DERIVED_VALUE_REL_TOLERANCE,
        abs_tol=_DERIVED_VALUE_ABS_TOLERANCE,
    )


def _close_vector(left: Sequence[float], right: Sequence[float]) -> bool:
    return len(left) == len(right) and all(
        _close_scalar(float(a), float(b)) for a, b in zip(left, right, strict=True)
    )


def _determinant3(rows: Sequence[Sequence[float]]) -> float:
    return (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )


def _validate_contact_frame(
    name: str,
    value: object,
    *,
    normal: Sequence[float],
) -> tuple[float, ...]:
    frame = _vector(name, value, length=9)
    rows = (frame[0:3], frame[3:6], frame[6:9])
    for index, row in enumerate(rows):
        if abs(_magnitude(row) - 1.0) > _FRAME_TOLERANCE:
            raise ValueError(f"{name} row {index} must be unit length")
    for left_index in range(3):
        for right_index in range(left_index + 1, 3):
            if abs(_dot(rows[left_index], rows[right_index])) > _FRAME_TOLERANCE:
                raise ValueError(f"{name} rows must be pairwise orthogonal")
    if abs(abs(_determinant3(rows)) - 1.0) > _FRAME_TOLERANCE:
        raise ValueError(f"{name} must be an orthonormal frame")
    if not _close_vector(rows[0], normal):
        raise ValueError(f"{name} normal row must match normal_world")
    return frame


def _contact_frame_to_world(
    frame: Sequence[float],
    local: Sequence[float],
) -> tuple[float, float, float]:
    """Transform contact-frame coordinates using MuJoCo's row-major frame."""

    rows = (frame[0:3], frame[3:6], frame[6:9])
    return tuple(
        math.fsum(rows[axis][component] * local[axis] for axis in range(3))
        for component in range(3)
    )  # type: ignore[return-value]


def _identity_document(identity: VersionedIdentity) -> dict[str, object]:
    return {"name": identity.name, "version": identity.version}


class ContactEvidenceError(ValueError):
    """Contact evidence input or MuJoCo measurement failure."""


@dataclass(frozen=True, slots=True)
class ContactForceAggregate:
    """Deterministically summed target-contact force/wrench values."""

    contact_count: int
    normal_force_n: float
    tangential_force_world_n: tuple[float, float, float]
    resultant_force_world_n: tuple[float, float, float]
    resultant_force_n: float
    object_on_tool_force_world_n: tuple[float, float, float]
    tool_on_object_force_world_n: tuple[float, float, float]
    object_on_tool_wrench_world_nm: tuple[float, float, float, float, float, float]

    def __post_init__(self) -> None:
        if type(self.contact_count) is not int or self.contact_count < 0:
            raise ValueError("contact aggregate contact_count must be non-negative")
        normal = _finite("aggregate.normal_force_n", self.normal_force_n)
        tangential = _vector3(
            "aggregate.tangential_force_world_n",
            self.tangential_force_world_n,
        )
        resultant = _vector3(
            "aggregate.resultant_force_world_n",
            self.resultant_force_world_n,
        )
        object_force = _vector3(
            "aggregate.object_on_tool_force_world_n",
            self.object_on_tool_force_world_n,
        )
        tool_force = _vector3(
            "aggregate.tool_on_object_force_world_n",
            self.tool_on_object_force_world_n,
        )
        wrench = _vector6(
            "aggregate.object_on_tool_wrench_world_nm",
            self.object_on_tool_wrench_world_nm,
        )
        magnitude = _finite("aggregate.resultant_force_n", self.resultant_force_n)
        if normal < 0.0:
            raise ValueError("aggregate.normal_force_n must be non-negative")
        if magnitude < 0.0:
            raise ValueError("aggregate.resultant_force_n must be non-negative")
        if not _close_vector(resultant, object_force):
            raise ValueError(
                "aggregate resultant_force_world_n must match object_on_tool_force_world_n"
            )
        if not _close_vector(tool_force, _scale(object_force, -1.0)):
            raise ValueError(
                "aggregate tool_on_object_force_world_n must oppose object_on_tool_force_world_n"
            )
        if not _close_scalar(magnitude, _magnitude(resultant)):
            raise ValueError(
                "aggregate resultant_force_n must match resultant force magnitude"
            )
        if self.contact_count == 0 and any(
            component != 0.0
            for component in (
                normal,
                *tangential,
                *resultant,
                magnitude,
                *object_force,
                *tool_force,
                *wrench,
            )
        ):
            raise ValueError("empty contact aggregate must contain only zero values")
        object.__setattr__(self, "normal_force_n", normal)
        object.__setattr__(self, "tangential_force_world_n", tangential)
        object.__setattr__(self, "resultant_force_world_n", resultant)
        object.__setattr__(self, "resultant_force_n", magnitude)
        object.__setattr__(self, "object_on_tool_force_world_n", object_force)
        object.__setattr__(self, "tool_on_object_force_world_n", tool_force)
        object.__setattr__(self, "object_on_tool_wrench_world_nm", wrench)

    @classmethod
    def no_contact(cls) -> "ContactForceAggregate":
        return cls(
            contact_count=0,
            normal_force_n=0.0,
            tangential_force_world_n=_ZERO3,
            resultant_force_world_n=_ZERO3,
            resultant_force_n=0.0,
            object_on_tool_force_world_n=_ZERO3,
            tool_on_object_force_world_n=_ZERO3,
            object_on_tool_wrench_world_nm=_ZERO6,
        )

    def to_document(self) -> dict[str, object]:
        return {
            "contact_count": self.contact_count,
            "normal_force_n": self.normal_force_n,
            "tangential_force_world_n": list(self.tangential_force_world_n),
            "resultant_force_world_n": list(self.resultant_force_world_n),
            "resultant_force_n": self.resultant_force_n,
            "object_on_tool_force_world_n": list(self.object_on_tool_force_world_n),
            "tool_on_object_force_world_n": list(self.tool_on_object_force_world_n),
            "object_on_tool_wrench_world_nm": list(
                self.object_on_tool_wrench_world_nm
            ),
        }


@dataclass(frozen=True, slots=True)
class ContactRecord:
    """One MuJoCo contact with raw geometry and official measured force."""

    contact_identity: str
    classification: ContactPairClassification
    geom1_id: int
    geom2_id: int
    geom1_name: str
    geom2_name: str
    body1_id: int
    body2_id: int
    body1_name: str
    body2_name: str
    point_world_m: tuple[float, float, float]
    normal_world: tuple[float, float, float]
    distance_m: float
    penetration_m: float
    contact_frame_world: tuple[float, ...]
    force_contact_frame_n: tuple[float, float, float] | None
    force_world_n: tuple[float, float, float] | None
    torque_contact_frame_nm: tuple[float, float, float] | None
    torque_world_nm: tuple[float, float, float] | None
    object_on_tool_force_world_n: tuple[float, float, float] | None
    tool_on_object_force_world_n: tuple[float, float, float] | None
    normal_force_n: float | None
    tangential_force_world_n: tuple[float, float, float] | None
    resultant_force_n: float | None
    force_status: ContactEvidenceStatus

    def __post_init__(self) -> None:
        if not isinstance(self.contact_identity, str) or not self.contact_identity:
            raise ValueError("contact identity must be a non-empty string")
        if not isinstance(self.classification, ContactPairClassification):
            raise TypeError("contact classification must be typed")
        if not isinstance(self.force_status, ContactEvidenceStatus):
            raise TypeError("contact force status must be typed")
        for name, value in (
            ("geom1_id", self.geom1_id),
            ("geom2_id", self.geom2_id),
            ("body1_id", self.body1_id),
            ("body2_id", self.body2_id),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name, value in (
            ("geom1_name", self.geom1_name),
            ("geom2_name", self.geom2_name),
            ("body1_name", self.body1_name),
            ("body2_name", self.body2_name),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        point = _vector3("contact.point_world_m", self.point_world_m)
        normal = _vector3("contact.normal_world", self.normal_world)
        if abs(_magnitude(normal) - 1.0) > 1e-9:
            raise ValueError("contact normal must be unit length")
        distance = _finite("contact.distance_m", self.distance_m)
        penetration = _finite("contact.penetration_m", self.penetration_m)
        if penetration < 0.0:
            raise ValueError("contact penetration must be non-negative")
        if not _close_scalar(penetration, max(0.0, -distance)):
            raise ValueError("contact penetration must match signed distance")
        frame = _validate_contact_frame(
            "contact.contact_frame_world",
            self.contact_frame_world,
            normal=normal,
        )
        if self.force_contact_frame_n is None:
            if self.force_world_n is not None:
                raise ValueError("missing local force cannot carry world force")
        else:
            local_force = _vector3(
                "contact.force_contact_frame_n",
                self.force_contact_frame_n,
            )
            world_force = (
                None
                if self.force_world_n is None
                else _vector3("contact.force_world_n", self.force_world_n)
            )
            if world_force is None:
                raise ValueError("measured local force requires world force")
            expected_world_force = _contact_frame_to_world(frame, local_force)
            if not _close_vector(world_force, expected_world_force):
                raise ValueError(
                    "contact world force must match contact-frame force transform"
                )
            object.__setattr__(self, "force_contact_frame_n", local_force)
            object.__setattr__(self, "force_world_n", world_force)
        if self.torque_contact_frame_nm is None:
            if self.torque_world_nm is not None:
                raise ValueError("missing local torque cannot carry world torque")
        else:
            local_torque = _vector3(
                "contact.torque_contact_frame_nm",
                self.torque_contact_frame_nm,
            )
            if self.torque_world_nm is None:
                raise ValueError("measured local torque requires world torque")
            world_torque = _vector3(
                "contact.torque_world_nm",
                self.torque_world_nm,
            )
            expected_world_torque = _contact_frame_to_world(frame, local_torque)
            if not _close_vector(world_torque, expected_world_torque):
                raise ValueError(
                    "contact world torque must match contact-frame torque transform"
                )
            object.__setattr__(self, "torque_contact_frame_nm", local_torque)
            object.__setattr__(self, "torque_world_nm", world_torque)
        object_force = (
            None
            if self.object_on_tool_force_world_n is None
            else _vector3(
                "contact.object_on_tool_force_world_n",
                self.object_on_tool_force_world_n,
            )
        )
        tool_force = (
            None
            if self.tool_on_object_force_world_n is None
            else _vector3(
                "contact.tool_on_object_force_world_n",
                self.tool_on_object_force_world_n,
            )
        )
        tangential_force = (
            None
            if self.tangential_force_world_n is None
            else _vector3(
                "contact.tangential_force_world_n",
                self.tangential_force_world_n,
            )
        )
        normal_force = (
            None
            if self.normal_force_n is None
            else _finite("contact.normal_force_n", self.normal_force_n)
        )
        resultant = (
            None
            if self.resultant_force_n is None
            else _finite("contact.resultant_force_n", self.resultant_force_n)
        )
        if normal_force is not None and normal_force < 0.0:
            raise ValueError("contact normal force must be non-negative")
        if resultant is not None and resultant < 0.0:
            raise ValueError("contact resultant force must be non-negative")
        if self.force_status is ContactEvidenceStatus.NO_CONTACT:
            raise ValueError("contact record cannot use no_contact force status")
        force_values = (
            self.force_contact_frame_n,
            self.force_world_n,
            self.torque_contact_frame_nm,
            self.torque_world_nm,
            object_force,
            tool_force,
            normal_force,
            tangential_force,
            resultant,
        )
        if self.force_status is not ContactEvidenceStatus.MEASURED and any(
            value is not None
            for value in force_values
        ):
            raise ValueError("non-measured force status must not carry force")
        if self.force_status is ContactEvidenceStatus.MEASURED:
            if self.force_contact_frame_n is None or self.force_world_n is None:
                raise ValueError("measured contact requires local and world force")
            if self.torque_contact_frame_nm is None or self.torque_world_nm is None:
                raise ValueError("measured contact requires local and world torque")
            if self.classification is ContactPairClassification.TARGET_OBJECT:
                if (
                    object_force is None
                    or tool_force is None
                    or normal_force is None
                    or tangential_force is None
                    or resultant is None
                ):
                    raise ValueError(
                        "measured target contact requires normalized force fields"
                    )
                if not _close_vector(tool_force, _scale(object_force, -1.0)):
                    raise ValueError(
                        "tool_on_object force must oppose object_on_tool force"
                    )
                if not (
                    _close_vector(object_force, self.force_world_n)
                    or _close_vector(object_force, _scale(self.force_world_n, -1.0))
                ):
                    raise ValueError(
                        "object_on_tool force must preserve MuJoCo force sign"
                    )
                if not _close_scalar(resultant, _magnitude(object_force)):
                    raise ValueError(
                        "resultant force must match object_on_tool force magnitude"
                    )
                if not _close_scalar(
                    normal_force,
                    abs(_dot(object_force, normal)),
                ):
                    raise ValueError(
                        "normal force must match object_on_tool normal component"
                    )
                if abs(_dot(tangential_force, normal)) > _FRAME_TOLERANCE * max(
                    1.0,
                    _magnitude(tangential_force),
                ):
                    raise ValueError("tangential force must be normal-orthogonal")
                if not _close_scalar(
                    _magnitude(tangential_force) ** 2,
                    max(0.0, resultant * resultant - normal_force * normal_force),
                ):
                    raise ValueError(
                        "tangential force must match normal/resultant decomposition"
                    )
            elif any(
                value is not None
                for value in (
                    object_force,
                    tool_force,
                    normal_force,
                    tangential_force,
                    resultant,
                )
            ):
                raise ValueError(
                    "non-target measured contact must not carry target force fields"
                )
        object.__setattr__(self, "point_world_m", point)
        object.__setattr__(self, "normal_world", normal)
        object.__setattr__(self, "distance_m", distance)
        object.__setattr__(self, "penetration_m", penetration)
        object.__setattr__(self, "contact_frame_world", frame)
        object.__setattr__(self, "object_on_tool_force_world_n", object_force)
        object.__setattr__(self, "tool_on_object_force_world_n", tool_force)
        object.__setattr__(self, "tangential_force_world_n", tangential_force)
        object.__setattr__(self, "normal_force_n", normal_force)
        object.__setattr__(self, "resultant_force_n", resultant)

    @property
    def point_world_frame_m(self) -> tuple[float, float, float]:
        return self.point_world_m

    @property
    def penetration_depth_m(self) -> float:
        return self.penetration_m

    @property
    def contact_normal_world(self) -> tuple[float, float, float]:
        return self.normal_world

    def to_document(self) -> dict[str, object]:
        def values(value: Sequence[float] | None) -> list[float] | None:
            return None if value is None else list(value)

        return {
            "contact_identity": self.contact_identity,
            "classification": self.classification.value,
            "geom1_id": self.geom1_id,
            "geom2_id": self.geom2_id,
            "geom1_name": self.geom1_name,
            "geom2_name": self.geom2_name,
            "body1_id": self.body1_id,
            "body2_id": self.body2_id,
            "body1_name": self.body1_name,
            "body2_name": self.body2_name,
            "point_world_m": list(self.point_world_m),
            "normal_world": list(self.normal_world),
            "distance_m": self.distance_m,
            "penetration_m": self.penetration_m,
            "contact_frame_world": list(self.contact_frame_world),
            "force_contact_frame_n": values(self.force_contact_frame_n),
            "force_world_n": values(self.force_world_n),
            "torque_contact_frame_nm": values(self.torque_contact_frame_nm),
            "torque_world_nm": values(self.torque_world_nm),
            "object_on_tool_force_world_n": values(
                self.object_on_tool_force_world_n
            ),
            "tool_on_object_force_world_n": values(
                self.tool_on_object_force_world_n
            ),
            "normal_force_n": self.normal_force_n,
            "tangential_force_world_n": values(self.tangential_force_world_n),
            "resultant_force_n": self.resultant_force_n,
            "force_status": self.force_status.value,
        }


@dataclass(frozen=True, slots=True)
class ContactEvidence:
    """One deterministic measured contact snapshot."""

    status: ContactEvidenceStatus
    scene_identity: VersionedIdentity
    object_identity: VersionedIdentity
    manifest_digest: str
    sample_time_s: float
    simulation_time_s: float
    contacts: tuple[ContactRecord, ...]
    aggregate: ContactForceAggregate | None
    reason: str | None = None
    frame_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ContactEvidenceStatus):
            raise TypeError("contact evidence status must be typed")
        if not isinstance(self.scene_identity, VersionedIdentity):
            raise TypeError("contact evidence scene identity must be typed")
        if not isinstance(self.object_identity, VersionedIdentity):
            raise TypeError("contact evidence object identity must be typed")
        if not isinstance(self.manifest_digest, str) or not _MANIFEST_DIGEST_PATTERN.fullmatch(
            self.manifest_digest
        ):
            raise ValueError("contact evidence manifest digest is invalid")
        sample = _finite("contact evidence sample_time_s", self.sample_time_s)
        simulation = _finite(
            "contact evidence simulation_time_s",
            self.simulation_time_s,
        )
        if sample < 0.0 or simulation < 0.0:
            raise ValueError("contact evidence times must be non-negative")
        contacts = tuple(self.contacts)
        if any(not isinstance(item, ContactRecord) for item in contacts):
            raise TypeError("contact evidence contacts must use ContactRecord")
        if len({item.contact_identity for item in contacts}) != len(contacts):
            raise ValueError("contact evidence identities must be unique")
        if sorted(contacts, key=_stable_contact_sort_key) != list(contacts):
            raise ValueError("contact evidence contacts must be deterministically ordered")
        if self.status in {
            ContactEvidenceStatus.MEASURED,
            ContactEvidenceStatus.NO_CONTACT,
        } and self.aggregate is None:
            raise ValueError("measured contact evidence requires an aggregate")
        if self.aggregate is not None and not isinstance(
            self.aggregate,
            ContactForceAggregate,
        ):
            raise TypeError("contact evidence aggregate must use ContactForceAggregate")
        if self.status in {
            ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE,
            ContactEvidenceStatus.INVALID_CONTACT,
            ContactEvidenceStatus.SOLVER_INVALID,
        }:
            if self.aggregate is not None:
                raise ValueError("failed contact evidence must not carry aggregate")
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("failed contact evidence requires a reason")
            if contacts and not any(
                item.force_status is self.status for item in contacts
            ):
                raise ValueError(
                    "failed contact evidence status must match a failed contact record"
                )
        if self.status is ContactEvidenceStatus.NO_CONTACT and any(
            item.classification is ContactPairClassification.TARGET_OBJECT
            for item in contacts
        ):
            raise ValueError("no_contact evidence must not contain target contacts")
        if self.status is ContactEvidenceStatus.NO_CONTACT:
            if any(
                item.force_status is not ContactEvidenceStatus.MEASURED
                for item in contacts
            ):
                raise ValueError("no_contact evidence must contain only measured records")
            if self.aggregate != ContactForceAggregate.no_contact():
                raise ValueError("no_contact evidence requires an empty aggregate")
        if self.status is ContactEvidenceStatus.MEASURED and not any(
            item.classification is ContactPairClassification.TARGET_OBJECT
            for item in contacts
        ):
            raise ValueError("measured contact evidence requires target contacts")
        if self.status is ContactEvidenceStatus.MEASURED:
            target_contacts = tuple(
                item
                for item in contacts
                if item.classification is ContactPairClassification.TARGET_OBJECT
            )
            if any(
                item.force_status is not ContactEvidenceStatus.MEASURED
                for item in contacts
            ):
                raise ValueError("measured evidence requires only measured records")
            assert self.aggregate is not None
            expected = _aggregate_from_records(target_contacts)
            if not _aggregates_match(self.aggregate, expected):
                raise ValueError(
                    "contact evidence aggregate does not match target records"
                )
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason.strip()
        ):
            raise ValueError("contact evidence reason must be non-empty or null")
        if self.frame_index is not None and (
            type(self.frame_index) is not int or self.frame_index < 0
        ):
            raise ValueError("contact evidence frame_index must be non-negative")
        object.__setattr__(self, "sample_time_s", sample)
        object.__setattr__(self, "simulation_time_s", simulation)
        object.__setattr__(self, "contacts", contacts)

    @property
    def has_target_contact(self) -> bool:
        return any(
            item.classification is ContactPairClassification.TARGET_OBJECT
            for item in self.contacts
        )

    @property
    def target_contacts(self) -> tuple[ContactRecord, ...]:
        return tuple(
            item
            for item in self.contacts
            if item.classification is ContactPairClassification.TARGET_OBJECT
        )

    @property
    def is_valid_measurement(self) -> bool:
        return self.status in {
            ContactEvidenceStatus.MEASURED,
            ContactEvidenceStatus.NO_CONTACT,
        }

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": CONTACT_EVIDENCE_SCHEMA_VERSION,
            "status": self.status.value,
            "scene_identity": _identity_document(self.scene_identity),
            "object_identity": _identity_document(self.object_identity),
            "manifest_digest": self.manifest_digest,
            "sample_time_s": self.sample_time_s,
            "simulation_time_s": self.simulation_time_s,
            "frame_index": self.frame_index,
            "contacts": [item.to_document() for item in self.contacts],
            "aggregate": (
                None if self.aggregate is None else self.aggregate.to_document()
            ),
            "reason": self.reason,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_document(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def as_canonical_evidence(self) -> CanonicalEvidence:
        if self.status is ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE:
            status = EvidenceStatus.UNAVAILABLE
            value = None
            reason = self.reason
        elif self.status in {
            ContactEvidenceStatus.INVALID_CONTACT,
            ContactEvidenceStatus.SOLVER_INVALID,
        }:
            status = EvidenceStatus.INVALID
            value = None
            reason = self.reason
        else:
            status = EvidenceStatus.MEASURED
            value = self.to_document()
            reason = None
        return CanonicalEvidence(
            identity=CONTACT_EVIDENCE_V1,
            status=status,
            value=value,
            provenance=CONTACT_EVIDENCE_PROVENANCE,
            reason=reason,
        )


def _stable_contact_sort_key(record: ContactRecord) -> tuple[object, ...]:
    return (
        record.classification.value,
        record.geom1_id,
        record.geom2_id,
        record.geom1_name,
        record.geom2_name,
        record.body1_id,
        record.body2_id,
        record.body1_name,
        record.body2_name,
        record.distance_m,
        record.penetration_m,
        record.point_world_m,
        record.normal_world,
        record.contact_frame_world,
        record.force_status.value,
        record.force_contact_frame_n or (),
        record.force_world_n or (),
        record.torque_contact_frame_nm or (),
        record.torque_world_nm or (),
        record.object_on_tool_force_world_n or (),
        record.tool_on_object_force_world_n or (),
        record.normal_force_n,
        record.tangential_force_world_n or (),
        record.resultant_force_n,
        record.contact_identity,
    )


def _aggregate_from_records(
    records: Sequence[ContactRecord],
) -> ContactForceAggregate:
    """Reconstruct the public aggregate without a second geometry identity SoT."""

    object_forces = [
        record.object_on_tool_force_world_n
        for record in records
        if record.object_on_tool_force_world_n is not None
    ]
    tool_forces = [
        record.tool_on_object_force_world_n
        for record in records
        if record.tool_on_object_force_world_n is not None
    ]
    tangential = [
        record.tangential_force_world_n
        for record in records
        if record.tangential_force_world_n is not None
    ]
    resultant = _add_vectors(object_forces)
    tangential_sum = _add_vectors(tangential)
    normal_force = math.fsum(
        record.normal_force_n
        for record in records
        if record.normal_force_n is not None
    )
    wrenches: list[tuple[float, float, float, float, float, float]] = []
    for record in records:
        if record.object_on_tool_force_world_n is None:
            continue
        force = record.object_on_tool_force_world_n
        torque = record.torque_world_nm or _ZERO3
        if record.force_world_n is not None and not _close_vector(
            force,
            record.force_world_n,
        ):
            torque = _scale(torque, -1.0)
        point = record.point_world_m
        moment = (
            point[1] * force[2] - point[2] * force[1],
            point[2] * force[0] - point[0] * force[2],
            point[0] * force[1] - point[1] * force[0],
        )
        wrenches.append(
            (*force, *(moment[index] + torque[index] for index in range(3)))
        )
    wrench = (
        tuple(
            math.fsum(item[index] for item in wrenches)
            for index in range(6)
        )
        if wrenches
        else _ZERO6
    )
    return ContactForceAggregate(
        contact_count=len(records),
        normal_force_n=normal_force,
        tangential_force_world_n=tangential_sum,
        resultant_force_world_n=resultant,
        resultant_force_n=_magnitude(resultant),
        object_on_tool_force_world_n=resultant,
        tool_on_object_force_world_n=_add_vectors(tool_forces),
        object_on_tool_wrench_world_nm=wrench,  # type: ignore[arg-type]
    )


def _aggregates_match(
    actual: ContactForceAggregate,
    expected: ContactForceAggregate,
) -> bool:
    return (
        actual.contact_count == expected.contact_count
        and _close_scalar(actual.normal_force_n, expected.normal_force_n)
        and _close_vector(
            actual.tangential_force_world_n,
            expected.tangential_force_world_n,
        )
        and _close_vector(
            actual.resultant_force_world_n,
            expected.resultant_force_world_n,
        )
        and _close_scalar(actual.resultant_force_n, expected.resultant_force_n)
        and _close_vector(
            actual.object_on_tool_force_world_n,
            expected.object_on_tool_force_world_n,
        )
        and _close_vector(
            actual.tool_on_object_force_world_n,
            expected.tool_on_object_force_world_n,
        )
        and _close_vector(
            actual.object_on_tool_wrench_world_nm,
            expected.object_on_tool_wrench_world_nm,
        )
    )


class ContactEvidenceExtractor:
    """Read one compiled MuJoCo state and produce raw contact evidence."""

    def __init__(
        self,
        *,
        model: object,
        data: object,
        scene_identity: VersionedIdentity,
        object_identity: VersionedIdentity,
        manifest_digest: str,
        object_body_name: str,
        object_geom_name: str,
        robot_geom_ids: Sequence[int] | None = None,
        robot_geom_names: Sequence[str] | None = None,
        sample_time_s: float | None = None,
        frame_index: int | None = None,
        manifest: ContactTaskManifest | None = None,
    ) -> None:
        self.model = model
        self.data = data
        self.scene_identity = scene_identity
        self.object_identity = object_identity
        if not isinstance(manifest_digest, str) or not _MANIFEST_DIGEST_PATTERN.fullmatch(
            manifest_digest
        ):
            raise ContactEvidenceError("contact evidence manifest digest is invalid")
        self.manifest_digest = manifest_digest
        if manifest is not None and not isinstance(manifest, ContactTaskManifest):
            raise TypeError("contact evidence manifest must use ContactTaskManifest")
        if manifest is not None:
            actual_digest = contact_manifest_digest(manifest)
            if manifest_digest != actual_digest:
                raise ContactEvidenceError(
                    "contact evidence manifest digest does not match canonical manifest"
                )
        self.manifest = manifest
        self.object_body_name = object_body_name
        self.object_geom_name = object_geom_name
        self.robot_geom_ids = None if robot_geom_ids is None else tuple(robot_geom_ids)
        self.robot_geom_names = (
            None if robot_geom_names is None else tuple(robot_geom_names)
        )
        self.sample_time_s = sample_time_s
        self.frame_index = frame_index

    @classmethod
    def from_scene(
        cls,
        scene: object,
        simulator: object,
        *,
        robot_geom_ids: Sequence[int] | None = None,
        robot_geom_names: Sequence[str] | None = None,
        sample_time_s: float | None = None,
        frame_index: int | None = None,
    ) -> "ContactEvidenceExtractor":
        manifest = getattr(scene, "manifest", None)
        if manifest is None:
            raise TypeError("contact evidence scene must expose a manifest")
        return cls(
            model=getattr(simulator, "model", None),
            data=getattr(simulator, "data", None),
            scene_identity=manifest.scene.identity,
            object_identity=manifest.object.identity,
            manifest_digest=getattr(scene, "manifest_digest", ""),
            object_body_name=getattr(scene, "object_body_name", ""),
            object_geom_name=getattr(scene, "object_geom_name", ""),
            robot_geom_ids=robot_geom_ids,
            robot_geom_names=robot_geom_names,
            sample_time_s=sample_time_s,
            frame_index=frame_index,
            manifest=manifest,
        )

    @classmethod
    def from_scene_instance(
        cls,
        instance: object,
        *,
        robot_geom_ids: Sequence[int] | None = None,
        robot_geom_names: Sequence[str] | None = None,
        sample_time_s: float | None = None,
        frame_index: int | None = None,
    ) -> "ContactEvidenceExtractor":
        definition = getattr(instance, "definition", None)
        simulator = getattr(instance, "simulator", None)
        if definition is None or simulator is None:
            raise TypeError(
                "contact scene instance must expose definition and simulator"
            )
        resolved_frame_index = (
            frame_index
            if frame_index is not None
            else getattr(simulator, "_frame_index", None)
        )
        return cls.from_scene(
            definition,
            simulator,
            robot_geom_ids=robot_geom_ids,
            robot_geom_names=robot_geom_names,
            sample_time_s=sample_time_s,
            frame_index=resolved_frame_index,
        )

    def extract(self) -> ContactEvidence:
        try:
            import mujoco
        except ImportError:
            return self._failed(
                ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE,
                "MuJoCo is unavailable",
            )
        if self.model is None or self.data is None:
            return self._failed(
                ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE,
                "MuJoCo model/data is unavailable",
            )
        try:
            object_body_id, object_geom_id = self._validate_model(mujoco)
            simulation_time = _finite("data.time", getattr(self.data, "time"))
            if simulation_time < 0.0:
                raise ContactEvidenceError("simulation time must be non-negative")
            sample_time = (
                simulation_time
                if self.sample_time_s is None
                else _finite("sample_time_s", self.sample_time_s)
            )
            if sample_time < 0.0:
                raise ContactEvidenceError("sample time must be non-negative")
            robot_ids = self._resolve_robot_geom_ids(mujoco, object_body_id)
            ncon = self._contact_count()
            records: list[ContactRecord] = []
            force_failure: tuple[ContactEvidenceStatus, str] | None = None
            for contact_index in range(ncon):
                try:
                    record, failure = self._record(
                        mujoco,
                        contact_index,
                        object_geom_id=object_geom_id,
                        robot_geom_ids=robot_ids,
                    )
                except ContactEvidenceError as exc:
                    return self._failed(
                        ContactEvidenceStatus.INVALID_CONTACT,
                        str(exc),
                        sample_time_s=sample_time,
                        simulation_time_s=simulation_time,
                    )
                records.append(record)
                if failure is not None and force_failure is None:
                    force_failure = failure
            records = self._with_stable_contact_identities(records)
            target_records = tuple(
                record
                for record in records
                if record.classification is ContactPairClassification.TARGET_OBJECT
            )
            # Every active contact is part of this physical snapshot.  A
            # non-target contact whose force cannot be measured therefore
            # invalidates the snapshot as well; returning no_contact would
            # silently turn partial evidence into a valid absence.
            if force_failure is not None:
                return self._failed(
                    force_failure[0],
                    force_failure[1],
                    sample_time_s=sample_time,
                    simulation_time_s=simulation_time,
                    contacts=records,
                )
            if not target_records:
                return ContactEvidence(
                    status=ContactEvidenceStatus.NO_CONTACT,
                    scene_identity=self.scene_identity,
                    object_identity=self.object_identity,
                    manifest_digest=self.manifest_digest,
                    sample_time_s=sample_time,
                    simulation_time_s=simulation_time,
                    contacts=records,
                    aggregate=ContactForceAggregate.no_contact(),
                    frame_index=self.frame_index,
                )
            if any(
                record.force_status is not ContactEvidenceStatus.MEASURED
                for record in target_records
            ):
                failed = next(
                    record
                    for record in target_records
                    if record.force_status is not ContactEvidenceStatus.MEASURED
                )
                return self._failed(
                    failed.force_status,
                    f"target contact force is {failed.force_status.value}",
                    sample_time_s=sample_time,
                    simulation_time_s=simulation_time,
                    contacts=records,
                )
            return ContactEvidence(
                status=ContactEvidenceStatus.MEASURED,
                scene_identity=self.scene_identity,
                object_identity=self.object_identity,
                manifest_digest=self.manifest_digest,
                sample_time_s=sample_time,
                simulation_time_s=simulation_time,
                contacts=records,
                aggregate=self._aggregate(
                    target_records,
                    object_geom_id=object_geom_id,
                ),
                frame_index=self.frame_index,
            )
        except ContactEvidenceError as exc:
            return self._failed(ContactEvidenceStatus.INVALID_CONTACT, str(exc))
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            return self._failed(
                ContactEvidenceStatus.INVALID_CONTACT,
                f"MuJoCo contact model/data is invalid: {exc}",
            )

    observe = extract

    def _failed(
        self,
        status: ContactEvidenceStatus,
        reason: str,
        *,
        sample_time_s: float = 0.0,
        simulation_time_s: float = 0.0,
        contacts: Sequence[ContactRecord] = (),
    ) -> ContactEvidence:
        return ContactEvidence(
            status=status,
            scene_identity=self.scene_identity,
            object_identity=self.object_identity,
            manifest_digest=self.manifest_digest,
            sample_time_s=sample_time_s,
            simulation_time_s=simulation_time_s,
            contacts=tuple(contacts),
            aggregate=None,
            reason=reason,
            frame_index=self.frame_index,
        )

    def _validate_model(self, mujoco: object) -> tuple[int, int]:
        if self.scene_identity != CONTACT_SCENE_IDENTITY:
            raise ContactEvidenceError("contact scene identity mismatch")
        if self.object_identity != CONTACT_OBJECT_IDENTITY:
            raise ContactEvidenceError("contact object identity mismatch")
        if not _MANIFEST_DIGEST_PATTERN.fullmatch(self.manifest_digest):
            raise ContactEvidenceError("contact manifest digest is invalid")
        if self.manifest is None:
            raise ContactEvidenceError(
                "contact evidence manifest is required for canonical digest verification"
            )
        if self.scene_identity != self.manifest.scene.identity:
            raise ContactEvidenceError("contact evidence scene/manifest identity mismatch")
        if self.object_identity != self.manifest.object.identity:
            raise ContactEvidenceError("contact evidence object/manifest identity mismatch")
        if contact_manifest_digest(self.manifest) != self.manifest_digest:
            raise ContactEvidenceError(
                "contact evidence manifest digest does not match canonical manifest"
            )
        data_model = getattr(self.data, "model", None)
        if data_model is not None and data_model is not self.model:
            raise ContactEvidenceError("contact model/data identity mismatch")
        body_id = int(
            mujoco.mj_name2id(
                self.model,
                mujoco.mjtObj.mjOBJ_BODY,
                self.object_body_name,
            )
        )
        geom_id = int(
            mujoco.mj_name2id(
                self.model,
                mujoco.mjtObj.mjOBJ_GEOM,
                self.object_geom_name,
            )
        )
        if body_id < 0 or geom_id < 0:
            raise ContactEvidenceError("contact object body/geom identity is missing")
        if geom_id >= int(self.model.ngeom) or body_id >= int(self.model.nbody):
            raise ContactEvidenceError("contact object body/geom ID is out of range")
        if int(self.model.geom_bodyid[geom_id]) != body_id:
            raise ContactEvidenceError("contact object body/geom model mismatch")
        return body_id, geom_id

    def _resolve_robot_geom_ids(
        self,
        mujoco: object,
        object_body_id: int,
    ) -> frozenset[int]:
        if self.robot_geom_ids is not None and self.robot_geom_names is not None:
            raise ContactEvidenceError(
                "robot geometry identity must use IDs or names, not both"
            )
        if self.robot_geom_names is not None:
            values: list[int] = []
            for name in self.robot_geom_names:
                if not isinstance(name, str) or not name.strip():
                    raise ContactEvidenceError(
                        "robot contact geom names must be non-empty strings"
                    )
                geom_id = int(
                    mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, name)
                )
                if geom_id < 0:
                    raise ContactEvidenceError(
                        f"robot contact geom identity is missing: {name!r}"
                    )
                values.append(geom_id)
            if len(values) != len(set(values)):
                raise ContactEvidenceError(
                    "robot contact geom names must be unique"
                )
            result = frozenset(values)
        elif self.robot_geom_ids is not None:
            if any(type(value) is not int for value in self.robot_geom_ids):
                raise ContactEvidenceError("robot contact geom IDs must be integers")
            if len(self.robot_geom_ids) != len(set(self.robot_geom_ids)):
                raise ContactEvidenceError("robot contact geom IDs must be unique")
            result = frozenset(self.robot_geom_ids)
        else:
            raise ContactEvidenceError(
                "robot contact geom identity is unavailable; provide IDs or names"
            )
        for geom_id in result:
            if geom_id < 0 or geom_id >= int(self.model.ngeom):
                raise ContactEvidenceError("robot contact geom ID is out of range")
            if int(self.model.geom_bodyid[geom_id]) == 0:
                raise ContactEvidenceError(
                    "robot contact geom identity cannot use the world body"
                )
            if int(self.model.geom_bodyid[geom_id]) == object_body_id:
                raise ContactEvidenceError(
                    "robot contact geom identity overlaps target object"
                )
        return result

    def _contact_count(self) -> int:
        ncon = getattr(self.data, "ncon")
        if isinstance(ncon, bool) or not isinstance(ncon, (int, float)):
            raise ContactEvidenceError("MuJoCo ncon is invalid")
        if isinstance(ncon, float) and (
            not math.isfinite(ncon) or not ncon.is_integer()
        ):
            raise ContactEvidenceError("MuJoCo ncon is not an integer")
        count = int(ncon)
        if count < 0:
            raise ContactEvidenceError("MuJoCo ncon is negative")
        max_contacts = getattr(self.data, "nconmax", None)
        if max_contacts is not None and count > int(max_contacts):
            raise ContactEvidenceError("MuJoCo ncon exceeds allocated contact capacity")
        return count

    def _record(
        self,
        mujoco: object,
        contact_index: int,
        *,
        object_geom_id: int,
        robot_geom_ids: frozenset[int],
    ) -> tuple[ContactRecord, tuple[ContactEvidenceStatus, str] | None]:
        contact = self.data.contact[contact_index]
        geom1_id, geom2_id = self._contact_geom_ids(contact)
        if geom1_id < 0 or geom2_id < 0:
            raise ContactEvidenceError("contact geom ID is negative")
        if geom1_id >= int(self.model.ngeom) or geom2_id >= int(self.model.ngeom):
            raise ContactEvidenceError("contact geom ID is out of range")
        body1_id = int(self.model.geom_bodyid[geom1_id])
        body2_id = int(self.model.geom_bodyid[geom2_id])
        if (
            body1_id < 0
            or body2_id < 0
            or body1_id >= int(self.model.nbody)
            or body2_id >= int(self.model.nbody)
        ):
            raise ContactEvidenceError("contact body ID is out of range")
        geom1_name = mujoco.mj_id2name(
            self.model, mujoco.mjtObj.mjOBJ_GEOM, geom1_id
        )
        geom2_name = mujoco.mj_id2name(
            self.model, mujoco.mjtObj.mjOBJ_GEOM, geom2_id
        )
        body1_name = mujoco.mj_id2name(
            self.model, mujoco.mjtObj.mjOBJ_BODY, body1_id
        )
        body2_name = mujoco.mj_id2name(
            self.model, mujoco.mjtObj.mjOBJ_BODY, body2_id
        )
        if not all(
            isinstance(name, str) and name
            for name in (geom1_name, geom2_name, body1_name, body2_name)
        ):
            raise ContactEvidenceError("contact geom/body name is unavailable")
        try:
            geometry = read_contact_geometry(contact)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ContactEvidenceError("invalid contact geometry") from exc
        point, distance, penetration = geometry.point_world_m, geometry.distance_m, geometry.penetration_m
        frame, normal = geometry.frame_world, geometry.normal_world
        classification = self._classify(
            geom1_id,
            geom2_id,
            body1_id,
            body2_id,
            object_geom_id=object_geom_id,
            robot_geom_ids=robot_geom_ids,
        )
        force_status = ContactEvidenceStatus.MEASURED
        force_local: tuple[float, float, float] | None = None
        force_world: tuple[float, float, float] | None = None
        torque_local: tuple[float, float, float] | None = None
        torque_world: tuple[float, float, float] | None = None
        object_force: tuple[float, float, float] | None = None
        tool_force: tuple[float, float, float] | None = None
        normal_force: float | None = None
        tangential_force: tuple[float, float, float] | None = None
        resultant: float | None = None
        failure: tuple[ContactEvidenceStatus, str] | None = None
        efc_address = getattr(contact, "efc_address", None)
        if efc_address is not None and int(efc_address) < 0:
            force_status = ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE
            failure = (
                force_status,
                f"contact {contact_index} has no active solver constraint",
            )
        else:
            try:
                import numpy as np

                wrench = read_contact_wrench(self.model,self.data,contact_index,frame)
                force_local = wrench.local[:3]
                torque_local = wrench.local[3:]
                force_world = wrench.force_on_geom2_world_n
                torque_world = wrench.torque_on_geom2_world_nm
                if not all(
                    math.isfinite(value)
                    for value in (*force_world, *torque_world)
                ):
                    raise ContactEvidenceError(
                        "mj_contactForce returned non-finite values"
                    )
                if classification is ContactPairClassification.TARGET_OBJECT:
                    if geom2_id == object_geom_id:
                        object_force = _scale(force_world, -1.0)
                        tool_force = force_world
                    else:
                        object_force = force_world
                        tool_force = _scale(force_world, -1.0)
                    object_to_tool_normal = (
                        _scale(normal, -1.0)
                        if geom2_id == object_geom_id
                        else normal
                    )
                    normal_force = _dot(object_force, object_to_tool_normal)
                    normal_component = _scale(
                        object_to_tool_normal,
                        normal_force,
                    )
                    tangential_force = _sub(object_force, normal_component)
                    resultant = _magnitude(object_force)
            except ContactEvidenceError:
                force_status = ContactEvidenceStatus.SOLVER_INVALID
                failure = (
                    force_status,
                    f"contact {contact_index} force extraction returned invalid values",
                )
            except Exception as exc:
                force_status = ContactEvidenceStatus.SOLVER_INVALID
                failure = (
                    force_status,
                    f"contact {contact_index} force extraction failed: {type(exc).__name__}",
                )
        return (
            ContactRecord(
                contact_identity=f"contact-{contact_index}",
                classification=classification,
                geom1_id=geom1_id,
                geom2_id=geom2_id,
                geom1_name=geom1_name,
                geom2_name=geom2_name,
                body1_id=body1_id,
                body2_id=body2_id,
                body1_name=body1_name,
                body2_name=body2_name,
                point_world_m=point,
                normal_world=normal,
                distance_m=distance,
                penetration_m=penetration,
                contact_frame_world=frame,
                force_contact_frame_n=force_local,
                force_world_n=force_world,
                torque_contact_frame_nm=torque_local,
                torque_world_nm=torque_world,
                object_on_tool_force_world_n=object_force,
                tool_on_object_force_world_n=tool_force,
                normal_force_n=normal_force,
                tangential_force_world_n=tangential_force,
                resultant_force_n=resultant,
                force_status=force_status,
            ),
            failure,
        )

    @staticmethod
    def _contact_geom_ids(contact: object) -> tuple[int, int]:
        if hasattr(contact, "geom"):
            values = getattr(contact, "geom")
            if not hasattr(values, "__len__") or len(values) != 2:
                raise ContactEvidenceError("contact geom pair is invalid")
            return int(values[0]), int(values[1])
        return int(getattr(contact, "geom1")), int(getattr(contact, "geom2"))

    @staticmethod
    def _classify(
        geom1_id: int,
        geom2_id: int,
        body1_id: int,
        body2_id: int,
        *,
        object_geom_id: int,
        robot_geom_ids: frozenset[int],
    ) -> ContactPairClassification:
        first_object = geom1_id == object_geom_id
        second_object = geom2_id == object_geom_id
        if first_object != second_object:
            other_geom = geom2_id if first_object else geom1_id
            other_body = body2_id if first_object else body1_id
            if other_geom in robot_geom_ids:
                return ContactPairClassification.TARGET_OBJECT
            if other_body == 0:
                return ContactPairClassification.ENVIRONMENT_CONTACT
            return ContactPairClassification.OTHER_OBJECT
        if geom1_id in robot_geom_ids and geom2_id in robot_geom_ids:
            return ContactPairClassification.SELF_CONTACT
        if body1_id == 0 or body2_id == 0:
            return ContactPairClassification.ENVIRONMENT_CONTACT
        if (geom1_id in robot_geom_ids) != (geom2_id in robot_geom_ids):
            return ContactPairClassification.OTHER_OBJECT
        return ContactPairClassification.UNCLASSIFIED

    @staticmethod
    def _with_stable_contact_identities(
        records: Sequence[ContactRecord],
    ) -> list[ContactRecord]:
        ordered = sorted(records, key=_stable_contact_sort_key)
        pair_ordinals: defaultdict[tuple[object, ...], int] = defaultdict(int)
        result: list[ContactRecord] = []
        for record in ordered:
            key = (
                record.classification.value,
                record.geom1_id,
                record.geom2_id,
                record.geom1_name,
                record.geom2_name,
                record.body1_id,
                record.body2_id,
                record.body1_name,
                record.body2_name,
            )
            ordinal = pair_ordinals[key]
            pair_ordinals[key] += 1
            values = {
                field: getattr(record, field)
                for field in record.__dataclass_fields__
                if field != "contact_identity"
            }
            result.append(
                ContactRecord(
                    contact_identity=(
                        f"{record.classification.value}:"
                        f"{record.geom1_name}:{record.geom2_name}:{ordinal}"
                    ),
                    **values,
                )
            )
        return sorted(result, key=_stable_contact_sort_key)

    @staticmethod
    def _aggregate(
        records: Sequence[ContactRecord],
        *,
        object_geom_id: int,
    ) -> ContactForceAggregate:
        object_forces = [
            record.object_on_tool_force_world_n
            for record in records
            if record.object_on_tool_force_world_n is not None
        ]
        tool_forces = [
            record.tool_on_object_force_world_n
            for record in records
            if record.tool_on_object_force_world_n is not None
        ]
        tangential = [
            record.tangential_force_world_n
            for record in records
            if record.tangential_force_world_n is not None
        ]
        resultant = _add_vectors(object_forces)
        tangential_sum = _add_vectors(tangential)
        normal_force = math.fsum(
            record.normal_force_n
            for record in records
            if record.normal_force_n is not None
        )
        wrenches: list[tuple[float, float, float, float, float, float]] = []
        for record in records:
            if record.object_on_tool_force_world_n is None:
                continue
            force = record.object_on_tool_force_world_n
            torque = record.torque_world_nm or _ZERO3
            # mj_contactForce reports the force/torque on geom2.  When the
            # target object is geom2, object-on-tool is the opposite wrench,
            # just as it is for the normalized force vector.
            if record.geom2_id == object_geom_id:
                torque = _scale(torque, -1.0)
            moment = (
                record.point_world_m[1] * force[2]
                - record.point_world_m[2] * force[1],
                record.point_world_m[2] * force[0]
                - record.point_world_m[0] * force[2],
                record.point_world_m[0] * force[1]
                - record.point_world_m[1] * force[0],
            )
            wrenches.append(
                (*force, *(moment[index] + torque[index] for index in range(3)))
            )
        wrench = (
            tuple(
                math.fsum(item[index] for item in wrenches)
                for index in range(6)
            )
            if wrenches
            else _ZERO6
        )
        return ContactForceAggregate(
            contact_count=len(records),
            normal_force_n=normal_force,
            tangential_force_world_n=tangential_sum,
            resultant_force_world_n=resultant,
            resultant_force_n=_magnitude(resultant),
            object_on_tool_force_world_n=resultant,
            tool_on_object_force_world_n=_add_vectors(tool_forces),
            object_on_tool_wrench_world_nm=wrench,  # type: ignore[arg-type]
        )


def extract_contact_evidence(
    scene: object,
    simulator: object,
    *,
    robot_geom_ids: Sequence[int] | None = None,
    robot_geom_names: Sequence[str] | None = None,
    sample_time_s: float | None = None,
    frame_index: int | None = None,
) -> ContactEvidence:
    """Convenience facade for a backend-owned ContactScene instance."""

    return ContactEvidenceExtractor.from_scene(
        scene,
        simulator,
        robot_geom_ids=robot_geom_ids,
        robot_geom_names=robot_geom_names,
        sample_time_s=sample_time_s,
        frame_index=frame_index,
    ).extract()


def extract_contact_evidence_from_scene_instance(
    instance: object,
    *,
    robot_geom_ids: Sequence[int] | None = None,
    robot_geom_names: Sequence[str] | None = None,
    sample_time_s: float | None = None,
    frame_index: int | None = None,
) -> ContactEvidence:
    """Extract evidence from a ContactSceneInstance without viewer state."""

    definition = getattr(instance, "definition", None)
    simulator = getattr(instance, "simulator", None)
    if definition is None or simulator is None:
        raise TypeError("contact scene instance must expose definition and simulator")
    resolved_frame_index = (
        frame_index
        if frame_index is not None
        else getattr(simulator, "_frame_index", None)
    )
    return extract_contact_evidence(
        definition,
        simulator,
        robot_geom_ids=robot_geom_ids,
        robot_geom_names=robot_geom_names,
        sample_time_s=sample_time_s,
        frame_index=resolved_frame_index,
    )


__all__ = [
    "CONTACT_EVIDENCE_IDENTITY",
    "CONTACT_DISTANCE_UNIT",
    "CONTACT_EVIDENCE_PROVENANCE",
    "CONTACT_EVIDENCE_SCHEMA_VERSION",
    "CONTACT_FORCE_UNIT",
    "CONTACT_FRAME_NAME",
    "CONTACT_POINT_FRAME",
    "CONTACT_TORQUE_UNIT",
    "ContactEvidence",
    "ContactEvidenceError",
    "ContactEvidenceExtractor",
    "ContactEvidenceStatus",
    "ContactMeasurementStatus",
    "ContactForceAggregate",
    "ContactClassification",
    "ContactPairClassification",
    "ContactRecord",
    "extract_contact_evidence",
    "extract_contact_evidence_from_scene_instance",
    "measure_contact_evidence",
]


measure_contact_evidence = extract_contact_evidence
