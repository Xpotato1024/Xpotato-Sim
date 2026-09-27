"""R7-H contact press/hold taskの共有typed contract。

Task lifecycleの状態遷移はTask Pluginが所有する。このmoduleは、manifestへ
bindする実行条件、raw MuJoCo contact observation、canonical outcomeと
terminal evidenceのversioned shapeだけを定義する。forceの再計算、clamp、
reaction-force推定は行わない。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from xpotato_sim.runtime.contact.evidence import (
    ContactEvidence,
    ContactEvidenceStatus,
)
from xpotato_sim.runtime.contact.manifest import (
    CONTACT_TASK_IDENTITY,
    ContactTaskManifest,
    contact_manifest_digest,
)
from xpotato_sim.runtime.experiment.contracts import (
    CanonicalEvidenceSet,
    EvidenceStatus,
    TaskTerminalClassification,
    VersionedIdentity,
)


CONTACT_TASK_SCHEMA_VERSION: Final[str] = "contact-press-hold-task/v1"
CONTACT_TASK_OUTCOME_SCHEMA_VERSION: Final[str] = "contact-task-outcome/v1"
CONTACT_TASK_TERMINAL_EVIDENCE: Final[VersionedIdentity] = VersionedIdentity(
    "contact_press_hold_terminal", 1
)
CONTACT_TASK_OUTCOME_EVIDENCE: Final[VersionedIdentity] = VersionedIdentity(
    "contact_press_hold_outcome", 1
)
CONTACT_TASK_TERMINAL_PROVENANCE: Final[str] = "contact_press_hold_task/v1:terminal"
CONTACT_TASK_OUTCOME_PROVENANCE: Final[str] = "contact_press_hold_task/v1:outcome"
CONTACT_TASK_OUTCOME_IDENTITY: Final[VersionedIdentity] = VersionedIdentity(
    "contact_outcome", 1
)
# Short spelling mirrors the evaluation package identity export.
CONTACT_OUTCOME_IDENTITY: Final[VersionedIdentity] = CONTACT_TASK_OUTCOME_IDENTITY
CONTACT_OUTCOME_PROVENANCE: Final[str] = "contact_outcome/v1:derived"
_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ContactTaskContractError(ValueError):
    """Contact task contractの入力またはcanonical decode failure。"""


class ContactTaskPhase(str, Enum):
    """contact taskのversioned lifecycle phase。"""

    READY = "ready"
    APPROACH = "approach"
    FIRST_CONTACT = "first_contact"
    PRESS = "press"
    HOLD = "hold"
    SUCCESS = "success"
    FAILURE = "failure"
    TECHNICAL_INVALID = "technical_invalid"


class ContactOperatorStatus(str, Enum):
    """operator/input statusをphysical contactから分離した分類。"""

    NOMINAL = "nominal"
    HELD = "held"
    REJECTED = "rejected"
    STALE = "stale"
    TIMEOUT = "timeout"
    RESET_FAILURE = "reset_failure"
    TECHNICAL_INVALID = "technical_invalid"

    # Existing R7-G naming is accepted as a compatibility spelling only.
    RESET = "reset_failure"
    OPERATOR_TIMEOUT = "timeout"


def _finite(name: str, value: object, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContactTaskContractError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ContactTaskContractError(f"{name} must be finite")
    if non_negative and result < 0.0:
        raise ContactTaskContractError(f"{name} must be non-negative")
    return 0.0 if result == 0.0 else result


def _integer(name: str, value: object, *, non_negative: bool = False) -> int:
    if type(value) is not int or (non_negative and value < 0):
        suffix = " non-negative" if non_negative else ""
        raise ContactTaskContractError(f"{name} must be a{suffix} integer")
    return value


def _vector3(name: str, value: object) -> tuple[float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 3
    ):
        raise ContactTaskContractError(f"{name} must contain exactly three numbers")
    return tuple(
        _finite(f"{name}[{index}]", item) for index, item in enumerate(value)
    )  # type: ignore[return-value]


def _quaternion(name: str, value: object) -> tuple[float, float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 4
    ):
        raise ContactTaskContractError(f"{name} must contain exactly four numbers")
    result = tuple(
        _finite(f"{name}[{index}]", item) for index, item in enumerate(value)
    )
    norm = math.sqrt(math.fsum(item * item for item in result))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1e-9:
        raise ContactTaskContractError(f"{name} must be a unit quaternion")
    return result  # type: ignore[return-value]


def _optional_vector3(
    name: str,
    value: object | None,
) -> tuple[float, float, float] | None:
    return None if value is None else _vector3(name, value)


def _optional_finite(name: str, value: object | None) -> float | None:
    return None if value is None else _finite(name, value, non_negative=True)


def _band(
    name: str,
    value: object,
    *,
    non_negative: bool = False,
) -> tuple[float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 2
    ):
        raise ContactTaskContractError(f"{name} must contain two values")
    low = _finite(f"{name}[0]", value[0], non_negative=non_negative)
    high = _finite(f"{name}[1]", value[1], non_negative=non_negative)
    if high < low:
        raise ContactTaskContractError(f"{name} upper bound must not be below lower bound")
    return (low, high)


def _optional_cosine(name: str, value: object | None) -> float | None:
    if value is None:
        return None
    result = _finite(name, value)
    if result < -1.0 or result > 1.0:
        raise ContactTaskContractError(f"{name} must be within [-1, 1]")
    return result


def _stable_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ContactTaskContractError(f"{name} must be a non-empty string")
    if "\x00" in value or value.startswith(("/", "\\")):
        raise ContactTaskContractError(f"{name} must be a stable identifier")
    return value


@dataclass(frozen=True, slots=True)
class ContactTrialIdentity:
    """R7-Gと同じtrial/repetition/attempt境界を持つcontact identity。"""

    trial_id: str
    repetition_index: int = 0
    attempt_index: int = 0
    retry_of_trial_id: str | None = None

    def __post_init__(self) -> None:
        _stable_id("trial.trial_id", self.trial_id)
        _integer("trial.repetition_index", self.repetition_index, non_negative=True)
        _integer("trial.attempt_index", self.attempt_index, non_negative=True)
        if self.retry_of_trial_id is not None:
            _stable_id("trial.retry_of_trial_id", self.retry_of_trial_id)
            if self.retry_of_trial_id == self.trial_id:
                raise ContactTaskContractError(
                    "trial.retry_of_trial_id must differ from trial_id"
                )

    def to_document(self) -> dict[str, object]:
        return {
            "attempt_index": self.attempt_index,
            "repetition_index": self.repetition_index,
            "retry_of_trial_id": self.retry_of_trial_id,
            "trial_id": self.trial_id,
        }


@dataclass(frozen=True, slots=True)
class ContactTaskContext:
    """manifestから一度だけbindするimmutable task conditions。"""

    manifest: ContactTaskManifest
    dwell_interval_s: float
    timeout_s: float
    target_normal_force_band_n: tuple[float, float] | None = None
    approach_alignment_min_cosine: float | None = None
    normal_alignment_min_cosine: float | None = None
    max_contact_location_drift_m: float | None = None
    require_pose_measurement: bool = False
    trial: ContactTrialIdentity = field(
        default_factory=lambda: ContactTrialIdentity("contact-trial-0")
    )

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, ContactTaskManifest):
            raise TypeError("contact task context requires ContactTaskManifest")
        if self.manifest.task_identity != CONTACT_TASK_IDENTITY:
            raise ContactTaskContractError(
                "contact task context manifest must select contact_press_hold_task/v1"
            )
        dwell = _finite("dwell_interval_s", self.dwell_interval_s, non_negative=True)
        timeout = _finite("timeout_s", self.timeout_s, non_negative=True)
        if dwell <= 0.0 or timeout <= 0.0 or dwell > timeout:
            raise ContactTaskContractError(
                "contact task dwell and timeout must be positive, with dwell <= timeout"
            )
        band = self.target_normal_force_band_n
        if band is not None:
            if (
                not isinstance(band, Sequence)
                or isinstance(band, (str, bytes, bytearray))
                or len(band) != 2
            ):
                raise ContactTaskContractError(
                    "target_normal_force_band_n must contain two values"
                )
            low = _finite("target_normal_force_band_n[0]", band[0], non_negative=True)
            high = _finite("target_normal_force_band_n[1]", band[1], non_negative=True)
            if high < low:
                raise ContactTaskContractError(
                    "target normal-force band upper bound must not be below lower bound"
                )
            band = (low, high)
        for name, value in (
            ("approach_alignment_min_cosine", self.approach_alignment_min_cosine),
            ("normal_alignment_min_cosine", self.normal_alignment_min_cosine),
        ):
            if value is not None:
                value = _finite(name, value)
                if value < -1.0 or value > 1.0:
                    raise ContactTaskContractError(f"{name} must be within [-1, 1]")
                object.__setattr__(self, name, value)
        drift = self.max_contact_location_drift_m
        if drift is not None:
            drift = _finite("max_contact_location_drift_m", drift, non_negative=True)
        if type(self.require_pose_measurement) is not bool:
            raise TypeError("require_pose_measurement must be a bool")
        if not isinstance(self.trial, ContactTrialIdentity):
            raise TypeError("contact task context trial must use ContactTrialIdentity")
        object.__setattr__(self, "dwell_interval_s", dwell)
        object.__setattr__(self, "timeout_s", timeout)
        object.__setattr__(self, "target_normal_force_band_n", band)
        object.__setattr__(self, "max_contact_location_drift_m", drift)

    @property
    def manifest_digest(self) -> str:
        return contact_manifest_digest(self.manifest)

    @property
    def target_penetration_band_m(self) -> tuple[float, float]:
        return self.manifest.scene.target.penetration_band_m

    @property
    def target_face(self) -> str:
        return self.manifest.scene.target.face

    @property
    def target_normal_object(self) -> tuple[float, float, float]:
        return self.manifest.scene.target.normal_object

    @property
    def approach_direction_world(self) -> tuple[float, float, float]:
        return self.manifest.scene.target.approach_direction_world


@dataclass(frozen=True, slots=True)
class ContactTaskObservation:
    """Taskへ渡す一時点のraw MuJoCo contact evidenceとmeasured pose。"""

    elapsed_time_s: float
    contact_evidence: ContactEvidence
    tip_position_world_m: tuple[float, float, float] | None = None
    object_position_world_m: tuple[float, float, float] | None = None
    object_orientation_wxyz: tuple[float, float, float, float] | None = None
    contact_location_world_m: tuple[float, float, float] | None = None
    operator_status: ContactOperatorStatus = ContactOperatorStatus.NOMINAL
    reason: str | None = None
    # Compatibility spelling for callers that use the R7-G motion terminology.
    motion_status: ContactOperatorStatus | None = None

    def __post_init__(self) -> None:
        elapsed = _finite("observation.elapsed_time_s", self.elapsed_time_s, non_negative=True)
        if not isinstance(self.contact_evidence, ContactEvidence):
            raise TypeError("contact observation requires raw ContactEvidence")
        if self.contact_evidence.status is ContactEvidenceStatus.MEASURED:
            target_records = self.contact_evidence.target_contacts
            aggregate = self.contact_evidence.aggregate
            if not target_records or aggregate is None:
                raise ContactTaskContractError(
                    "measured contact observation requires target records and aggregate"
                )
            if aggregate.contact_count != len(target_records) or any(
                item.force_status is not ContactEvidenceStatus.MEASURED
                for item in target_records
            ):
                raise ContactTaskContractError(
                    "measured contact observation has inconsistent target force evidence"
                )
        status = self.operator_status
        if self.motion_status is not None:
            if not isinstance(self.motion_status, ContactOperatorStatus):
                raise TypeError("observation.motion_status must be typed")
            if status is not ContactOperatorStatus.NOMINAL and status is not self.motion_status:
                raise ContactTaskContractError(
                    "operator_status and motion_status disagree"
                )
            status = self.motion_status
        if not isinstance(status, ContactOperatorStatus):
            raise TypeError("observation.operator_status must be typed")
        tip = _optional_vector3("observation.tip_position_world_m", self.tip_position_world_m)
        object_position = _optional_vector3(
            "observation.object_position_world_m", self.object_position_world_m
        )
        orientation = (
            None
            if self.object_orientation_wxyz is None
            else _quaternion(
                "observation.object_orientation_wxyz",
                self.object_orientation_wxyz,
            )
        )
        location = _optional_vector3(
            "observation.contact_location_world_m", self.contact_location_world_m
        )
        reason = self.reason
        if self.contact_evidence.status in {
            ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE,
            ContactEvidenceStatus.INVALID_CONTACT,
            ContactEvidenceStatus.SOLVER_INVALID,
        } and reason is None:
            reason = self.contact_evidence.reason
        if status is not ContactOperatorStatus.NOMINAL and (
            not isinstance(reason, str) or not reason.strip()
        ):
            raise ContactTaskContractError(
                "non-nominal contact observation requires a reason"
            )
        if self.contact_evidence.status is not ContactEvidenceStatus.MEASURED and (
            self.contact_evidence.status is not ContactEvidenceStatus.NO_CONTACT
            and (not isinstance(reason, str) or not reason.strip())
        ):
            raise ContactTaskContractError(
                "failed contact observation requires a reason"
            )
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ContactTaskContractError("observation reason must be non-empty or null")
        object.__setattr__(self, "elapsed_time_s", elapsed)
        object.__setattr__(self, "tip_position_world_m", tip)
        object.__setattr__(self, "object_position_world_m", object_position)
        object.__setattr__(self, "object_orientation_wxyz", orientation)
        object.__setattr__(self, "contact_location_world_m", location)
        object.__setattr__(self, "operator_status", status)
        object.__setattr__(self, "motion_status", status)
        object.__setattr__(self, "reason", reason)

    @property
    def has_target_contact(self) -> bool:
        return (
            self.contact_evidence.status is ContactEvidenceStatus.MEASURED
            and self.contact_evidence.has_target_contact
        )


@dataclass(frozen=True, slots=True)
class ContactTaskOutcome:
    """Task transitionから再計算可能なdeterministic outcome artifact。"""

    manifest_digest: str
    trial: ContactTrialIdentity
    dwell_interval_s: float
    timeout_s: float
    target_penetration_band_m: tuple[float, float]
    target_normal_force_band_n: tuple[float, float] | None
    approach_alignment_min_cosine: float | None
    normal_alignment_min_cosine: float | None
    max_contact_location_drift_m: float | None
    require_pose_measurement: bool
    phase: ContactTaskPhase
    classification: TaskTerminalClassification
    reason: str | None
    terminal_time_s: float | None
    completion_time_s: float | None
    first_contact_time_s: float | None
    peak_normal_force_n: float | None
    max_penetration_m: float | None
    overshoot_m: float | None
    steady_state_error_m: float | None
    force_variability_n: float | None
    peak_tangential_force_n: float | None
    slip_proxy_m: float | None
    contact_loss_count: int
    recontact_count: int
    final_tip_position_world_m: tuple[float, float, float] | None
    final_object_position_world_m: tuple[float, float, float] | None
    final_object_orientation_wxyz: tuple[float, float, float, float] | None
    final_contact_location_world_m: tuple[float, float, float] | None
    contact_location_drift_m: float | None
    final_normal_alignment_cosine: float | None
    observations_count: int
    schema_version: str = CONTACT_TASK_OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTACT_TASK_OUTCOME_SCHEMA_VERSION:
            raise ContactTaskContractError("unsupported contact task outcome schema")
        if not isinstance(self.manifest_digest, str) or not _DIGEST_PATTERN.fullmatch(
            self.manifest_digest
        ):
            raise ContactTaskContractError("outcome manifest digest is invalid")
        if not isinstance(self.trial, ContactTrialIdentity):
            raise TypeError("outcome trial must use ContactTrialIdentity")
        dwell = _finite("outcome.dwell_interval_s", self.dwell_interval_s, non_negative=True)
        timeout = _finite("outcome.timeout_s", self.timeout_s, non_negative=True)
        if dwell <= 0.0 or timeout <= 0.0 or dwell > timeout:
            raise ContactTaskContractError(
                "outcome dwell and timeout must be positive, with dwell <= timeout"
            )
        penetration_band = _band(
            "outcome.target_penetration_band_m",
            self.target_penetration_band_m,
            non_negative=True,
        )
        force_band = (
            None
            if self.target_normal_force_band_n is None
            else _band(
                "outcome.target_normal_force_band_n",
                self.target_normal_force_band_n,
                non_negative=True,
            )
        )
        approach_alignment = _optional_cosine(
            "outcome.approach_alignment_min_cosine",
            self.approach_alignment_min_cosine,
        )
        normal_alignment = _optional_cosine(
            "outcome.normal_alignment_min_cosine",
            self.normal_alignment_min_cosine,
        )
        drift = _optional_finite(
            "outcome.max_contact_location_drift_m",
            self.max_contact_location_drift_m,
        )
        if type(self.require_pose_measurement) is not bool:
            raise TypeError("outcome.require_pose_measurement must be a bool")
        object.__setattr__(self, "dwell_interval_s", dwell)
        object.__setattr__(self, "timeout_s", timeout)
        object.__setattr__(self, "target_penetration_band_m", penetration_band)
        object.__setattr__(self, "target_normal_force_band_n", force_band)
        object.__setattr__(self, "approach_alignment_min_cosine", approach_alignment)
        object.__setattr__(self, "normal_alignment_min_cosine", normal_alignment)
        object.__setattr__(self, "max_contact_location_drift_m", drift)
        if not isinstance(self.phase, ContactTaskPhase):
            raise TypeError("outcome phase must use ContactTaskPhase")
        if not isinstance(self.classification, TaskTerminalClassification):
            raise TypeError("outcome classification must use TaskTerminalClassification")
        if self.classification is TaskTerminalClassification.SUCCESS and self.phase is not ContactTaskPhase.SUCCESS:
            raise ContactTaskContractError("success outcome must use success phase")
        if self.classification is TaskTerminalClassification.FAILURE and self.phase is not ContactTaskPhase.FAILURE:
            raise ContactTaskContractError("failure outcome must use failure phase")
        if self.classification is TaskTerminalClassification.TECHNICAL_INVALID and self.phase is not ContactTaskPhase.TECHNICAL_INVALID:
            raise ContactTaskContractError(
                "technical-invalid outcome must use technical-invalid phase"
            )
        if self.classification is TaskTerminalClassification.RUNNING and self.phase in {
            ContactTaskPhase.SUCCESS,
            ContactTaskPhase.FAILURE,
            ContactTaskPhase.TECHNICAL_INVALID,
        }:
            raise ContactTaskContractError("running outcome must use a non-terminal phase")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ContactTaskContractError("outcome reason must be non-empty or null")
        if self.classification in {
            TaskTerminalClassification.FAILURE,
            TaskTerminalClassification.TECHNICAL_INVALID,
        } and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ContactTaskContractError(
                "failure or technical-invalid outcome requires a reason"
            )
        terminal = _optional_finite("outcome.terminal_time_s", self.terminal_time_s)
        completion = _optional_finite("outcome.completion_time_s", self.completion_time_s)
        first_contact = _optional_finite(
            "outcome.first_contact_time_s", self.first_contact_time_s
        )
        if self.classification is TaskTerminalClassification.SUCCESS and completion is None:
            raise ContactTaskContractError("success outcome requires completion_time_s")
        if self.classification is not TaskTerminalClassification.SUCCESS and completion is not None:
            raise ContactTaskContractError(
                "non-success outcome must not carry completion_time_s"
            )
        scalar_names = (
            "peak_normal_force_n",
            "max_penetration_m",
            "overshoot_m",
            "steady_state_error_m",
            "force_variability_n",
            "peak_tangential_force_n",
            "slip_proxy_m",
            "contact_location_drift_m",
            "final_normal_alignment_cosine",
        )
        for name in scalar_names:
            value = getattr(self, name)
            if value is not None:
                finite = _finite(f"outcome.{name}", value, non_negative=name != "final_normal_alignment_cosine")
                if name == "final_normal_alignment_cosine" and not -1.0 <= finite <= 1.0:
                    raise ContactTaskContractError(
                        "outcome.final_normal_alignment_cosine must be within [-1, 1]"
                    )
                object.__setattr__(self, name, finite)
        for name, value in (
            ("final_tip_position_world_m", self.final_tip_position_world_m),
            ("final_object_position_world_m", self.final_object_position_world_m),
            ("final_contact_location_world_m", self.final_contact_location_world_m),
        ):
            object.__setattr__(self, name, _optional_vector3(f"outcome.{name}", value))
        if self.final_object_orientation_wxyz is not None:
            object.__setattr__(
                self,
                "final_object_orientation_wxyz",
                _quaternion(
                    "outcome.final_object_orientation_wxyz",
                    self.final_object_orientation_wxyz,
                ),
            )
        object.__setattr__(self, "terminal_time_s", terminal)
        object.__setattr__(self, "completion_time_s", completion)
        object.__setattr__(self, "first_contact_time_s", first_contact)
        object.__setattr__(
            self,
            "contact_loss_count",
            _integer("outcome.contact_loss_count", self.contact_loss_count, non_negative=True),
        )
        object.__setattr__(
            self,
            "recontact_count",
            _integer("outcome.recontact_count", self.recontact_count, non_negative=True),
        )
        object.__setattr__(
            self,
            "observations_count",
            _integer("outcome.observations_count", self.observations_count, non_negative=True),
        )

    @staticmethod
    def _optional_document(value: Sequence[float] | None) -> list[float] | None:
        return None if value is None else list(value)

    def to_document(self) -> dict[str, object]:
        return {
            "approach_alignment_min_cosine": self.approach_alignment_min_cosine,
            "classification": self.classification.value,
            "completion_time_s": self.completion_time_s,
            "contact_location_drift_m": self.contact_location_drift_m,
            "contact_loss_count": self.contact_loss_count,
            "dwell_interval_s": self.dwell_interval_s,
            "final_contact_location_world_m": self._optional_document(
                self.final_contact_location_world_m
            ),
            "final_normal_alignment_cosine": self.final_normal_alignment_cosine,
            "final_object_orientation_wxyz": self._optional_document(
                self.final_object_orientation_wxyz
            ),
            "final_object_position_world_m": self._optional_document(
                self.final_object_position_world_m
            ),
            "final_tip_position_world_m": self._optional_document(
                self.final_tip_position_world_m
            ),
            "first_contact_time_s": self.first_contact_time_s,
            "force_variability_n": self.force_variability_n,
            "manifest_digest": self.manifest_digest,
            "max_penetration_m": self.max_penetration_m,
            "max_contact_location_drift_m": self.max_contact_location_drift_m,
            "normal_alignment_min_cosine": self.normal_alignment_min_cosine,
            "observations_count": self.observations_count,
            "overshoot_m": self.overshoot_m,
            "peak_normal_force_n": self.peak_normal_force_n,
            "peak_tangential_force_n": self.peak_tangential_force_n,
            "phase": self.phase.value,
            "reason": self.reason,
            "recontact_count": self.recontact_count,
            "schema_version": self.schema_version,
            "slip_proxy_m": self.slip_proxy_m,
            "steady_state_error_m": self.steady_state_error_m,
            "terminal_time_s": self.terminal_time_s,
            "target_normal_force_band_n": self._optional_document(
                self.target_normal_force_band_n
            ),
            "target_penetration_band_m": self._optional_document(
                self.target_penetration_band_m
            ),
            "trial": self.trial.to_document(),
            "require_pose_measurement": self.require_pose_measurement,
            "timeout_s": self.timeout_s,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_document(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_document(cls, value: object) -> "ContactTaskOutcome":
        if not isinstance(value, Mapping):
            raise ContactTaskContractError("contact task outcome must be an object")
        expected = {
            "approach_alignment_min_cosine",
            "classification",
            "completion_time_s",
            "contact_location_drift_m",
            "contact_loss_count",
            "dwell_interval_s",
            "final_contact_location_world_m",
            "final_normal_alignment_cosine",
            "final_object_orientation_wxyz",
            "final_object_position_world_m",
            "final_tip_position_world_m",
            "first_contact_time_s",
            "force_variability_n",
            "manifest_digest",
            "max_contact_location_drift_m",
            "max_penetration_m",
            "normal_alignment_min_cosine",
            "observations_count",
            "overshoot_m",
            "peak_normal_force_n",
            "peak_tangential_force_n",
            "phase",
            "reason",
            "recontact_count",
            "schema_version",
            "slip_proxy_m",
            "steady_state_error_m",
            "terminal_time_s",
            "target_normal_force_band_n",
            "target_penetration_band_m",
            "trial",
            "require_pose_measurement",
            "timeout_s",
        }
        if set(value) != expected:
            raise ContactTaskContractError(
                "contact task outcome fields must be exactly "
                f"{sorted(expected)!r}"
            )
        trial_root = value["trial"]
        if not isinstance(trial_root, Mapping) or set(trial_root) != {
            "attempt_index",
            "repetition_index",
            "retry_of_trial_id",
            "trial_id",
        }:
            raise ContactTaskContractError("outcome trial identity is invalid")
        try:
            trial = ContactTrialIdentity(
                trial_id=trial_root["trial_id"],  # type: ignore[arg-type]
                repetition_index=trial_root["repetition_index"],  # type: ignore[arg-type]
                attempt_index=trial_root["attempt_index"],  # type: ignore[arg-type]
                retry_of_trial_id=trial_root["retry_of_trial_id"],  # type: ignore[arg-type]
            )
            phase = ContactTaskPhase(value["phase"])
            classification = TaskTerminalClassification(value["classification"])
        except (TypeError, ValueError) as exc:
            raise ContactTaskContractError("outcome identity or classification is invalid") from exc
        return cls(
            manifest_digest=value["manifest_digest"],  # type: ignore[arg-type]
            trial=trial,
            dwell_interval_s=value["dwell_interval_s"],  # type: ignore[arg-type]
            timeout_s=value["timeout_s"],  # type: ignore[arg-type]
            target_penetration_band_m=value["target_penetration_band_m"],  # type: ignore[arg-type]
            target_normal_force_band_n=value["target_normal_force_band_n"],  # type: ignore[arg-type]
            approach_alignment_min_cosine=value["approach_alignment_min_cosine"],  # type: ignore[arg-type]
            normal_alignment_min_cosine=value["normal_alignment_min_cosine"],  # type: ignore[arg-type]
            max_contact_location_drift_m=value["max_contact_location_drift_m"],  # type: ignore[arg-type]
            require_pose_measurement=value["require_pose_measurement"],  # type: ignore[arg-type]
            phase=phase,
            classification=classification,
            reason=value["reason"],  # type: ignore[arg-type]
            terminal_time_s=value["terminal_time_s"],  # type: ignore[arg-type]
            completion_time_s=value["completion_time_s"],  # type: ignore[arg-type]
            first_contact_time_s=value["first_contact_time_s"],  # type: ignore[arg-type]
            peak_normal_force_n=value["peak_normal_force_n"],  # type: ignore[arg-type]
            max_penetration_m=value["max_penetration_m"],  # type: ignore[arg-type]
            overshoot_m=value["overshoot_m"],  # type: ignore[arg-type]
            steady_state_error_m=value["steady_state_error_m"],  # type: ignore[arg-type]
            force_variability_n=value["force_variability_n"],  # type: ignore[arg-type]
            peak_tangential_force_n=value["peak_tangential_force_n"],  # type: ignore[arg-type]
            slip_proxy_m=value["slip_proxy_m"],  # type: ignore[arg-type]
            contact_loss_count=value["contact_loss_count"],  # type: ignore[arg-type]
            recontact_count=value["recontact_count"],  # type: ignore[arg-type]
            final_tip_position_world_m=value["final_tip_position_world_m"],  # type: ignore[arg-type]
            final_object_position_world_m=value["final_object_position_world_m"],  # type: ignore[arg-type]
            final_object_orientation_wxyz=value["final_object_orientation_wxyz"],  # type: ignore[arg-type]
            final_contact_location_world_m=value["final_contact_location_world_m"],  # type: ignore[arg-type]
            contact_location_drift_m=value["contact_location_drift_m"],  # type: ignore[arg-type]
            final_normal_alignment_cosine=value["final_normal_alignment_cosine"],  # type: ignore[arg-type]
            observations_count=value["observations_count"],  # type: ignore[arg-type]
            schema_version=value["schema_version"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class ContactTaskTerminalEvidence:
    """Task-owned terminal classificationのstrict decode result。"""

    classification: TaskTerminalClassification
    phase: ContactTaskPhase
    terminal_time_s: float | None
    completion_time_s: float | None
    reason: str | None
    manifest_digest: str
    trial: ContactTrialIdentity


def decode_contact_task_terminal_evidence(
    evidence: CanonicalEvidenceSet,
) -> ContactTaskTerminalEvidence:
    """Task-owned terminal evidenceをprovenance付きでstrictにdecodeする。"""

    entry = evidence.require(CONTACT_TASK_TERMINAL_EVIDENCE)
    if entry.status is not EvidenceStatus.MEASURED:
        raise ContactTaskContractError("contact task terminal evidence must be measured")
    if entry.provenance != CONTACT_TASK_TERMINAL_PROVENANCE:
        raise ContactTaskContractError("contact task terminal evidence producer is invalid")
    value = entry.value
    expected = {
        "classification",
        "completion_time_s",
        "manifest_digest",
        "phase",
        "reason",
        "terminal_time_s",
        "trial",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ContactTaskContractError("contact task terminal evidence shape is invalid")
    try:
        classification = TaskTerminalClassification(value["classification"])
        phase = ContactTaskPhase(value["phase"])
        trial_root = value["trial"]
        if not isinstance(trial_root, Mapping) or set(trial_root) != {
            "attempt_index",
            "repetition_index",
            "retry_of_trial_id",
            "trial_id",
        }:
            raise ContactTaskContractError("terminal trial identity is invalid")
        trial = ContactTrialIdentity(
            trial_id=trial_root["trial_id"],  # type: ignore[arg-type]
            repetition_index=trial_root["repetition_index"],  # type: ignore[arg-type]
            attempt_index=trial_root["attempt_index"],  # type: ignore[arg-type]
            retry_of_trial_id=trial_root["retry_of_trial_id"],  # type: ignore[arg-type]
        )
        terminal_time = _optional_finite("terminal.terminal_time_s", value["terminal_time_s"])
        completion = _optional_finite(
            "terminal.completion_time_s", value["completion_time_s"]
        )
        digest = value["manifest_digest"]
        if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
            raise ContactTaskContractError("terminal manifest digest is invalid")
        reason = value["reason"]
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ContactTaskContractError("terminal reason must be non-empty or null")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContactTaskContractError):
            raise
        raise ContactTaskContractError("contact task terminal evidence value is invalid") from exc
    if classification is TaskTerminalClassification.SUCCESS and completion is None:
        raise ContactTaskContractError("successful terminal evidence requires completion time")
    if classification is not TaskTerminalClassification.SUCCESS and completion is not None:
        raise ContactTaskContractError("non-success terminal evidence must not carry completion time")
    expected_phase = {
        TaskTerminalClassification.RUNNING: {
            ContactTaskPhase.READY,
            ContactTaskPhase.APPROACH,
            ContactTaskPhase.FIRST_CONTACT,
            ContactTaskPhase.PRESS,
            ContactTaskPhase.HOLD,
        },
        TaskTerminalClassification.SUCCESS: {ContactTaskPhase.SUCCESS},
        TaskTerminalClassification.FAILURE: {ContactTaskPhase.FAILURE},
        TaskTerminalClassification.TECHNICAL_INVALID: {
            ContactTaskPhase.TECHNICAL_INVALID
        },
    }[classification]
    if phase not in expected_phase:
        raise ContactTaskContractError(
            "contact task terminal classification and phase disagree"
        )
    if classification in {
        TaskTerminalClassification.FAILURE,
        TaskTerminalClassification.TECHNICAL_INVALID,
    } and (not isinstance(reason, str) or not reason.strip()):
        raise ContactTaskContractError(
            "failed or technical-invalid terminal evidence requires a reason"
        )
    return ContactTaskTerminalEvidence(
        classification=classification,
        phase=phase,
        terminal_time_s=terminal_time,
        completion_time_s=completion,
        reason=reason,
        manifest_digest=digest,
        trial=trial,
    )


__all__ = [
    "CONTACT_OUTCOME_IDENTITY",
    "CONTACT_OUTCOME_PROVENANCE",
    "CONTACT_TASK_OUTCOME_EVIDENCE",
    "CONTACT_TASK_OUTCOME_IDENTITY",
    "CONTACT_TASK_OUTCOME_PROVENANCE",
    "CONTACT_TASK_OUTCOME_SCHEMA_VERSION",
    "CONTACT_TASK_SCHEMA_VERSION",
    "CONTACT_TASK_TERMINAL_EVIDENCE",
    "CONTACT_TASK_TERMINAL_PROVENANCE",
    "CONTACT_TASK_IDENTITY",
    "ContactOperatorStatus",
    "ContactTaskContext",
    "ContactTaskContractError",
    "ContactTaskObservation",
    "ContactTaskOutcome",
    "ContactTaskPhase",
    "ContactTaskTerminalEvidence",
    "ContactTrialIdentity",
    "decode_contact_task_terminal_evidence",
]
