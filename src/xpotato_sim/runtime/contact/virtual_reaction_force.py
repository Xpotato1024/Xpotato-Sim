"""Contact evidenceから導出するsoftware-only仮想反力signal。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
from xpotato_sim.runtime.contact.evidence import (
    CONTACT_EVIDENCE_IDENTITY,
    ContactEvidence,
    ContactEvidenceStatus,
)
from xpotato_sim.runtime.contact.manifest import (
    ContactTaskManifest,
    contact_manifest_digest,
)
from xpotato_sim.runtime.contact.task_contract import ContactTrialIdentity


VIRTUAL_REACTION_FORCE_SCHEMA_VERSION: Final[str] = "virtual-reaction-force/v1"
VIRTUAL_REACTION_FORCE_CONTRACT_VERSION: Final[int] = 1
VIRTUAL_REACTION_FORCE_IDENTITY: Final[VersionedIdentity] = VersionedIdentity(
    "virtual_reaction_force", 1
)
VIRTUAL_REACTION_FORCE_PROVENANCE: Final[str] = "contact_evidence_derivation/v1"
VIRTUAL_REACTION_FORCE_UNIT: Final[str] = "newton"
VIRTUAL_REACTION_FORCE_SIGN_CONVENTION: Final[str] = "object_on_tool"
VIRTUAL_REACTION_FORCE_SOURCE_FIELD: Final[str] = (
    "aggregate.object_on_tool_force_world_n"
)
VIRTUAL_REACTION_FORCE_INPUT_FRAME: Final[str] = "mujoco_world"
VIRTUAL_REACTION_FORCE_DIGEST_ALGORITHM: Final[str] = "sha256"

_MANIFEST_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"sha256:[0-9a-f]{64}\Z"
)
_ROTATION_TOLERANCE: Final[float] = 1e-8
_ZERO3: Final[tuple[float, float, float]] = (0.0, 0.0, 0.0)
_IDENTITY_ROTATION: Final[tuple[float, ...]] = (
    1.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    1.0,
)
_FILTER_ORDER: Final[tuple[str, ...]] = (
    "deadband",
    "moving_average",
    "one_pole_low_pass",
    "vector_rate_limit",
    "magnitude_clamp",
)


class VirtualReactionForceError(ValueError):
    """仮想反力manifestまたはsignalのvalidation failure。"""


class VirtualReactionForceFrame(str, Enum):
    """仮想反力signalのdevice-neutralなvector frame。"""

    MUJOCO_WORLD = "mujoco_world"
    TOOL = "tool"
    DEVICE_NEUTRAL = "device_neutral"


class VirtualReactionForceStatus(str, Enum):
    """導出signalの閉じたlifecycle。"""

    ACTIVE = "active"
    NO_CONTACT = "no_contact"
    MEASUREMENT_UNAVAILABLE = "measurement_unavailable"
    INVALID = "invalid"
    STALE = "stale"


def _finite(name: str, value: object, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VirtualReactionForceError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise VirtualReactionForceError(f"{name} must be finite")
    if non_negative and result < 0.0:
        raise VirtualReactionForceError(f"{name} must be non-negative")
    return 0.0 if result == 0.0 else result


def _vector3(name: str, value: object) -> tuple[float, float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 3
    ):
        raise VirtualReactionForceError(
            f"{name} must contain exactly three finite numbers"
        )
    values = tuple(_finite(f"{name}[{index}]", item) for index, item in enumerate(value))
    return values  # type: ignore[return-value]


def _identity_document(value: VersionedIdentity) -> dict[str, object]:
    return {"name": value.name, "version": value.version}


def _digest(name: str, value: object) -> str:
    if not isinstance(value, str) or not _MANIFEST_DIGEST_PATTERN.fullmatch(value):
        raise VirtualReactionForceError(f"{name} is invalid")
    return value


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum(left[index] * right[index] for index in range(3))


def _magnitude(value: Sequence[float]) -> float:
    return math.hypot(value[0], value[1], value[2])


def _clean_vector(value: Sequence[float]) -> tuple[float, float, float]:
    result = _vector3("force vector", value)
    return tuple(0.0 if item == 0.0 else item for item in result)  # type: ignore[return-value]


def _rotation3(name: str, value: object) -> tuple[float, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 9
    ):
        raise VirtualReactionForceError(
            f"{name} must contain nine finite row-major values"
        )
    matrix = tuple(
        _finite(f"{name}[{index}]", item) for index, item in enumerate(value)
    )
    rows = (matrix[0:3], matrix[3:6], matrix[6:9])
    try:
        for row_index, row in enumerate(rows):
            if abs(_dot(row, row) - 1.0) > _ROTATION_TOLERANCE:
                raise VirtualReactionForceError(
                    f"{name} rows must be unit length"
                )
            for other_index in range(row_index + 1, 3):
                if abs(_dot(row, rows[other_index])) > _ROTATION_TOLERANCE:
                    raise VirtualReactionForceError(
                        f"{name} rows must be orthogonal"
                    )
        determinant = (
            rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
            - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
            + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
        )
    except (OverflowError, ValueError) as exc:
        raise VirtualReactionForceError(f"{name} is numerically invalid") from exc
    if not math.isfinite(determinant) or abs(determinant - 1.0) > _ROTATION_TOLERANCE:
        raise VirtualReactionForceError(f"{name} must be a right-handed rotation")
    return matrix


def _apply_rotation(
    rotation: Sequence[float],
    vector: Sequence[float],
) -> tuple[float, float, float]:
    try:
        transformed = tuple(
            math.fsum(
                rotation[row * 3 + column] * vector[column]
                for column in range(3)
            )
            for row in range(3)
        )
    except (OverflowError, ValueError) as exc:
        raise VirtualReactionForceError(
            "world-to-output transform overflowed"
        ) from exc
    return _clean_vector(transformed)


@dataclass(frozen=True, slots=True)
class VirtualReactionForceConfig:
    """signal導出に必要な全parameterを明示するimmutable設定。"""

    output_frame: VirtualReactionForceFrame
    deadband_n: float
    low_pass_time_constant_s: float
    smoothing_window_samples: int
    rate_limit_n_per_s: float | None
    magnitude_clamp_n: float | None
    max_inter_sample_gap_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.output_frame, VirtualReactionForceFrame):
            raise TypeError("output_frame must use VirtualReactionForceFrame")
        deadband = _finite("deadband_n", self.deadband_n, non_negative=True)
        low_pass = _finite(
            "low_pass_time_constant_s",
            self.low_pass_time_constant_s,
            non_negative=True,
        )
        if type(self.smoothing_window_samples) is not int:
            raise TypeError("smoothing_window_samples must be an integer")
        if self.smoothing_window_samples < 1:
            raise ValueError("smoothing_window_samples must be positive")
        if self.rate_limit_n_per_s is None:
            rate_limit = None
        else:
            rate_limit = _finite("rate_limit_n_per_s", self.rate_limit_n_per_s)
            if rate_limit <= 0.0:
                raise ValueError("rate_limit_n_per_s must be positive or null")
        if self.magnitude_clamp_n is None:
            magnitude_clamp = None
        else:
            magnitude_clamp = _finite("magnitude_clamp_n", self.magnitude_clamp_n)
            if magnitude_clamp <= 0.0:
                raise ValueError("magnitude_clamp_n must be positive or null")
        max_gap = _finite(
            "max_inter_sample_gap_s",
            self.max_inter_sample_gap_s,
        )
        if max_gap <= 0.0:
            raise ValueError("max_inter_sample_gap_s must be positive")
        object.__setattr__(self, "deadband_n", deadband)
        object.__setattr__(self, "low_pass_time_constant_s", low_pass)
        object.__setattr__(self, "rate_limit_n_per_s", rate_limit)
        object.__setattr__(self, "magnitude_clamp_n", magnitude_clamp)
        object.__setattr__(self, "max_inter_sample_gap_s", max_gap)

    def to_document(self) -> dict[str, object]:
        return {
            "deadband_n": self.deadband_n,
            "low_pass_time_constant_s": self.low_pass_time_constant_s,
            "magnitude_clamp_n": self.magnitude_clamp_n,
            "max_inter_sample_gap_s": self.max_inter_sample_gap_s,
            "output_frame": self.output_frame.value,
            "rate_limit_n_per_s": self.rate_limit_n_per_s,
            "smoothing_window_samples": self.smoothing_window_samples,
        }


@dataclass(frozen=True, slots=True)
class VirtualReactionForceManifest:
    """raw contact manifestとsignal processing policyのversioned identity。"""

    contact_manifest: ContactTaskManifest
    config: VirtualReactionForceConfig
    schema_version: str = VIRTUAL_REACTION_FORCE_SCHEMA_VERSION
    contract_version: int = VIRTUAL_REACTION_FORCE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.contact_manifest, ContactTaskManifest):
            raise TypeError("contact_manifest must use ContactTaskManifest")
        if not isinstance(self.config, VirtualReactionForceConfig):
            raise TypeError("config must use VirtualReactionForceConfig")
        if self.schema_version != VIRTUAL_REACTION_FORCE_SCHEMA_VERSION:
            raise ValueError("unsupported virtual reaction-force schema version")
        if (
            type(self.contract_version) is not int
            or self.contract_version != VIRTUAL_REACTION_FORCE_CONTRACT_VERSION
        ):
            raise ValueError("unsupported virtual reaction-force contract version")

    @property
    def source_contact_manifest_digest(self) -> str:
        return contact_manifest_digest(self.contact_manifest)

    @property
    def digest(self) -> str:
        value = hashlib.sha256(self.canonical_bytes()).hexdigest()
        return f"{VIRTUAL_REACTION_FORCE_DIGEST_ALGORITHM}:{value}"

    def to_document(self) -> dict[str, object]:
        output_frame = self.config.output_frame
        transform_policy = (
            "identity_world_frame/v1"
            if output_frame is VirtualReactionForceFrame.MUJOCO_WORLD
            else "caller_supplied_world_to_output_rotation_per_sample/v1"
        )
        return {
            "contract_version": self.contract_version,
            "filter_initial_state_policy": "first_valid_sample_seeds_filters/v1",
            "filter_order": list(_FILTER_ORDER),
            "force_source": {
                "field": VIRTUAL_REACTION_FORCE_SOURCE_FIELD,
                "frame": VIRTUAL_REACTION_FORCE_INPUT_FRAME,
                "identity": _identity_document(CONTACT_EVIDENCE_IDENTITY),
                "sign_convention": VIRTUAL_REACTION_FORCE_SIGN_CONVENTION,
                "unit": VIRTUAL_REACTION_FORCE_UNIT,
            },
            "identity": _identity_document(VIRTUAL_REACTION_FORCE_IDENTITY),
            "no_contact_policy": "status_zero_and_clear_filter_state/v1",
            "output_frame_transform_policy": transform_policy,
            "provenance": VIRTUAL_REACTION_FORCE_PROVENANCE,
            "schema_version": self.schema_version,
            "source_contact_manifest_digest": self.source_contact_manifest_digest,
            "source_object_identity": _identity_document(
                self.contact_manifest.object.identity
            ),
            "source_scene_identity": _identity_document(
                self.contact_manifest.scene.identity
            ),
            "stale_policy": (
                "gap_over_configured_limit_invalidates_signal_and_clears_filters/v1"
            ),
            "trial_boundary_policy": "trial_identity_change_clears_filters/v1",
            "config": self.config.to_document(),
        }

    def canonical_bytes(self) -> bytes:
        try:
            return json.dumps(
                self.to_document(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise VirtualReactionForceError(
                f"virtual reaction-force manifest serialization failed: {exc}"
            ) from exc


def encode_virtual_reaction_force_manifest(
    manifest: VirtualReactionForceManifest,
) -> bytes:
    """manifestをdeterministic UTF-8 JSONへencodeする。"""

    if not isinstance(manifest, VirtualReactionForceManifest):
        raise TypeError(
            "encode_virtual_reaction_force_manifest requires "
            "VirtualReactionForceManifest"
        )
    return manifest.canonical_bytes()

def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VirtualReactionForceError(
                f"duplicate JSON object key: {key}"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise VirtualReactionForceError(f"invalid JSON numeric constant: {value}")


def _require_object_keys(
    name: str,
    value: object,
    expected: set[str],
) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise VirtualReactionForceError(f"{name} has missing or unexpected fields")
    return value


def decode_virtual_reaction_force_manifest(
    data: bytes,
    *,
    contact_manifest: ContactTaskManifest,
) -> VirtualReactionForceManifest:
    """canonical manifestをraw contact manifestへ照合してstrict decodeする。"""

    if type(data) is not bytes:
        raise TypeError("virtual reaction-force manifest must use built-in bytes")
    if not isinstance(contact_manifest, ContactTaskManifest):
        raise TypeError("contact_manifest must use ContactTaskManifest")
    try:
        document = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VirtualReactionForceError(
            "virtual reaction-force manifest is not valid UTF-8 JSON"
        ) from exc
    root = _require_object_keys(
        "virtual reaction-force manifest",
        document,
        {
            "contract_version",
            "filter_initial_state_policy",
            "filter_order",
            "force_source",
            "identity",
            "no_contact_policy",
            "output_frame_transform_policy",
            "provenance",
            "schema_version",
            "source_contact_manifest_digest",
            "source_object_identity",
            "source_scene_identity",
            "stale_policy",
            "trial_boundary_policy",
            "config",
        },
    )
    config_doc = _require_object_keys(
        "virtual reaction-force config",
        root["config"],
        {
            "deadband_n",
            "low_pass_time_constant_s",
            "magnitude_clamp_n",
            "max_inter_sample_gap_s",
            "output_frame",
            "rate_limit_n_per_s",
            "smoothing_window_samples",
        },
    )
    try:
        config = VirtualReactionForceConfig(
            output_frame=VirtualReactionForceFrame(config_doc["output_frame"]),
            deadband_n=config_doc["deadband_n"],  # type: ignore[arg-type]
            low_pass_time_constant_s=config_doc[
                "low_pass_time_constant_s"
            ],  # type: ignore[arg-type]
            smoothing_window_samples=config_doc[
                "smoothing_window_samples"
            ],  # type: ignore[arg-type]
            rate_limit_n_per_s=config_doc[
                "rate_limit_n_per_s"
            ],  # type: ignore[arg-type]
            magnitude_clamp_n=config_doc[
                "magnitude_clamp_n"
            ],  # type: ignore[arg-type]
            max_inter_sample_gap_s=config_doc[
                "max_inter_sample_gap_s"
            ],  # type: ignore[arg-type]
        )
        manifest = VirtualReactionForceManifest(
            contact_manifest=contact_manifest,
            config=config,
            schema_version=root["schema_version"],  # type: ignore[arg-type]
            contract_version=root["contract_version"],  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        raise VirtualReactionForceError(
            f"virtual reaction-force manifest is invalid: {exc}"
        ) from exc
    if manifest.to_document() != document:
        raise VirtualReactionForceError(
            "virtual reaction-force manifest does not match canonical contract"
        )
    if manifest.canonical_bytes() != data:
        raise VirtualReactionForceError(
            "virtual reaction-force manifest bytes are not canonical"
        )
    return manifest


@dataclass(frozen=True, slots=True)
class VirtualReactionForceSignal:
    """raw evidenceと分離した、filter済み仮想反力の1sample。"""

    manifest_digest: str
    source_contact_manifest_digest: str
    trial: ContactTrialIdentity
    status: VirtualReactionForceStatus
    source_status: ContactEvidenceStatus | None
    output_frame: VirtualReactionForceFrame
    sample_time_s: float | None
    simulation_time_s: float | None
    frame_index: int | None
    raw_force_world_n: tuple[float, float, float] | None
    raw_force_output_frame_n: tuple[float, float, float] | None
    force_n: tuple[float, float, float] | None
    world_to_output_rotation: tuple[float, ...] | None
    filtered: bool
    deadbanded: bool
    rate_limited: bool
    clamped: bool
    reason: str | None

    def __post_init__(self) -> None:
        _digest("manifest_digest", self.manifest_digest)
        _digest(
            "source_contact_manifest_digest",
            self.source_contact_manifest_digest,
        )
        if not isinstance(self.trial, ContactTrialIdentity):
            raise TypeError("trial must use ContactTrialIdentity")
        if not isinstance(self.status, VirtualReactionForceStatus):
            raise TypeError("status must use VirtualReactionForceStatus")
        if self.source_status is not None and not isinstance(
            self.source_status,
            ContactEvidenceStatus,
        ):
            raise TypeError("source_status must use ContactEvidenceStatus or null")
        if not isinstance(self.output_frame, VirtualReactionForceFrame):
            raise TypeError("output_frame must use VirtualReactionForceFrame")
        for name, value in (
            ("filtered", self.filtered),
            ("deadbanded", self.deadbanded),
            ("rate_limited", self.rate_limited),
            ("clamped", self.clamped),
        ):
            if type(value) is not bool:
                raise TypeError(f"{name} must be a boolean")
        if (self.sample_time_s is None) is not (self.simulation_time_s is None):
            raise ValueError("signal sample and simulation time must be paired")
        if self.sample_time_s is not None:
            sample = _finite("signal.sample_time_s", self.sample_time_s)
            simulation = _finite(
                "signal.simulation_time_s",
                self.simulation_time_s,
            )
            if sample < 0.0 or simulation < 0.0:
                raise ValueError("signal times must be non-negative")
            object.__setattr__(self, "sample_time_s", sample)
            object.__setattr__(self, "simulation_time_s", simulation)
        if self.frame_index is not None and (
            type(self.frame_index) is not int or self.frame_index < 0
        ):
            raise ValueError("signal.frame_index must be non-negative or null")
        raw_world = (
            None
            if self.raw_force_world_n is None
            else _vector3("signal.raw_force_world_n", self.raw_force_world_n)
        )
        raw_output = (
            None
            if self.raw_force_output_frame_n is None
            else _vector3(
                "signal.raw_force_output_frame_n",
                self.raw_force_output_frame_n,
            )
        )
        force = (
            None
            if self.force_n is None
            else _vector3("signal.force_n", self.force_n)
        )
        rotation = (
            None
            if self.world_to_output_rotation is None
            else _rotation3(
                "signal.world_to_output_rotation",
                self.world_to_output_rotation,
            )
        )
        if raw_output is not None and raw_world is None:
            raise ValueError("output-frame raw force requires raw world force")
        valid_status = self.status in {
            VirtualReactionForceStatus.ACTIVE,
            VirtualReactionForceStatus.NO_CONTACT,
        }
        if valid_status != (force is not None):
            raise ValueError("signal force must match its valid lifecycle status")
        if self.status is VirtualReactionForceStatus.ACTIVE:
            if self.source_status is not ContactEvidenceStatus.MEASURED:
                raise ValueError("active signal requires measured contact evidence")
            if raw_world is None or raw_output is None or rotation is None:
                raise ValueError("active signal requires raw force and transform")
            if self.reason is not None:
                raise ValueError("active signal must not carry a failure reason")
        elif self.status is VirtualReactionForceStatus.NO_CONTACT:
            if self.source_status is not ContactEvidenceStatus.NO_CONTACT:
                raise ValueError("no-contact signal requires valid no-contact evidence")
            if raw_world != _ZERO3 or raw_output != _ZERO3 or force != _ZERO3:
                raise ValueError("no-contact signal must contain only status zero")
            if any((self.filtered, self.deadbanded, self.rate_limited, self.clamped)):
                raise ValueError("no-contact signal must not carry filter flags")
            if rotation is None and self.output_frame is VirtualReactionForceFrame.MUJOCO_WORLD:
                raise ValueError("world-frame no-contact signal requires identity transform")
            if self.reason is not None:
                raise ValueError("no-contact signal must not carry a failure reason")
        else:
            if force is not None:
                raise ValueError("invalid signal state must not carry a force")
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("invalid signal state requires a reason")
        object.__setattr__(self, "raw_force_world_n", raw_world)
        object.__setattr__(self, "raw_force_output_frame_n", raw_output)
        object.__setattr__(self, "force_n", force)
        object.__setattr__(self, "world_to_output_rotation", rotation)

    @property
    def unit(self) -> str:
        return VIRTUAL_REACTION_FORCE_UNIT

    @property
    def sign_convention(self) -> str:
        return VIRTUAL_REACTION_FORCE_SIGN_CONVENTION

    @property
    def saturated(self) -> bool:
        """magnitude clamp到達時の別名。"""

        return self.clamped

    def to_document(self) -> dict[str, object]:
        def vector(value: Sequence[float] | None) -> list[float] | None:
            return None if value is None else list(value)

        return {
            "deadbanded": self.deadbanded,
            "filtered": self.filtered,
            "force_source": {
                "field": VIRTUAL_REACTION_FORCE_SOURCE_FIELD,
                "frame": VIRTUAL_REACTION_FORCE_INPUT_FRAME,
                "identity": _identity_document(CONTACT_EVIDENCE_IDENTITY),
                "manifest_digest": self.source_contact_manifest_digest,
                "sign_convention": VIRTUAL_REACTION_FORCE_SIGN_CONVENTION,
                "status": (
                    None if self.source_status is None else self.source_status.value
                ),
                "unit": VIRTUAL_REACTION_FORCE_UNIT,
            },
            "frame_index": self.frame_index,
            "identity": _identity_document(VIRTUAL_REACTION_FORCE_IDENTITY),
            "manifest_digest": self.manifest_digest,
            "output": {
                "force_n": vector(self.force_n),
                "frame": self.output_frame.value,
                "raw_force_n": vector(self.raw_force_output_frame_n),
                "sign_convention": VIRTUAL_REACTION_FORCE_SIGN_CONVENTION,
                "unit": VIRTUAL_REACTION_FORCE_UNIT,
                "world_to_output_rotation_row_major": (
                    None
                    if self.world_to_output_rotation is None
                    else list(self.world_to_output_rotation)
                ),
            },
            "provenance": VIRTUAL_REACTION_FORCE_PROVENANCE,
            "raw_force_world_n": vector(self.raw_force_world_n),
            "reason": self.reason,
            "rate_limited": self.rate_limited,
            "sample_time_s": self.sample_time_s,
            "saturated": self.saturated,
            "schema_version": VIRTUAL_REACTION_FORCE_SCHEMA_VERSION,
            "source_contact_manifest_digest": self.source_contact_manifest_digest,
            "status": self.status.value,
            "simulation_time_s": self.simulation_time_s,
            "trial": self.trial.to_document(),
            "unit": VIRTUAL_REACTION_FORCE_UNIT,
            "sign_convention": VIRTUAL_REACTION_FORCE_SIGN_CONVENTION,
        }

    def canonical_bytes(self) -> bytes:
        try:
            return json.dumps(
                self.to_document(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise VirtualReactionForceError(
                f"virtual reaction-force signal serialization failed: {exc}"
            ) from exc


class VirtualReactionForceProcessor:
    """trial-bound filter stateを持つ決定的なsignal導出器。"""

    def __init__(self, manifest: VirtualReactionForceManifest) -> None:
        if not isinstance(manifest, VirtualReactionForceManifest):
            raise TypeError("processor requires VirtualReactionForceManifest")
        self.manifest = manifest
        self._trial: ContactTrialIdentity | None = None
        self._last_sample_time_s: float | None = None
        self._last_simulation_time_s: float | None = None
        self._last_frame_index: int | None = None
        self._window: list[tuple[float, float, float]] = []
        self._low_pass: tuple[float, float, float] | None = None
        self._last_output: tuple[float, float, float] | None = None

    def reset(self, trial: ContactTrialIdentity | None = None) -> None:
        """trial境界で時計と全filter stateを破棄する。"""

        if trial is not None and not isinstance(trial, ContactTrialIdentity):
            raise TypeError("trial must use ContactTrialIdentity or null")
        self._trial = trial
        self._clear_filter_state()
        self._last_sample_time_s = None
        self._last_simulation_time_s = None
        self._last_frame_index = None

    def _clear_filter_state(self) -> None:
        self._window.clear()
        self._low_pass = None
        self._last_output = None

    def _record_clock(self, evidence: ContactEvidence) -> None:
        self._last_sample_time_s = evidence.sample_time_s
        self._last_simulation_time_s = evidence.simulation_time_s
        if evidence.frame_index is not None:
            self._last_frame_index = evidence.frame_index

    def _signal(
        self,
        *,
        trial: ContactTrialIdentity,
        status: VirtualReactionForceStatus,
        source_status: ContactEvidenceStatus | None,
        sample_time_s: float | None = None,
        simulation_time_s: float | None = None,
        frame_index: int | None = None,
        raw_world: tuple[float, float, float] | None = None,
        raw_output: tuple[float, float, float] | None = None,
        force: tuple[float, float, float] | None = None,
        rotation: tuple[float, ...] | None = None,
        filtered: bool = False,
        deadbanded: bool = False,
        rate_limited: bool = False,
        clamped: bool = False,
        reason: str | None = None,
    ) -> VirtualReactionForceSignal:
        return VirtualReactionForceSignal(
            manifest_digest=self.manifest.digest,
            source_contact_manifest_digest=self.manifest.source_contact_manifest_digest,
            trial=trial,
            status=status,
            source_status=source_status,
            output_frame=self.manifest.config.output_frame,
            sample_time_s=sample_time_s,
            simulation_time_s=simulation_time_s,
            frame_index=frame_index,
            raw_force_world_n=raw_world,
            raw_force_output_frame_n=raw_output,
            force_n=force,
            world_to_output_rotation=rotation,
            filtered=filtered,
            deadbanded=deadbanded,
            rate_limited=rate_limited,
            clamped=clamped,
            reason=reason,
        )

    def _invalid(
        self,
        trial: ContactTrialIdentity,
        reason: str,
        *,
        evidence: ContactEvidence | None = None,
        source_status: ContactEvidenceStatus | None = None,
    ) -> VirtualReactionForceSignal:
        self._clear_filter_state()
        return self._signal(
            trial=trial,
            status=VirtualReactionForceStatus.INVALID,
            source_status=source_status,
            sample_time_s=None if evidence is None else evidence.sample_time_s,
            simulation_time_s=(
                None if evidence is None else evidence.simulation_time_s
            ),
            frame_index=None if evidence is None else evidence.frame_index,
            reason=reason,
        )

    def _rotation_for_sample(
        self,
        value: object | None,
        *,
        allow_missing: bool = False,
    ) -> tuple[float, ...] | None:
        frame = self.manifest.config.output_frame
        if frame is VirtualReactionForceFrame.MUJOCO_WORLD:
            if value is not None:
                raise VirtualReactionForceError(
                    "world-frame output does not accept a caller transform"
                )
            return _IDENTITY_ROTATION
        if value is None:
            if allow_missing:
                return None
            raise VirtualReactionForceError(
                "tool and device-neutral frames require a per-sample rotation"
            )
        return _rotation3("world_to_output_rotation", value)

    def process(
        self,
        evidence: ContactEvidence | None,
        *,
        trial: ContactTrialIdentity,
        world_to_output_rotation: Sequence[float] | None = None,
    ) -> VirtualReactionForceSignal:
        """raw measured evidence 1sampleから送信を伴わないsignalを返す。"""

        if not isinstance(trial, ContactTrialIdentity):
            raise TypeError("trial must use ContactTrialIdentity")
        if self._trial is None or trial != self._trial:
            self.reset(trial)

        if evidence is None:
            self._clear_filter_state()
            return self._signal(
                trial=trial,
                status=VirtualReactionForceStatus.MEASUREMENT_UNAVAILABLE,
                source_status=None,
                reason="contact evidence sample is missing",
            )
        if not isinstance(evidence, ContactEvidence):
            self._clear_filter_state()
            return self._signal(
                trial=trial,
                status=VirtualReactionForceStatus.INVALID,
                source_status=None,
                reason="input is not ContactEvidence",
            )
        if evidence.manifest_digest != self.manifest.source_contact_manifest_digest:
            return self._invalid(
                trial,
                "contact evidence manifest digest does not match signal manifest",
                evidence=evidence,
                source_status=evidence.status,
            )
        source_manifest = self.manifest.contact_manifest
        if (
            evidence.scene_identity != source_manifest.scene.identity
            or evidence.object_identity != source_manifest.object.identity
        ):
            return self._invalid(
                trial,
                "contact evidence scene or object identity does not match signal manifest",
                evidence=evidence,
                source_status=evidence.status,
            )

        sample = evidence.sample_time_s
        simulation = evidence.simulation_time_s
        sample_delta: float | None = None
        if self._last_sample_time_s is not None:
            sample_delta = sample - self._last_sample_time_s
            simulation_delta = simulation - self._last_simulation_time_s  # type: ignore[operator]
            if sample_delta <= 0.0 or simulation_delta < 0.0:
                return self._invalid(
                    trial,
                    "contact evidence time moved backwards or repeated",
                    evidence=evidence,
                    source_status=evidence.status,
                )
            if (
                evidence.frame_index is not None
                and self._last_frame_index is not None
                and evidence.frame_index <= self._last_frame_index
            ):
                return self._invalid(
                    trial,
                    "contact evidence frame_index moved backwards or repeated",
                    evidence=evidence,
                    source_status=evidence.status,
                )
            if simulation_delta == 0.0:
                self._record_clock(evidence)
                self._clear_filter_state()
                return self._signal(
                    trial=trial,
                    status=VirtualReactionForceStatus.STALE,
                    source_status=evidence.status,
                    sample_time_s=sample,
                    simulation_time_s=simulation,
                    frame_index=evidence.frame_index,
                    reason="simulation time did not advance",
                )
            if (
                sample_delta > self.manifest.config.max_inter_sample_gap_s
                or simulation_delta > self.manifest.config.max_inter_sample_gap_s
            ):
                self._record_clock(evidence)
                self._clear_filter_state()
                raw_world = self._valid_raw_force(evidence)
                return self._signal(
                    trial=trial,
                    status=VirtualReactionForceStatus.STALE,
                    source_status=evidence.status,
                    sample_time_s=sample,
                    simulation_time_s=simulation,
                    frame_index=evidence.frame_index,
                    raw_world=raw_world,
                    reason="contact evidence gap exceeds max_inter_sample_gap_s",
                )

        if evidence.status is ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE:
            self._record_clock(evidence)
            self._clear_filter_state()
            return self._signal(
                trial=trial,
                status=VirtualReactionForceStatus.MEASUREMENT_UNAVAILABLE,
                source_status=evidence.status,
                sample_time_s=sample,
                simulation_time_s=simulation,
                frame_index=evidence.frame_index,
                reason=evidence.reason or "contact measurement is unavailable",
            )
        if evidence.status in {
            ContactEvidenceStatus.INVALID_CONTACT,
            ContactEvidenceStatus.SOLVER_INVALID,
        }:
            self._record_clock(evidence)
            self._clear_filter_state()
            return self._signal(
                trial=trial,
                status=VirtualReactionForceStatus.INVALID,
                source_status=evidence.status,
                sample_time_s=sample,
                simulation_time_s=simulation,
                frame_index=evidence.frame_index,
                reason=evidence.reason or "contact evidence is invalid",
            )
        if evidence.status is ContactEvidenceStatus.NO_CONTACT:
            self._record_clock(evidence)
            self._clear_filter_state()
            try:
                rotation = self._rotation_for_sample(
                    world_to_output_rotation,
                    allow_missing=True,
                )
                raw_output = (
                    _ZERO3
                    if rotation is None
                    else _apply_rotation(rotation, _ZERO3)
                )
            except VirtualReactionForceError as exc:
                return self._signal(
                    trial=trial,
                    status=VirtualReactionForceStatus.INVALID,
                    source_status=evidence.status,
                    sample_time_s=sample,
                    simulation_time_s=simulation,
                    frame_index=evidence.frame_index,
                    reason=str(exc),
                )
            return self._signal(
                trial=trial,
                status=VirtualReactionForceStatus.NO_CONTACT,
                source_status=evidence.status,
                sample_time_s=sample,
                simulation_time_s=simulation,
                frame_index=evidence.frame_index,
                raw_world=_ZERO3,
                raw_output=raw_output,
                force=_ZERO3,
                rotation=rotation,
            )
        if evidence.status is not ContactEvidenceStatus.MEASURED:
            return self._invalid(
                trial,
                "contact evidence status is unsupported",
                evidence=evidence,
                source_status=evidence.status,
            )

        self._record_clock(evidence)
        raw_world = self._valid_raw_force(evidence)
        if raw_world is None:
            return self._invalid(
                trial,
                "measured contact evidence is missing its target aggregate",
                evidence=evidence,
                source_status=evidence.status,
            )
        try:
            rotation = self._rotation_for_sample(world_to_output_rotation)
            assert rotation is not None
            raw_output = _apply_rotation(rotation, raw_world)
            magnitude = _magnitude(raw_output)
            if not math.isfinite(magnitude):
                raise VirtualReactionForceError("raw contact force magnitude is invalid")
            deadbanded = magnitude > 0.0 and magnitude <= self.manifest.config.deadband_n
            filtered_input = _ZERO3 if magnitude <= self.manifest.config.deadband_n else raw_output
            self._window.append(filtered_input)
            if len(self._window) > self.manifest.config.smoothing_window_samples:
                del self._window[0]
            smoothed = tuple(
                math.fsum(sample_value[axis] for sample_value in self._window)
                / len(self._window)
                for axis in range(3)
            )
            filtered = (
                self.manifest.config.smoothing_window_samples > 1
                or self.manifest.config.low_pass_time_constant_s > 0.0
            )

            time_constant = self.manifest.config.low_pass_time_constant_s
            if time_constant == 0.0:
                low_pass = _clean_vector(smoothed)
            elif self._low_pass is None or sample_delta is None:
                low_pass = _clean_vector(smoothed)
            else:
                alpha = 1.0 - math.exp(-sample_delta / time_constant)
                low_pass = _clean_vector(
                    tuple(
                        self._low_pass[axis]
                        + alpha * (smoothed[axis] - self._low_pass[axis])
                        for axis in range(3)
                    )
                )
            self._low_pass = low_pass
            output = low_pass
            rate_limited = False
            rate_limit = self.manifest.config.rate_limit_n_per_s
            if (
                rate_limit is not None
                and self._last_output is not None
                and sample_delta is not None
            ):
                delta = tuple(output[axis] - self._last_output[axis] for axis in range(3))
                delta_magnitude = _magnitude(delta)
                permitted_delta = rate_limit * sample_delta
                if not math.isfinite(permitted_delta):
                    raise VirtualReactionForceError("rate-limit interval is invalid")
                if delta_magnitude > permitted_delta and delta_magnitude > 0.0:
                    scale = permitted_delta / delta_magnitude
                    output = _clean_vector(
                        tuple(
                            self._last_output[axis] + delta[axis] * scale
                            for axis in range(3)
                        )
                    )
                    rate_limited = True

            clamped = False
            maximum = self.manifest.config.magnitude_clamp_n
            output_magnitude = _magnitude(output)
            if not math.isfinite(output_magnitude):
                raise VirtualReactionForceError("filtered force magnitude is invalid")
            if maximum is not None and output_magnitude > maximum:
                scale = maximum / output_magnitude
                output = _clean_vector(
                    tuple(component * scale for component in output)
                )
                clamped = True
            output = _clean_vector(output)
        except (OverflowError, ValueError) as exc:
            self._clear_filter_state()
            return self._signal(
                trial=trial,
                status=VirtualReactionForceStatus.INVALID,
                source_status=evidence.status,
                sample_time_s=sample,
                simulation_time_s=simulation,
                frame_index=evidence.frame_index,
                raw_world=raw_world,
                reason=f"virtual reaction-force computation failed: {exc}",
            )
        self._last_output = output
        return self._signal(
            trial=trial,
            status=VirtualReactionForceStatus.ACTIVE,
            source_status=evidence.status,
            sample_time_s=sample,
            simulation_time_s=simulation,
            frame_index=evidence.frame_index,
            raw_world=raw_world,
            raw_output=raw_output,
            force=output,
            rotation=rotation,
            filtered=filtered,
            deadbanded=deadbanded,
            rate_limited=rate_limited,
            clamped=clamped,
        )

    @staticmethod
    def _valid_raw_force(
        evidence: ContactEvidence,
    ) -> tuple[float, float, float] | None:
        if evidence.status is ContactEvidenceStatus.NO_CONTACT:
            return _ZERO3
        if evidence.status is not ContactEvidenceStatus.MEASURED:
            return None
        aggregate = evidence.aggregate
        if aggregate is None or aggregate.contact_count < 1:
            return None
        try:
            return _vector3(
                "contact aggregate object_on_tool_force_world_n",
                aggregate.object_on_tool_force_world_n,
            )
        except VirtualReactionForceError:
            return None


__all__ = [
    "VIRTUAL_REACTION_FORCE_CONTRACT_VERSION",
    "VIRTUAL_REACTION_FORCE_DIGEST_ALGORITHM",
    "VIRTUAL_REACTION_FORCE_IDENTITY",
    "VIRTUAL_REACTION_FORCE_INPUT_FRAME",
    "VIRTUAL_REACTION_FORCE_PROVENANCE",
    "VIRTUAL_REACTION_FORCE_SCHEMA_VERSION",
    "VIRTUAL_REACTION_FORCE_SIGN_CONVENTION",
    "VIRTUAL_REACTION_FORCE_SOURCE_FIELD",
    "VIRTUAL_REACTION_FORCE_UNIT",
    "VirtualReactionForceConfig",
    "VirtualReactionForceError",
    "VirtualReactionForceFrame",
    "VirtualReactionForceManifest",
    "VirtualReactionForceProcessor",
    "VirtualReactionForceSignal",
    "VirtualReactionForceStatus",
    "decode_virtual_reaction_force_manifest",
    "encode_virtual_reaction_force_manifest",
]
