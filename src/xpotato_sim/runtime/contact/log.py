"""versionedで決定的なJSONL contact-task trial log。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, cast, Literal, overload

from xpotato_sim.runtime.contact.evidence import (
    CONTACT_EVIDENCE_IDENTITY,
    CONTACT_EVIDENCE_SCHEMA_VERSION,
    ContactEvidence,
    ContactEvidenceStatus,
    ContactForceAggregate,
    ContactPairClassification,
    ContactRecord,
)
from xpotato_sim.runtime.contact.manifest import (
    ContactTaskManifest,
    decode_contact_manifest,
)
from xpotato_sim.runtime.contact.task_contract import (
    ContactOperatorStatus,
    ContactTaskContext,
    ContactTaskContractError,
    ContactTaskObservation,
    ContactTaskOutcome,
    ContactTaskPhase,
    ContactTrialIdentity,
)
from xpotato_sim.runtime.contact.virtual_reaction_force import (
    VIRTUAL_REACTION_FORCE_IDENTITY,
    VIRTUAL_REACTION_FORCE_INPUT_FRAME,
    VIRTUAL_REACTION_FORCE_PROVENANCE,
    VIRTUAL_REACTION_FORCE_SIGN_CONVENTION,
    VIRTUAL_REACTION_FORCE_SOURCE_FIELD,
    VIRTUAL_REACTION_FORCE_UNIT,
    VIRTUAL_REACTION_FORCE_SCHEMA_VERSION,
    VirtualReactionForceError,
    VirtualReactionForceFrame,
    VirtualReactionForceManifest,
    VirtualReactionForceSignal,
    VirtualReactionForceStatus,
    decode_virtual_reaction_force_manifest,
)
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    TaskTerminalClassification,
    VersionedIdentity,
)


CONTACT_TASK_LOG_SCHEMA_VERSION: Final[str] = "contact-task-log/v1"
CONTACT_TASK_LOG_CONTRACT_VERSION: Final[int] = 1
CONTACT_TASK_LOG_PROVENANCE: Final[str] = "runtime_contact_task_log/v1"
CONTACT_TASK_LOG_DIGEST_ALGORITHM: Final[str] = "sha256"
CONTACT_TASK_PRESENTATION_SCHEMA_VERSION: Final[str] = "contact-task-presentation/v1"
CONTACT_CUBE_SCENE_IDENTITY: Final[VersionedIdentity] = VersionedIdentity(
    "contact_cube_scene", 1
)
CONTACT_CUBE_OBJECT_IDENTITY: Final[VersionedIdentity] = VersionedIdentity(
    "contact_cube", 1
)
CONTACT_CUBE_PRESENTATION_IDENTITY: Final[str] = "contact-cube/v1"

_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z")
_VECTOR3_FIELDS: Final[tuple[str, ...]] = (
    "tip_position_world_m",
    "object_position_world_m",
    "contact_location_world_m",
)


class ContactTaskLogSourceKind(str, Enum):
    """MuJoCo実行時captureとsoftware-only生成fixtureを区別する。"""

    RUNTIME_CAPTURE = "runtime_capture"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class ContactTaskLogError(ValueError):
    """厳密なcontact-task log契約の違反。"""


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ContactTaskLogError(f"contact task log serialization failed: {exc}") from exc


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContactTaskLogError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ContactTaskLogError(f"invalid JSON numeric constant: {value}")


def _strict_object(
    name: str,
    value: object,
    expected: set[str],
) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise ContactTaskLogError(f"{name} has missing or unexpected fields")
    return value


def _finite(name: str, value: object, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContactTaskLogError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (non_negative and result < 0.0):
        raise ContactTaskLogError(f"{name} is outside its finite range")
    return 0.0 if result == 0.0 else result


def _optional_finite(name: str, value: object) -> float | None:
    return None if value is None else _finite(name, value)


# 検証済みの固定長を型へ伝える。実装の受理集合と例外は維持する。
@overload
def _vector(name: str, value: object, *, length: Literal[3]) -> tuple[float, float, float]: ...

@overload
def _vector(name: str, value: object, *, length: Literal[4]) -> tuple[float, float, float, float]: ...

@overload
def _vector(name: str, value: object, *, length: int) -> tuple[float, ...]: ...


def _vector(
    name: str,
    value: object,
    *,
    length: int,
) -> tuple[float, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != length
    ):
        raise ContactTaskLogError(f"{name} must contain {length} finite numbers")
    return tuple(_finite(f"{name}[{index}]", item) for index, item in enumerate(value))


# 検証済みの固定長を型へ伝える。実装の受理集合と例外は維持する。
@overload
def _optional_vector(name: str, value: object, *, length: Literal[3]) -> tuple[float, float, float] | None: ...

@overload
def _optional_vector(name: str, value: object, *, length: Literal[4]) -> tuple[float, float, float, float] | None: ...

@overload
def _optional_vector(name: str, value: object, *, length: int) -> tuple[float, ...] | None: ...


def _optional_vector(
    name: str,
    value: object,
    *,
    length: int,
) -> tuple[float, ...] | None:
    return None if value is None else _vector(name, value, length=length)


def _identity_document(identity: VersionedIdentity) -> dict[str, object]:
    return {"name": identity.name, "version": identity.version}


def _decode_identity(name: str, value: object) -> VersionedIdentity:
    root = _strict_object(f"{name} identity", value, {"name", "version"})
    try:
        return VersionedIdentity(root["name"], root["version"])  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ContactTaskLogError(f"{name} identity is invalid") from exc


def _decode_trial(value: object) -> ContactTrialIdentity:
    root = _strict_object(
        "trial identity",
        value,
        {"attempt_index", "repetition_index", "retry_of_trial_id", "trial_id"},
    )
    try:
        return ContactTrialIdentity(
            trial_id=root["trial_id"],  # type: ignore[arg-type]
            repetition_index=root["repetition_index"],  # type: ignore[arg-type]
            attempt_index=root["attempt_index"],  # type: ignore[arg-type]
            retry_of_trial_id=root["retry_of_trial_id"],  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        raise ContactTaskLogError(f"trial identity is invalid: {exc}") from exc


def _decode_enum(enum_type: type[Enum], name: str, value: object) -> Enum:
    if not isinstance(value, str):
        raise ContactTaskLogError(f"{name} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ContactTaskLogError(f"{name} is unsupported") from exc


def _digest(name: str, value: object) -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise ContactTaskLogError(f"{name} is invalid")
    return value


def _optional_vector_document(value: Sequence[float] | None) -> list[float] | None:
    return None if value is None else list(value)


def _task_context_document(context: ContactTaskContext) -> dict[str, object]:
    return {
        "approach_alignment_min_cosine": context.approach_alignment_min_cosine,
        "dwell_interval_s": context.dwell_interval_s,
        "max_contact_location_drift_m": context.max_contact_location_drift_m,
        "normal_alignment_min_cosine": context.normal_alignment_min_cosine,
        "require_pose_measurement": context.require_pose_measurement,
        "target_normal_force_band_n": _optional_vector_document(
            context.target_normal_force_band_n
        ),
        "timeout_s": context.timeout_s,
        "trial": context.trial.to_document(),
    }


@dataclass(frozen=True, slots=True)
class ContactTaskLogTaskState:
    """force dataと分離したTask所有のphase/classification snapshot。"""

    phase: ContactTaskPhase
    classification: TaskTerminalClassification
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.phase, ContactTaskPhase):
            raise TypeError("task state phase must use ContactTaskPhase")
        if not isinstance(self.classification, TaskTerminalClassification):
            raise TypeError(
                "task state classification must use TaskTerminalClassification"
            )
        if self.classification is TaskTerminalClassification.SUCCESS:
            if self.phase is not ContactTaskPhase.SUCCESS or self.reason is not None:
                raise ContactTaskLogError("success task state is inconsistent")
        elif self.classification is TaskTerminalClassification.FAILURE:
            if self.phase is not ContactTaskPhase.FAILURE:
                raise ContactTaskLogError("failure task state phase is inconsistent")
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ContactTaskLogError("failure task state requires a reason")
        elif self.classification is TaskTerminalClassification.TECHNICAL_INVALID:
            if self.phase is not ContactTaskPhase.TECHNICAL_INVALID:
                raise ContactTaskLogError("technical-invalid task state is inconsistent")
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ContactTaskLogError(
                    "technical-invalid task state requires a reason"
                )
        else:
            if self.phase in {
                ContactTaskPhase.SUCCESS,
                ContactTaskPhase.FAILURE,
                ContactTaskPhase.TECHNICAL_INVALID,
            }:
                raise ContactTaskLogError("running task state has a terminal phase")
            if self.reason is not None and (
                not isinstance(self.reason, str) or not self.reason.strip()
            ):
                raise ContactTaskLogError(
                    "running task state reason must be non-empty or null"
                )

    def to_document(self) -> dict[str, object]:
        return {
            "classification": self.classification.value,
            "phase": self.phase.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ContactTaskLogHeader:
    """1 trialのcontact、signal policy、task condition identity。"""

    context: ContactTaskContext
    force_manifest: VirtualReactionForceManifest
    schema_version: str = CONTACT_TASK_LOG_SCHEMA_VERSION
    contract_version: int = CONTACT_TASK_LOG_CONTRACT_VERSION
    source_kind: ContactTaskLogSourceKind = ContactTaskLogSourceKind.RUNTIME_CAPTURE

    def __post_init__(self) -> None:
        if not isinstance(self.context, ContactTaskContext):
            raise TypeError("log header context must use ContactTaskContext")
        if not isinstance(self.force_manifest, VirtualReactionForceManifest):
            raise TypeError(
                "log header force_manifest must use VirtualReactionForceManifest"
            )
        if not isinstance(self.source_kind, ContactTaskLogSourceKind):
            raise TypeError("log header source_kind must use ContactTaskLogSourceKind")
        if self.schema_version != CONTACT_TASK_LOG_SCHEMA_VERSION:
            raise ContactTaskLogError("unsupported contact task log schema version")
        if (
            type(self.contract_version) is not int
            or self.contract_version != CONTACT_TASK_LOG_CONTRACT_VERSION
        ):
            raise ContactTaskLogError("unsupported contact task log contract version")
        if self.force_manifest.contact_manifest != self.context.manifest:
            raise ContactTaskLogError(
                "signal manifest and task context contact manifests differ"
            )

    @property
    def binding_document(self) -> dict[str, object]:
        manifest = self.context.manifest
        return {
            "manifest_digest": self.context.manifest_digest,
            "object_identity": _identity_document(manifest.scene.object.identity),
            "presentation_identity": (
                manifest.scene.presentation.visual_feedback_identity
            ),
            "robot_bundle": {
                "contract_version": manifest.robot_bundle.contract_version,
                "plugin_id": manifest.robot_bundle.plugin_id,
            },
            "scene_identity": _identity_document(manifest.scene.identity),
            "source_kind": self.source_kind.value,
            "signal_manifest_digest": self.force_manifest.digest,
            "trial": self.context.trial.to_document(),
        }

    def to_document(self) -> dict[str, object]:
        return {
            "binding": self.binding_document,
            "contract_version": self.contract_version,
            "manifest": self.context.manifest.to_document(),
            "provenance": CONTACT_TASK_LOG_PROVENANCE,
            "record_kind": "header",
            "schema_version": self.schema_version,
            "source_kind": self.source_kind.value,
            "signal_manifest": self.force_manifest.to_document(),
            "task_context": _task_context_document(self.context),
        }


@dataclass(frozen=True, slots=True)
class ContactTaskLogSample:
    """raw observation、derived signal、task stateを分離した記録。"""

    observation: ContactTaskObservation
    force_signal: VirtualReactionForceSignal
    task_state: ContactTaskLogTaskState

    def __post_init__(self) -> None:
        if not isinstance(self.observation, ContactTaskObservation):
            raise TypeError("log sample observation must use ContactTaskObservation")
        if not isinstance(self.force_signal, VirtualReactionForceSignal):
            raise TypeError(
                "log sample force_signal must use VirtualReactionForceSignal"
            )
        if not isinstance(self.task_state, ContactTaskLogTaskState):
            raise TypeError(
                "log sample task_state must use ContactTaskLogTaskState"
            )

    def to_document(
        self,
        *,
        header: ContactTaskLogHeader,
        sequence_index: int,
    ) -> dict[str, object]:
        if not isinstance(header, ContactTaskLogHeader):
            raise TypeError("log sample encoding requires ContactTaskLogHeader")
        return {
            "binding": header.binding_document,
            "derived_reaction_force": self.force_signal.to_document(),
            "elapsed_time_s": self.observation.elapsed_time_s,
            "raw_contact_evidence": self.observation.contact_evidence.to_document(),
            "record_kind": "sample",
            "schema_version": CONTACT_TASK_LOG_SCHEMA_VERSION,
            "sequence_index": sequence_index,
            "task_observation": {
                "contact_location_world_m": _optional_vector_document(
                    self.observation.contact_location_world_m
                ),
                "object_orientation_wxyz": _optional_vector_document(
                    self.observation.object_orientation_wxyz
                ),
                "object_position_world_m": _optional_vector_document(
                    self.observation.object_position_world_m
                ),
                "operator_status": self.observation.operator_status.value,
                "reason": self.observation.reason,
                "tip_position_world_m": _optional_vector_document(
                    self.observation.tip_position_world_m
                ),
            },
            "task_state": self.task_state.to_document(),
        }


@dataclass(frozen=True, slots=True)
class ContactTaskLogSummary:
    """全sampleと同じtrialに結び付いたTask所有outcome summary。"""

    outcome: ContactTaskOutcome
    sample_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ContactTaskOutcome):
            raise TypeError("log summary outcome must use ContactTaskOutcome")
        if type(self.sample_count) is not int or self.sample_count < 1:
            raise ContactTaskLogError("log summary sample_count must be positive")

    def to_document(self, *, header: ContactTaskLogHeader) -> dict[str, object]:
        return {
            "binding": header.binding_document,
            "outcome": self.outcome.to_document(),
            "record_kind": "summary",
            "sample_count": self.sample_count,
            "schema_version": CONTACT_TASK_LOG_SCHEMA_VERSION,
        }

def _decode_force_aggregate(value: object) -> ContactForceAggregate | None:
    if value is None:
        return None
    root = _strict_object(
        "contact force aggregate",
        value,
        {
            "contact_count",
            "normal_force_n",
            "tangential_force_world_n",
            "resultant_force_world_n",
            "resultant_force_n",
            "object_on_tool_force_world_n",
            "tool_on_object_force_world_n",
            "object_on_tool_wrench_world_nm",
        },
    )
    try:
        return ContactForceAggregate(
            contact_count=root["contact_count"],  # type: ignore[arg-type]
            normal_force_n=_finite("aggregate.normal_force_n", root["normal_force_n"]),
            tangential_force_world_n=_vector(
                "aggregate.tangential_force_world_n",
                root["tangential_force_world_n"],
                length=3,
            ),
            resultant_force_world_n=_vector(
                "aggregate.resultant_force_world_n",
                root["resultant_force_world_n"],
                length=3,
            ),
            resultant_force_n=_finite("aggregate.resultant_force_n", root["resultant_force_n"]),
            object_on_tool_force_world_n=_vector(
                "aggregate.object_on_tool_force_world_n",
                root["object_on_tool_force_world_n"],
                length=3,
            ),
            tool_on_object_force_world_n=_vector(
                "aggregate.tool_on_object_force_world_n",
                root["tool_on_object_force_world_n"],
                length=3,
            ),
            object_on_tool_wrench_world_nm=_vector(
                "aggregate.object_on_tool_wrench_world_nm",
                root["object_on_tool_wrench_world_nm"],
                length=6,
            ),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        raise ContactTaskLogError(f"contact force aggregate is invalid: {exc}") from exc


def _decode_contact_record(value: object, index: int) -> ContactRecord:
    root = _strict_object(
        f"contact record {index}",
        value,
        {
            "contact_identity",
            "classification",
            "geom1_id",
            "geom2_id",
            "geom1_name",
            "geom2_name",
            "body1_id",
            "body2_id",
            "body1_name",
            "body2_name",
            "point_world_m",
            "normal_world",
            "distance_m",
            "penetration_m",
            "contact_frame_world",
            "force_contact_frame_n",
            "force_world_n",
            "torque_contact_frame_nm",
            "torque_world_nm",
            "object_on_tool_force_world_n",
            "tool_on_object_force_world_n",
            "normal_force_n",
            "tangential_force_world_n",
            "resultant_force_n",
            "force_status",
        },
    )
    try:
        return ContactRecord(
            contact_identity=root["contact_identity"],  # type: ignore[arg-type]
            classification=cast(
                ContactPairClassification,
                _decode_enum(ContactPairClassification, "contact classification", root["classification"]),
            ),
            geom1_id=root["geom1_id"],  # type: ignore[arg-type]
            geom2_id=root["geom2_id"],  # type: ignore[arg-type]
            geom1_name=root["geom1_name"],  # type: ignore[arg-type]
            geom2_name=root["geom2_name"],  # type: ignore[arg-type]
            body1_id=root["body1_id"],  # type: ignore[arg-type]
            body2_id=root["body2_id"],  # type: ignore[arg-type]
            body1_name=root["body1_name"],  # type: ignore[arg-type]
            body2_name=root["body2_name"],  # type: ignore[arg-type]
            point_world_m=_vector("contact.point_world_m", root["point_world_m"], length=3),
            normal_world=_vector("contact.normal_world", root["normal_world"], length=3),
            distance_m=_finite("contact.distance_m", root["distance_m"]),
            penetration_m=_finite("contact.penetration_m", root["penetration_m"], non_negative=True),
            contact_frame_world=_vector("contact.contact_frame_world", root["contact_frame_world"], length=9),
            force_contact_frame_n=_optional_vector("contact.force_contact_frame_n", root["force_contact_frame_n"], length=3),
            force_world_n=_optional_vector("contact.force_world_n", root["force_world_n"], length=3),
            torque_contact_frame_nm=_optional_vector("contact.torque_contact_frame_nm", root["torque_contact_frame_nm"], length=3),
            torque_world_nm=_optional_vector("contact.torque_world_nm", root["torque_world_nm"], length=3),
            object_on_tool_force_world_n=_optional_vector("contact.object_on_tool_force_world_n", root["object_on_tool_force_world_n"], length=3),
            tool_on_object_force_world_n=_optional_vector("contact.tool_on_object_force_world_n", root["tool_on_object_force_world_n"], length=3),
            normal_force_n=_optional_finite("contact.normal_force_n", root["normal_force_n"]),
            tangential_force_world_n=_optional_vector("contact.tangential_force_world_n", root["tangential_force_world_n"], length=3),
            resultant_force_n=_optional_finite("contact.resultant_force_n", root["resultant_force_n"]),
            force_status=cast(
                ContactEvidenceStatus,
                _decode_enum(ContactEvidenceStatus, "contact force status", root["force_status"]),
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ContactTaskLogError(f"contact record {index} is invalid: {exc}") from exc


def _decode_contact_evidence(value: object) -> ContactEvidence:
    root = _strict_object(
        "raw contact evidence",
        value,
        {
            "schema_version",
            "status",
            "scene_identity",
            "object_identity",
            "manifest_digest",
            "sample_time_s",
            "simulation_time_s",
            "frame_index",
            "contacts",
            "aggregate",
            "reason",
        },
    )
    if root["schema_version"] != CONTACT_EVIDENCE_SCHEMA_VERSION:
        raise ContactTaskLogError("unsupported raw contact evidence schema")
    raw_contacts = root["contacts"]
    if not isinstance(raw_contacts, list):
        raise ContactTaskLogError("raw contact evidence contacts must be an array")
    try:
        frame_index = root["frame_index"]
        if frame_index is not None and (type(frame_index) is not int or frame_index < 0):
            raise ContactTaskLogError("raw contact evidence frame_index is invalid")
        reason = root["reason"]
        if reason is not None and not isinstance(reason, str):
            raise ContactTaskLogError("raw contact evidence reason must be a string or null")
        return ContactEvidence(
            status=cast(
                ContactEvidenceStatus,
                _decode_enum(ContactEvidenceStatus, "raw evidence status", root["status"]),
            ),
            scene_identity=_decode_identity("scene", root["scene_identity"]),
            object_identity=_decode_identity("object", root["object_identity"]),
            manifest_digest=_digest("raw evidence manifest_digest", root["manifest_digest"]),
            sample_time_s=_finite("raw evidence sample_time_s", root["sample_time_s"], non_negative=True),
            simulation_time_s=_finite("raw evidence simulation_time_s", root["simulation_time_s"], non_negative=True),
            contacts=tuple(
                _decode_contact_record(item, index)
                for index, item in enumerate(raw_contacts)
            ),
            aggregate=_decode_force_aggregate(root["aggregate"]),
            reason=reason,
            frame_index=frame_index,
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContactTaskLogError):
            raise
        raise ContactTaskLogError(f"raw contact evidence is invalid: {exc}") from exc


def _decode_signal(value: object) -> VirtualReactionForceSignal:
    root = _strict_object(
        "derived reaction force",
        value,
        {
            "deadbanded",
            "filtered",
            "force_source",
            "frame_index",
            "identity",
            "manifest_digest",
            "output",
            "provenance",
            "raw_force_world_n",
            "reason",
            "rate_limited",
            "sample_time_s",
            "saturated",
            "schema_version",
            "source_contact_manifest_digest",
            "status",
            "simulation_time_s",
            "trial",
            "unit",
            "sign_convention",
        },
    )
    if root["schema_version"] != VIRTUAL_REACTION_FORCE_SCHEMA_VERSION:
        raise ContactTaskLogError("unsupported derived reaction-force schema")
    if root["provenance"] != VIRTUAL_REACTION_FORCE_PROVENANCE:
        raise ContactTaskLogError("derived reaction-force provenance is invalid")
    if root["identity"] != _identity_document(VIRTUAL_REACTION_FORCE_IDENTITY):
        raise ContactTaskLogError("derived reaction-force identity is invalid")
    if root["unit"] != VIRTUAL_REACTION_FORCE_UNIT:
        raise ContactTaskLogError("derived reaction-force unit is invalid")
    if root["sign_convention"] != VIRTUAL_REACTION_FORCE_SIGN_CONVENTION:
        raise ContactTaskLogError("derived reaction-force sign convention is invalid")
    source = _strict_object(
        "derived force source",
        root["force_source"],
        {"field", "frame", "identity", "manifest_digest", "sign_convention", "status", "unit"},
    )
    if (
        source["field"] != VIRTUAL_REACTION_FORCE_SOURCE_FIELD
        or source["frame"] != VIRTUAL_REACTION_FORCE_INPUT_FRAME
        or source["identity"] != _identity_document(CONTACT_EVIDENCE_IDENTITY)
        or source["sign_convention"] != VIRTUAL_REACTION_FORCE_SIGN_CONVENTION
        or source["unit"] != VIRTUAL_REACTION_FORCE_UNIT
    ):
        raise ContactTaskLogError("derived force source descriptor is invalid")
    output = _strict_object(
        "derived force output",
        root["output"],
        {"force_n", "frame", "raw_force_n", "sign_convention", "unit", "world_to_output_rotation_row_major"},
    )
    if (
        output["sign_convention"] != VIRTUAL_REACTION_FORCE_SIGN_CONVENTION
        or output["unit"] != VIRTUAL_REACTION_FORCE_UNIT
    ):
        raise ContactTaskLogError("derived force output descriptor is invalid")
    source_status = source["status"]
    if source_status is not None:
        source_status = _decode_enum(ContactEvidenceStatus, "derived force source status", source_status)
    frame_index = root["frame_index"]
    if frame_index is not None and (type(frame_index) is not int or frame_index < 0):
        raise ContactTaskLogError("derived force frame_index is invalid")
    try:
        return VirtualReactionForceSignal(
            manifest_digest=_digest("derived force manifest_digest", root["manifest_digest"]),
            source_contact_manifest_digest=_digest(
                "derived force source_contact_manifest_digest",
                root["source_contact_manifest_digest"],
            ),
            trial=_decode_trial(root["trial"]),
            status=cast(
                VirtualReactionForceStatus,
                _decode_enum(VirtualReactionForceStatus, "derived force status", root["status"]),
            ),
            source_status=cast(ContactEvidenceStatus, source_status) if source_status is not None else None,
            output_frame=cast(
                VirtualReactionForceFrame,
                _decode_enum(VirtualReactionForceFrame, "derived force output frame", output["frame"]),
            ),
            sample_time_s=_optional_finite("derived force sample_time_s", root["sample_time_s"]),
            simulation_time_s=_optional_finite("derived force simulation_time_s", root["simulation_time_s"]),
            frame_index=frame_index,
            raw_force_world_n=_optional_vector("derived force raw_force_world_n", root["raw_force_world_n"], length=3),
            raw_force_output_frame_n=_optional_vector("derived force output raw_force_n", output["raw_force_n"], length=3),
            force_n=_optional_vector("derived force output force_n", output["force_n"], length=3),
            world_to_output_rotation=_optional_vector(
                "derived force world_to_output_rotation_row_major",
                output["world_to_output_rotation_row_major"],
                length=9,
            ),
            filtered=root["filtered"],  # type: ignore[arg-type]
            deadbanded=root["deadbanded"],  # type: ignore[arg-type]
            rate_limited=root["rate_limited"],  # type: ignore[arg-type]
            clamped=root["saturated"],  # type: ignore[arg-type]
            reason=root["reason"],  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContactTaskLogError):
            raise
        raise ContactTaskLogError(f"derived reaction force is invalid: {exc}") from exc


def _decode_task_context(value: object, manifest: ContactTaskManifest) -> ContactTaskContext:
    root = _strict_object(
        "contact task context",
        value,
        {
            "approach_alignment_min_cosine",
            "dwell_interval_s",
            "max_contact_location_drift_m",
            "normal_alignment_min_cosine",
            "require_pose_measurement",
            "target_normal_force_band_n",
            "timeout_s",
            "trial",
        },
    )
    try:
        return ContactTaskContext(
            manifest=manifest,
            dwell_interval_s=_finite("task_context.dwell_interval_s", root["dwell_interval_s"], non_negative=True),
            timeout_s=_finite("task_context.timeout_s", root["timeout_s"], non_negative=True),
            target_normal_force_band_n=_optional_vector(
                "task_context.target_normal_force_band_n",
                root["target_normal_force_band_n"],
                length=2,
            ),  # type: ignore[arg-type]
            approach_alignment_min_cosine=_optional_finite(
                "task_context.approach_alignment_min_cosine",
                root["approach_alignment_min_cosine"],
            ),
            normal_alignment_min_cosine=_optional_finite(
                "task_context.normal_alignment_min_cosine",
                root["normal_alignment_min_cosine"],
            ),
            max_contact_location_drift_m=_optional_finite(
                "task_context.max_contact_location_drift_m",
                root["max_contact_location_drift_m"],
            ),
            require_pose_measurement=root["require_pose_measurement"],  # type: ignore[arg-type]
            trial=_decode_trial(root["trial"]),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContactTaskLogError):
            raise
        raise ContactTaskLogError(f"contact task context is invalid: {exc}") from exc


def _decode_log_header(value: object) -> ContactTaskLogHeader:
    root = _strict_object(
        "contact task log header",
        value,
        {
            "binding",
            "contract_version",
            "manifest",
            "provenance",
            "record_kind",
            "schema_version",
            "signal_manifest",
            "source_kind",
            "task_context",
        },
    )
    if (
        root["schema_version"] != CONTACT_TASK_LOG_SCHEMA_VERSION
        or root["contract_version"] != CONTACT_TASK_LOG_CONTRACT_VERSION
        or root["provenance"] != CONTACT_TASK_LOG_PROVENANCE
        or root["record_kind"] != "header"
    ):
        raise ContactTaskLogError("contact task log header identity is unsupported")
    try:
        manifest = decode_contact_manifest(_canonical_json(root["manifest"]))
        force_manifest = decode_virtual_reaction_force_manifest(
            _canonical_json(root["signal_manifest"]),
            contact_manifest=manifest,
        )
        context = _decode_task_context(root["task_context"], manifest)
        source_kind = cast(
            ContactTaskLogSourceKind,
            _decode_enum(ContactTaskLogSourceKind, "log source_kind", root["source_kind"]),
        )
        header = ContactTaskLogHeader(context=context, force_manifest=force_manifest, source_kind=source_kind)
    except (TypeError, ValueError, VirtualReactionForceError) as exc:
        if isinstance(exc, ContactTaskLogError):
            raise
        raise ContactTaskLogError(f"contact task log header is invalid: {exc}") from exc
    binding = _strict_object(
        "contact task log header binding",
        root["binding"],
        {
            "manifest_digest",
            "object_identity",
            "presentation_identity",
            "robot_bundle",
            "scene_identity",
            "signal_manifest_digest",
            "source_kind",
            "trial",
        },
    )
    if binding != header.binding_document:
        raise ContactTaskLogError("contact task log header binding does not match its manifests")
    return header
def _decode_task_state(value: object) -> ContactTaskLogTaskState:
    root = _strict_object(
        "contact task state",
        value,
        {"classification", "phase", "reason"},
    )
    reason = root["reason"]
    if reason is not None and not isinstance(reason, str):
        raise ContactTaskLogError("task state reason must be a string or null")
    try:
        return ContactTaskLogTaskState(
            phase=cast(
                ContactTaskPhase,
                _decode_enum(ContactTaskPhase, "task state phase", root["phase"]),
            ),
            classification=cast(
                TaskTerminalClassification,
                _decode_enum(
                    TaskTerminalClassification,
                    "task state classification",
                    root["classification"],
                ),
            ),
            reason=reason,
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContactTaskLogError):
            raise
        raise ContactTaskLogError(f"contact task state is invalid: {exc}") from exc


def _decode_task_observation(
    value: object,
    *,
    elapsed_time_s: float,
    evidence: ContactEvidence,
) -> ContactTaskObservation:
    root = _strict_object(
        "contact task observation",
        value,
        {
            "contact_location_world_m",
            "object_orientation_wxyz",
            "object_position_world_m",
            "operator_status",
            "reason",
            "tip_position_world_m",
        },
    )
    reason = root["reason"]
    if reason is not None and not isinstance(reason, str):
        raise ContactTaskLogError("task observation reason must be a string or null")
    try:
        return ContactTaskObservation(
            elapsed_time_s=elapsed_time_s,
            contact_evidence=evidence,
            tip_position_world_m=_optional_vector(
                "observation.tip_position_world_m",
                root["tip_position_world_m"],
                length=3,
            ),
            object_position_world_m=_optional_vector(
                "observation.object_position_world_m",
                root["object_position_world_m"],
                length=3,
            ),
            object_orientation_wxyz=_optional_vector(
                "observation.object_orientation_wxyz",
                root["object_orientation_wxyz"],
                length=4,
            ),
            contact_location_world_m=_optional_vector(
                "observation.contact_location_world_m",
                root["contact_location_world_m"],
                length=3,
            ),
            operator_status=cast(
                ContactOperatorStatus,
                _decode_enum(
                    ContactOperatorStatus,
                    "task observation operator_status",
                    root["operator_status"],
                ),
            ),
            reason=reason,
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContactTaskLogError):
            raise
        raise ContactTaskLogError(f"contact task observation is invalid: {exc}") from exc


def _decode_log_sample(
    value: object,
    *,
    header: ContactTaskLogHeader,
    expected_sequence_index: int,
) -> ContactTaskLogSample:
    root = _strict_object(
        "contact task log sample",
        value,
        {
            "binding",
            "derived_reaction_force",
            "elapsed_time_s",
            "raw_contact_evidence",
            "record_kind",
            "schema_version",
            "sequence_index",
            "task_observation",
            "task_state",
        },
    )
    if (
        root["schema_version"] != CONTACT_TASK_LOG_SCHEMA_VERSION
        or root["record_kind"] != "sample"
        or type(root["sequence_index"]) is not int
        or root["sequence_index"] != expected_sequence_index
    ):
        raise ContactTaskLogError("contact task log sample sequence or identity is invalid")
    if root["binding"] != header.binding_document:
        raise ContactTaskLogError("contact task log sample binding does not match its header")
    evidence = _decode_contact_evidence(root["raw_contact_evidence"])
    elapsed_time_s = _finite(
        "task observation elapsed_time_s",
        root["elapsed_time_s"],
        non_negative=True,
    )
    observation = _decode_task_observation(
        root["task_observation"],
        elapsed_time_s=elapsed_time_s,
        evidence=evidence,
    )
    sample = ContactTaskLogSample(
        observation=observation,
        force_signal=_decode_signal(root["derived_reaction_force"]),
        task_state=_decode_task_state(root["task_state"]),
    )
    _validate_sample_bindings(header, sample)
    return sample


def _decode_log_summary(
    value: object,
    *,
    header: ContactTaskLogHeader,
) -> ContactTaskLogSummary:
    root = _strict_object(
        "contact task log summary",
        value,
        {
            "binding",
            "outcome",
            "record_kind",
            "sample_count",
            "schema_version",
        },
    )
    if (
        root["schema_version"] != CONTACT_TASK_LOG_SCHEMA_VERSION
        or root["record_kind"] != "summary"
    ):
        raise ContactTaskLogError("contact task log summary identity is unsupported")
    if root["binding"] != header.binding_document:
        raise ContactTaskLogError("contact task log summary binding does not match its header")
    if type(root["sample_count"]) is not int or root["sample_count"] < 1:
        raise ContactTaskLogError("contact task log summary sample_count is invalid")
    try:
        outcome = ContactTaskOutcome.from_document(root["outcome"])
    except (TypeError, ValueError, ContactTaskContractError) as exc:
        raise ContactTaskLogError(f"contact task outcome is invalid: {exc}") from exc
    return ContactTaskLogSummary(outcome=outcome, sample_count=root["sample_count"])


def _validate_sample_bindings(
    header: ContactTaskLogHeader,
    sample: ContactTaskLogSample,
) -> None:
    manifest = header.context.manifest
    evidence = sample.observation.contact_evidence
    signal = sample.force_signal
    if (
        evidence.manifest_digest != header.context.manifest_digest
        or evidence.scene_identity != manifest.scene.identity
        or evidence.object_identity != manifest.scene.object.identity
    ):
        raise ContactTaskLogError("raw contact evidence identity does not match log header")
    if (
        signal.manifest_digest != header.force_manifest.digest
        or signal.source_contact_manifest_digest != header.context.manifest_digest
        or signal.trial != header.context.trial
        or signal.source_status is not evidence.status
        or signal.output_frame is not header.force_manifest.config.output_frame
    ):
        raise ContactTaskLogError("derived force identity does not match log header or raw evidence")
    if signal.status is VirtualReactionForceStatus.NO_CONTACT:
        if evidence.status is not ContactEvidenceStatus.NO_CONTACT:
            raise ContactTaskLogError("no-contact signal does not match raw evidence")
    elif evidence.status is ContactEvidenceStatus.NO_CONTACT:
        raise ContactTaskLogError("valid no-contact evidence must not carry a nonzero force lifecycle")
    if signal.sample_time_s is not None and signal.sample_time_s != evidence.sample_time_s:
        raise ContactTaskLogError("derived force sample time does not match raw evidence")
    if (
        signal.simulation_time_s is not None
        and signal.simulation_time_s != evidence.simulation_time_s
    ):
        raise ContactTaskLogError("derived force simulation time does not match raw evidence")
    if signal.frame_index is not None and signal.frame_index != evidence.frame_index:
        raise ContactTaskLogError("derived force frame index does not match raw evidence")
    raw_force_world_n = (
        None
        if evidence.aggregate is None
        else evidence.aggregate.object_on_tool_force_world_n
    )
    if (
        signal.raw_force_world_n is not None
        and signal.raw_force_world_n != raw_force_world_n
    ):
        raise ContactTaskLogError(
            "derived raw force does not exactly preserve raw contact evidence"
        )
    if signal.status in {
        VirtualReactionForceStatus.ACTIVE,
        VirtualReactionForceStatus.NO_CONTACT,
    } and signal.raw_force_world_n != raw_force_world_n:
        raise ContactTaskLogError(
            "available derived force must carry the exact raw contact force"
        )


def _validate_final_sample_outcome(
    samples: tuple[ContactTaskLogSample, ...],
    outcome: ContactTaskOutcome,
) -> None:
    final_sample = samples[-1]
    task_state = final_sample.task_state
    if outcome.classification is TaskTerminalClassification.SUCCESS:
        if (
            task_state.classification is not TaskTerminalClassification.SUCCESS
            or task_state.phase is not ContactTaskPhase.SUCCESS
            or task_state.reason is not None
        ):
            raise ContactTaskLogError(
                "successful outcome does not match the final task state"
            )
        evidence = final_sample.observation.contact_evidence
        if (
            evidence.status is not ContactEvidenceStatus.MEASURED
            or not evidence.target_contacts
        ):
            raise ContactTaskLogError(
                "successful outcome requires measured final target contact evidence"
            )
        return
    if (
        task_state.classification is TaskTerminalClassification.RUNNING
        and outcome.classification is TaskTerminalClassification.FAILURE
    ):
        return
    if (
        task_state.phase is not outcome.phase
        or task_state.classification is not outcome.classification
        or task_state.reason != outcome.reason
    ):
        raise ContactTaskLogError(
            "task outcome does not match the final task state"
        )


@dataclass(frozen=True, slots=True)
class ContactTaskLog:
    """header、順序付きsample、raw evidence由来outcomeからなるbinding済みJSONL trial。"""

    header: ContactTaskLogHeader
    samples: tuple[ContactTaskLogSample, ...]
    summary: ContactTaskLogSummary

    def __post_init__(self) -> None:
        if not isinstance(self.header, ContactTaskLogHeader):
            raise TypeError("contact task log header must be typed")
        samples = tuple(self.samples)
        if not samples or any(not isinstance(item, ContactTaskLogSample) for item in samples):
            raise ContactTaskLogError("contact task log requires typed samples")
        object.__setattr__(self, "samples", samples)
        if not isinstance(self.summary, ContactTaskLogSummary):
            raise TypeError("contact task log summary must be typed")
        if self.summary.sample_count != len(samples):
            raise ContactTaskLogError("summary sample count does not match the log")
        outcome = self.summary.outcome
        if (
            outcome.manifest_digest != self.header.context.manifest_digest
            or outcome.trial != self.header.context.trial
            or outcome.observations_count != len(samples)
        ):
            raise ContactTaskLogError("task outcome identity or observation count does not match the log")
        context_conditions = _task_context_document(self.header.context)
        outcome_values = outcome.to_document()
        if any(
            context_conditions[field] != outcome_values[field]
            for field in (
                "dwell_interval_s",
                "timeout_s",
                "target_normal_force_band_n",
                "approach_alignment_min_cosine",
                "normal_alignment_min_cosine",
                "max_contact_location_drift_m",
                "require_pose_measurement",
            )
        ):
            raise ContactTaskLogError("task outcome conditions do not match the log context")
        for sample in samples:
            _validate_sample_bindings(self.header, sample)
        _validate_final_sample_outcome(samples, outcome)

    def to_jsonl(self) -> bytes:
        records = [self.header.to_document()]
        records.extend(
            sample.to_document(header=self.header, sequence_index=index)
            for index, sample in enumerate(self.samples)
        )
        records.append(self.summary.to_document(header=self.header))
        return b"".join(_canonical_json(record) + b"\n" for record in records)


class ContactTaskLogRecorder:
    """atomic artifact書込み前のtrial-scoped append-only in-memory composer。"""

    def __init__(self, header: ContactTaskLogHeader) -> None:
        if not isinstance(header, ContactTaskLogHeader):
            raise TypeError("log recorder requires ContactTaskLogHeader")
        self.header = header
        self._samples: list[ContactTaskLogSample] = []
        self._finalized = False

    def append(
        self,
        observation: ContactTaskObservation,
        force_signal: VirtualReactionForceSignal,
        task_state: ContactTaskLogTaskState,
    ) -> ContactTaskLogSample:
        if self._finalized:
            raise ContactTaskLogError("cannot append to a finalized contact task log")
        sample = ContactTaskLogSample(observation, force_signal, task_state)
        _validate_sample_bindings(self.header, sample)
        self._samples.append(sample)
        return sample

    def finalize(self, outcome: ContactTaskOutcome) -> ContactTaskLog:
        if self._finalized:
            raise ContactTaskLogError("contact task log recorder is already finalized")
        self._finalized = True
        return ContactTaskLog(
            header=self.header,
            samples=tuple(self._samples),
            summary=ContactTaskLogSummary(outcome, len(self._samples)),
        )


def decode_contact_task_log(data: bytes | bytearray | memoryview) -> ContactTaskLog:
    """canonical UTF-8 JSONLと各recordのbindingを厳密にdecodeする。"""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("contact task log decoder requires bytes")
    raw = bytes(data)
    if not raw or raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise ContactTaskLogError("contact task log must be non-empty UTF-8 LF JSONL with a final newline")
    lines = raw[:-1].split(b"\n")
    if len(lines) < 3 or any(not line for line in lines):
        raise ContactTaskLogError("contact task log requires header, samples, and summary records")
    documents: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            value = json.loads(
                line.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeError, json.JSONDecodeError, ContactTaskLogError) as exc:
            raise ContactTaskLogError(f"contact task log line {line_number} is invalid JSON: {exc}") from exc
        if type(value) is not dict or _canonical_json(value) != line:
            raise ContactTaskLogError(f"contact task log line {line_number} is not canonical JSON")
        documents.append(value)
    if documents[0].get("record_kind") != "header" or documents[-1].get("record_kind") != "summary":
        raise ContactTaskLogError("contact task log record order must be header, samples, summary")
    header = _decode_log_header(documents[0])
    samples = tuple(
        _decode_log_sample(
            document,
            header=header,
            expected_sequence_index=index,
        )
        for index, document in enumerate(documents[1:-1])
    )
    summary = _decode_log_summary(documents[-1], header=header)
    log = ContactTaskLog(header, samples, summary)
    if log.to_jsonl() != raw:
        raise ContactTaskLogError("contact task log failed deterministic strict read-back")
    return log


def contact_task_log_artifact_name(trial: ContactTrialIdentity) -> str:
    """binding済みcontact trial向けの決定的でpath-safeなartifact basename。"""

    if not isinstance(trial, ContactTrialIdentity):
        raise TypeError("artifact naming requires ContactTrialIdentity")
    trial_fingerprint = hashlib.sha256(trial.trial_id.encode("utf-8")).hexdigest()[:12]
    return (
        f"contact-task-log-v1-{trial_fingerprint}"
        f"-r{trial.repetition_index:04d}-a{trial.attempt_index:04d}.jsonl"
    )


def read_contact_task_log(path: str | os.PathLike[str]) -> ContactTaskLog:
    return decode_contact_task_log(Path(path).read_bytes())


def write_contact_task_log(
    path: str | os.PathLike[str],
    log: ContactTaskLog,
    *,
    overwrite: bool = False,
) -> Path:
    """厳密なJSONL artifactをatomicに公開し、保存bytesを検証する。既定の排他的hard link公開が使えないfilesystemでは失敗させる。"""

    if not isinstance(log, ContactTaskLog):
        raise TypeError("contact task log writer requires ContactTaskLog")
    destination = Path(path)
    parent = destination.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"contact task log parent directory does not exist: {parent}")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    expected = log.to_jsonl()
    if decode_contact_task_log(expected) != log:
        raise ContactTaskLogError("contact task log failed pre-write read-back")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        stored_temporary = temporary.read_bytes()
        decode_contact_task_log(stored_temporary)
        if stored_temporary != expected:
            raise ContactTaskLogError("temporary contact task log bytes changed before commit")
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        if overwrite:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)
        final_bytes = destination.read_bytes()
        decode_contact_task_log(final_bytes)
        if final_bytes != expected:
            raise ContactTaskLogError("contact task log final read-back differs from deterministic bytes")
        return destination
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
