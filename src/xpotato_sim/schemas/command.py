"""MappingからRobot/runtimeへ渡すtyped command schema。

field ordering、unit、frameは各commandのcontractで固定し、viewerやtransportが独自に
physical stateを再構成するためのschemaではない。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from typing import Literal, TypeAlias

from xpotato_sim.schemas.types import Vector3


PHYSICAL_OUTPUT_REQUEST_SCHEMA_VERSION = "physical-output-request/v1"
PHYSICAL_OUTPUT_PERMISSION_SCHEMA_VERSION = "physical-output-permission/v1"

PhysicalOutputMode: TypeAlias = Literal[
    "disabled",
    "dry_run",
    "transmission_enabled",
    "physical_actuation",
]
PhysicalOutputPermissionState: TypeAlias = Literal["disabled", "enabled"]
PhysicalOutputDecisionStatus: TypeAlias = Literal["accepted", "rejected"]
PhysicalOutputEvidenceStatus: TypeAlias = Literal[
    "requested",
    "accepted",
    "rejected",
    "sent",
    "acknowledged",
]

_PHYSICAL_OUTPUT_MODES = frozenset(
    {"disabled", "dry_run", "transmission_enabled", "physical_actuation"}
)


def _physical_output_command_type(command_semantics: str) -> type | None:
    # The command classes are defined below this contract block.  Resolve the
    # type lazily so module import order cannot turn a known semantic into None.
    if command_semantics == "endpoint_velocity_command/v1":
        return EndpointVelocityCommand
    if command_semantics == "joint_position_command/v1":
        return JointPositionCommand
    return None


def _physical_output_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL")
    return value


def _physical_output_optional_identifier(name: str, value: object | None) -> str | None:
    if value is None:
        return None
    return _physical_output_identifier(name, value)


def _physical_output_finite(name: str, value: object, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    if positive and result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def _physical_output_non_negative_int(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class PhysicalOutputPermission:
    """Physical output mode and the explicit operator gate.

    ``dry_run`` is recording-only and never authorizes a transport.  Both
    transmission modes require an operator identity and an opaque enable
    identifier.  The identifier is deliberately not a secret/token payload.
    """

    mode: PhysicalOutputMode = "disabled"
    operator_id: str | None = None
    enable_token_id: str | None = None
    schema_version: str = PHYSICAL_OUTPUT_PERMISSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PHYSICAL_OUTPUT_PERMISSION_SCHEMA_VERSION:
            raise ValueError(
                "unsupported physical output permission schema_version: "
                f"{self.schema_version!r}"
            )
        if not isinstance(self.mode, str) or self.mode not in _PHYSICAL_OUTPUT_MODES:
            raise ValueError(
                "physical output permission mode must be one of "
                f"{sorted(_PHYSICAL_OUTPUT_MODES)!r}"
            )
        operator_id = _physical_output_optional_identifier("operator_id", self.operator_id)
        enable_token_id = _physical_output_optional_identifier(
            "enable_token_id", self.enable_token_id
        )
        if self.mode == "disabled" and (
            operator_id is not None or enable_token_id is not None
        ):
            raise ValueError(
                "disabled physical output permission cannot carry an operator gate"
            )
        if enable_token_id is not None and operator_id is None:
            raise ValueError("enable_token_id requires operator_id")
        if self.mode in {"transmission_enabled", "physical_actuation"}:
            if operator_id is None or enable_token_id is None:
                raise ValueError(
                    f"{self.mode} requires an explicit operator enable gate"
                )
        object.__setattr__(self, "operator_id", operator_id)
        object.__setattr__(self, "enable_token_id", enable_token_id)

    @property
    def state(self) -> PhysicalOutputPermissionState:
        return "disabled" if self.mode == "disabled" else "enabled"

    @property
    def explicitly_enabled(self) -> bool:
        return self.operator_id is not None and self.enable_token_id is not None

    @property
    def allows_transmission(self) -> bool:
        return self.mode in {
            "transmission_enabled",
            "physical_actuation",
        } and self.explicitly_enabled

    @property
    def allows_physical_actuation(self) -> bool:
        return self.mode == "physical_actuation" and self.explicitly_enabled

    def to_json_value(self) -> dict[str, object]:
        return physical_output_permission_to_json_value(self)

    def to_json_bytes(self) -> bytes:
        return encode_physical_output_permission(self)

    @classmethod
    def from_json(
        cls, document: bytes | str | Mapping[str, object]
    ) -> "PhysicalOutputPermission":
        return decode_physical_output_permission(document)


@dataclass(frozen=True, slots=True)
class PhysicalOutputRequest:
    """Versioned boundary between an internal Robot command and physical output.

    The request accepts only a typed ``RobotCommand``.  ``MotionCommand`` and
    arbitrary mappings cannot cross this boundary.  A request is intent/evidence
    at the ``requested`` level; it does not imply acceptance, transmission, or
    acknowledgement.
    """

    target_robot_id: str
    endpoint_id: str
    command_semantics: str
    command: RobotCommand
    session_id: str
    sequence: int
    timestamp_s: float
    cadence_s: float
    software_revision: str
    schema_version: str = PHYSICAL_OUTPUT_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PHYSICAL_OUTPUT_REQUEST_SCHEMA_VERSION:
            raise ValueError(
                "unsupported physical output request schema_version: "
                f"{self.schema_version!r}"
            )
        for name in (
            "target_robot_id",
            "endpoint_id",
            "command_semantics",
            "session_id",
            "software_revision",
        ):
            _physical_output_identifier(name, getattr(self, name))
        expected_command_type = _physical_output_command_type(self.command_semantics)
        if expected_command_type is None:
            raise ValueError(
                "unknown physical output command semantics: "
                f"{self.command_semantics!r}"
            )
        if not isinstance(self.command, expected_command_type):
            raise TypeError(
                "physical output command does not match command_semantics "
                f"{self.command_semantics!r}"
            )
        timestamp_s = _physical_output_finite("timestamp_s", self.timestamp_s)
        cadence_s = _physical_output_finite("cadence_s", self.cadence_s, positive=True)
        if self.command.timestamp_s != timestamp_s:
            raise ValueError(
                "physical output request timestamp_s must match command timestamp_s"
            )
        sequence = _physical_output_non_negative_int("sequence", self.sequence)
        object.__setattr__(self, "timestamp_s", timestamp_s)
        object.__setattr__(self, "cadence_s", cadence_s)
        object.__setattr__(self, "sequence", sequence)

    def to_json_value(self) -> dict[str, object]:
        return physical_output_request_to_json_value(self)

    def to_json_bytes(self) -> bytes:
        return encode_physical_output_request(self)

    @classmethod
    def from_json(
        cls, document: bytes | str | Mapping[str, object]
    ) -> "PhysicalOutputRequest":
        return decode_physical_output_request(document)


@dataclass(frozen=True, slots=True)
class PhysicalOutputDecision:
    """Permission decision kept separate from request and delivery evidence."""

    request: PhysicalOutputRequest
    permission: PhysicalOutputPermission
    status: PhysicalOutputDecisionStatus
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, PhysicalOutputRequest):
            raise TypeError("physical output decision requires PhysicalOutputRequest")
        if not isinstance(self.permission, PhysicalOutputPermission):
            raise TypeError("physical output decision requires PhysicalOutputPermission")
        if self.status not in {"accepted", "rejected"}:
            raise ValueError("physical output decision status must be accepted or rejected")
        reason = _physical_output_optional_identifier("reason", self.reason)
        if self.status == "rejected" and reason is None:
            raise ValueError("rejected physical output decision requires a reason")
        if self.status == "accepted" and reason is not None:
            raise ValueError("accepted physical output decision cannot carry a reason")
        if self.status == "accepted" and self.permission.mode == "disabled":
            raise ValueError(
                "accepted physical output decision requires non-disabled permission"
            )
        if (
            self.status == "accepted"
            and self.permission.mode
            in {"transmission_enabled", "physical_actuation"}
            and not self.permission.explicitly_enabled
        ):
            raise ValueError(
                "accepted physical output decision requires explicit operator enable gate"
            )
        object.__setattr__(self, "reason", reason)

    @property
    def requested(self) -> bool:
        return True

    @property
    def sent(self) -> bool:
        return False

    @property
    def acknowledged(self) -> bool:
        return False


# This descriptive alias keeps call sites explicit while retaining one schema
# owner for the permission decision.
PhysicalOutputPermissionDecision = PhysicalOutputDecision


def _physical_output_command_to_json_value(command: RobotCommand) -> dict[str, object]:
    if isinstance(command, EndpointVelocityCommand):
        return {
            "kind": "endpoint_velocity_command",
            "timestamp_s": command.timestamp_s,
            "velocity_m_s": list(command.velocity_m_s),
            "frame": command.frame,
        }
    if isinstance(command, JointPositionCommand):
        return {
            "kind": "joint_position_command",
            "timestamp_s": command.timestamp_s,
            "joint_angles_rad": list(command.joint_angles_rad),
        }
    raise TypeError("physical output request command must be a typed RobotCommand")


def physical_output_request_to_json_value(
    request: PhysicalOutputRequest,
) -> dict[str, object]:
    if not isinstance(request, PhysicalOutputRequest):
        raise TypeError("physical output JSON encoding requires PhysicalOutputRequest")
    return {
        "cadence_s": request.cadence_s,
        "command": _physical_output_command_to_json_value(request.command),
        "command_semantics": request.command_semantics,
        "endpoint_id": request.endpoint_id,
        "schema_version": request.schema_version,
        "sequence": request.sequence,
        "session_id": request.session_id,
        "software_revision": request.software_revision,
        "target_robot_id": request.target_robot_id,
        "timestamp_s": request.timestamp_s,
    }


def _physical_output_permission_to_json_value(
    permission: PhysicalOutputPermission,
) -> dict[str, object]:
    if not isinstance(permission, PhysicalOutputPermission):
        raise TypeError("physical output JSON encoding requires PhysicalOutputPermission")
    return {
        "enable_token_id": permission.enable_token_id,
        "mode": permission.mode,
        "operator_id": permission.operator_id,
        "schema_version": permission.schema_version,
        "state": permission.state,
    }


def physical_output_permission_to_json_value(
    permission: PhysicalOutputPermission,
) -> dict[str, object]:
    return _physical_output_permission_to_json_value(permission)


def _physical_output_canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def encode_physical_output_request(request: PhysicalOutputRequest) -> bytes:
    """Encode one request deterministically as UTF-8 JSON bytes."""

    encoded = _physical_output_canonical_json_bytes(
        physical_output_request_to_json_value(request)
    )
    if decode_physical_output_request(encoded) != request:
        raise ValueError("physical output request JSON round-trip is not deterministic")
    return encoded


def encode_physical_output_permission(permission: PhysicalOutputPermission) -> bytes:
    encoded = _physical_output_canonical_json_bytes(
        _physical_output_permission_to_json_value(permission)
    )
    if decode_physical_output_permission(encoded) != permission:
        raise ValueError("physical output permission JSON round-trip is not deterministic")
    return encoded


def _physical_output_parse_json_document(
    document: bytes | str | Mapping[str, object],
) -> Mapping[str, object]:
    if isinstance(document, Mapping):
        value = dict(document)
    else:
        if isinstance(document, bytes):
            try:
                text = document.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError("physical output document must be valid UTF-8") from exc
        elif isinstance(document, str):
            text = document
        else:
            raise TypeError("physical output document must be UTF-8 bytes, text, or an object")

        def reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate field in physical output document: {key!r}")
                result[key] = value
            return result

        def reject_non_finite_constant(value: str) -> object:
            raise ValueError(f"non-finite JSON constant is forbidden: {value}")

        try:
            value = json.loads(
                text,
                object_pairs_hook=reject_duplicate_fields,
                parse_constant=reject_non_finite_constant,
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("physical output document is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("physical output document must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError("physical output document object keys must be strings")
    return value


def _physical_output_require_fields(
    document: Mapping[str, object],
    expected: frozenset[str],
    name: str,
) -> Mapping[str, object]:
    actual = frozenset(document)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ValueError(f"{name} has unknown fields: {unknown}")
    if missing:
        raise ValueError(f"{name} is missing fields: {missing}")
    return document


def _physical_output_decode_command(
    value: object,
    *,
    expected_semantics: str,
) -> RobotCommand:
    if not isinstance(value, Mapping):
        raise ValueError("physical output command must be an object")
    if expected_semantics == "endpoint_velocity_command/v1":
        payload = _physical_output_require_fields(
            value,
            frozenset({"kind", "timestamp_s", "velocity_m_s", "frame"}),
            "physical output command",
        )
        if payload["kind"] != "endpoint_velocity_command":
            raise ValueError("physical output command kind does not match semantics")
        velocity = payload["velocity_m_s"]
        if not isinstance(velocity, (list, tuple)):
            raise ValueError("physical output command velocity_m_s must be an array")
        return EndpointVelocityCommand(
            timestamp_s=payload["timestamp_s"],
            velocity_m_s=velocity,
            frame=payload["frame"],
        )
    if expected_semantics == "joint_position_command/v1":
        payload = _physical_output_require_fields(
            value,
            frozenset({"kind", "timestamp_s", "joint_angles_rad"}),
            "physical output command",
        )
        if payload["kind"] != "joint_position_command":
            raise ValueError("physical output command kind does not match semantics")
        angles = payload["joint_angles_rad"]
        if not isinstance(angles, (list, tuple)):
            raise ValueError("physical output command joint_angles_rad must be an array")
        return JointPositionCommand(
            timestamp_s=payload["timestamp_s"],
            joint_angles_rad=angles,
        )
    raise ValueError(f"unknown physical output command semantics: {expected_semantics!r}")


def decode_physical_output_request(
    document: bytes | str | Mapping[str, object],
) -> PhysicalOutputRequest:
    payload = _physical_output_require_fields(
        _physical_output_parse_json_document(document),
        frozenset(
            {
                "schema_version",
                "target_robot_id",
                "endpoint_id",
                "command_semantics",
                "command",
                "session_id",
                "sequence",
                "timestamp_s",
                "cadence_s",
                "software_revision",
            }
        ),
        "physical output request",
    )
    command_semantics = payload["command_semantics"]
    if not isinstance(command_semantics, str):
        raise ValueError("physical output request command_semantics must be a string")
    command = _physical_output_decode_command(
        payload["command"], expected_semantics=command_semantics
    )
    return PhysicalOutputRequest(
        target_robot_id=payload["target_robot_id"],
        endpoint_id=payload["endpoint_id"],
        command_semantics=command_semantics,
        command=command,
        session_id=payload["session_id"],
        sequence=payload["sequence"],
        timestamp_s=payload["timestamp_s"],
        cadence_s=payload["cadence_s"],
        software_revision=payload["software_revision"],
        schema_version=payload["schema_version"],
    )


def decode_physical_output_permission(
    document: bytes | str | Mapping[str, object],
) -> PhysicalOutputPermission:
    payload = _physical_output_require_fields(
        _physical_output_parse_json_document(document),
        frozenset({"schema_version", "mode", "state", "operator_id", "enable_token_id"}),
        "physical output permission",
    )
    permission = PhysicalOutputPermission(
        mode=payload["mode"],
        operator_id=payload["operator_id"],
        enable_token_id=payload["enable_token_id"],
        schema_version=payload["schema_version"],
    )
    if payload["state"] != permission.state:
        raise ValueError("physical output permission state does not match mode")
    return permission


@dataclass(frozen=True, slots=True)
class EndpointVelocityCommand:
    """``frame`` 座標系のendpoint linear velocity command。

    ``velocity_m_s`` は(x, y, z)順のm/sで、``frame`` はcurrent contractの
    ``world`` または``tool``である。accept/reject/holdとqpos生成は対応する
    provider / runtime boundaryが所有する。per-step delta limitはこのcommandの
    fieldではなく、upstream intentまたはruntime policyが所有する。
    """

    timestamp_s: float
    velocity_m_s: Vector3
    frame: str

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_s, bool) or not isinstance(
            self.timestamp_s, Real
        ):
            raise TypeError("endpoint velocity command timestamp must be numeric")
        timestamp_s = float(self.timestamp_s)
        if not isfinite(timestamp_s):
            raise ValueError("endpoint velocity command timestamp must be finite")

        values = tuple(self.velocity_m_s)
        if len(values) != 3:
            raise ValueError(
                "endpoint velocity command must contain exactly three finite values"
            )
        if any(
            isinstance(component, bool) or not isinstance(component, Real)
            for component in values
        ):
            raise TypeError(
                "endpoint velocity command components must be numeric"
            )
        velocity = tuple(float(component) for component in values)
        if not all(isfinite(component) for component in velocity):
            raise ValueError(
                "endpoint velocity command must contain exactly three finite values"
            )
        if self.frame not in {"world", "tool"}:
            raise ValueError(
                "endpoint velocity command frame must be 'world' or 'tool'"
            )
        object.__setattr__(self, "timestamp_s", timestamp_s)
        object.__setattr__(self, "velocity_m_s", velocity)


@dataclass(frozen=True, slots=True)
class JointPositionCommand:
    """Robot-owned joint orderingのtarget qpos。角度jointのunitはrad。"""

    timestamp_s: float
    joint_angles_rad: tuple[float, ...]

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_s, bool) or not isinstance(
            self.timestamp_s, Real
        ):
            raise TypeError("joint position command timestamp must be numeric")
        timestamp_s = float(self.timestamp_s)
        if not isfinite(timestamp_s):
            raise ValueError("joint position command timestamp must be finite")

        values = tuple(self.joint_angles_rad)
        if not values:
            raise ValueError(
                "joint position command must contain at least one joint angle"
            )
        if any(
            isinstance(value, bool) or not isinstance(value, Real)
            for value in values
        ):
            raise TypeError("joint position command angles must be numeric")
        joint_angles_rad = tuple(float(value) for value in values)
        if not all(isfinite(value) for value in joint_angles_rad):
            raise ValueError("joint position command angles must be finite")

        object.__setattr__(self, "timestamp_s", timestamp_s)
        object.__setattr__(self, "joint_angles_rad", joint_angles_rad)


@dataclass(frozen=True, slots=True)
class JointCommand:
    """legacy joint delta/absolute commandを保持するcompatibility schema。"""

    joint_angles_rad: tuple[float, ...] = ()
    joint_velocities_rad_s: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class TargetCommand:
    """world frameのtarget positionをmで表すhigh-level intent。"""

    position_m: Vector3 | None = None
    delta_m: Vector3 = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class MotionCommand:
    """runtime内部でmotionとsafety情報を運ぶenvelope。

    optionalなtarget / joint command bucketとdiagnostic metadataを保持するが、
    Robot / backend commandそのものではない。downstream route / projectionが、
    選択されたcommand semanticで利用可能なshapeを検証する。
    """

    timestamp_s: float
    target: TargetCommand | None = None
    joint: JointCommand | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


RobotCommand = EndpointVelocityCommand | JointPositionCommand


__all__ = [
    "EndpointVelocityCommand",
    "JointCommand",
    "JointPositionCommand",
    "MotionCommand",
    "PHYSICAL_OUTPUT_PERMISSION_SCHEMA_VERSION",
    "PHYSICAL_OUTPUT_REQUEST_SCHEMA_VERSION",
    "PhysicalOutputDecision",
    "PhysicalOutputDecisionStatus",
    "PhysicalOutputEvidenceStatus",
    "PhysicalOutputMode",
    "PhysicalOutputPermission",
    "PhysicalOutputPermissionDecision",
    "PhysicalOutputPermissionState",
    "PhysicalOutputRequest",
    "RobotCommand",
    "TargetCommand",
    "decode_physical_output_permission",
    "decode_physical_output_request",
    "encode_physical_output_permission",
    "encode_physical_output_request",
    "physical_output_permission_to_json_value",
    "physical_output_request_to_json_value",
]
