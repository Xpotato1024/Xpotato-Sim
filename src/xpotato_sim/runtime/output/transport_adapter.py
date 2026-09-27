"""P5許可済みintentをversion付き汎用OSC/UDP datagramへ接続するruntime owner。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite
from numbers import Real
import tomllib
from threading import Lock
from time import monotonic
from typing import Literal, Protocol

from xpotato_sim.runtime.output.lifecycle import (
    PhysicalOutputLifecycle,
    PhysicalOutputLifecycleDispatchResult,
    PhysicalOutputLifecycleResult,
)
from xpotato_sim.runtime.output.permission import evaluate_physical_output_permission
from xpotato_sim.runtime.output.safety_gate import (
    PhysicalOutputSendableRequest,
    physical_output_candidate_id,
    validate_physical_output_sendable_request,
)
from xpotato_sim.schemas import PhysicalOutputPermission, PhysicalOutputRequest
from xpotato_sim.transport.endpoint import (
    OSC_UDP_ENDPOINT_SCHEMA_VERSION,
    OscUdpEndpointConfig,
)
from xpotato_sim.transport.osc import OscMessage, decode_osc_message, encode_osc_message
from xpotato_sim.transport.udp import (
    DatagramEvidenceKind,
    DatagramSendReceipt,
    DatagramSender,
    PreparedDatagramDestination,
)


PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1 = "physical-output-transport-config/v1"
PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2 = "physical-output-transport-config/v2"
PHYSICAL_OUTPUT_TRANSPORT_AUTHORIZATION_SCHEMA_VERSION = "physical-output-transport-authorization/v1"
PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION = PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1
PHYSICAL_OUTPUT_WIRE_SCHEMA_VERSION = "physical-output-wire/v1"
PHYSICAL_OUTPUT_OSC_ADDRESS = "/selfrionette/physical-output/v1"
PhysicalOutputTransportMode = Literal[
    "disabled",
    "dry_run",
    "recording",
    "transmission_enabled",
]
PhysicalOutputTransportResultStatus = Literal[
    "disabled",
    "dry_run_ready",
    "recorded",
    "rejected",
    "transmission_attempted",
]

_TRANSPORT_MODES = frozenset(
    {"disabled", "dry_run", "recording", "transmission_enabled"}
)
_CONFIG_FIELDS_V1 = frozenset(
    {
        "endpoint",
        "expected_codec_identity",
        "max_request_age_s",
        "max_safety_age_s",
        "minimum_cadence_s",
        "mode",
        "operator_enable",
        "schema_version",
        "software_revision",
        "target_robot_id",
    }
)
_CONFIG_FIELDS_V2 = _CONFIG_FIELDS_V1 | {"external_authorization_required"}
_OPERATOR_ENABLE_FIELDS = frozenset({"enable_token_id", "operator_id"})
_TRANSPORT_AUTHORIZATION_ISSUER = object()
_ENDPOINT_FIELDS = frozenset(
    {
        "endpoint_id",
        "host",
        "max_datagram_bytes",
        "port",
        "schema_version",
        "timeout_s",
    }
)
_CODEC_IDENTITY_FIELDS = frozenset(
    {"codec_id", "codec_version", "configuration_sha256"}
)


def _identifier(name: str, value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty canonical string")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL")
    return value


def _finite_non_negative(name: str, value: object) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"{name} must be an integer or float")
    result = float(value)
    if not isfinite(result) or result < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


def _timestamp(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _sha256_hex(name: str, value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")


def _parse_config_json(document: bytes | str) -> object:
    if type(document) is bytes:
        text = document.decode("utf-8", errors="strict")
    elif type(document) is str:
        text = document
    else:
        raise TypeError("transport config must be UTF-8 bytes or text")
    if text.startswith("\ufeff"):
        raise ValueError("transport config must not contain a UTF-8 BOM")
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_json_constant,
    )


@dataclass(frozen=True, slots=True)
class PhysicalOutputCodecIdentity:
    """codecとimmutable設定を特定するversioned identity。"""

    codec_id: str
    codec_version: str
    configuration_sha256: str

    def __post_init__(self) -> None:
        _identifier("codec_id", self.codec_id)
        _identifier("codec_version", self.codec_version)
        _sha256_hex("codec configuration_sha256", self.configuration_sha256)

    @classmethod
    def from_settings(
        cls,
        codec_id: str,
        codec_version: str,
        immutable_settings: Mapping[str, object],
    ) -> "PhysicalOutputCodecIdentity":
        if not isinstance(immutable_settings, Mapping):
            raise TypeError("immutable codec settings must be a mapping")
        if any(type(name) is not str for name in immutable_settings):
            raise ValueError("immutable codec setting names must be strings")
        settings_digest = sha256(_canonical_json_bytes(dict(immutable_settings))).hexdigest()
        return cls(codec_id, codec_version, settings_digest)

    @property
    def identity_sha256(self) -> str:
        return sha256(_canonical_json_bytes(self.to_json_value())).hexdigest()

    def to_json_value(self) -> dict[str, object]:
        return {
            "codec_id": self.codec_id,
            "codec_version": self.codec_version,
            "configuration_sha256": self.configuration_sha256,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "PhysicalOutputCodecIdentity":
        if not isinstance(value, Mapping) or set(value) != _CODEC_IDENTITY_FIELDS:
            raise ValueError("expected_codec_identity fields are incomplete or unknown")
        if any(type(value[name]) is not str for name in _CODEC_IDENTITY_FIELDS):
            raise ValueError("expected_codec_identity values must be strings")
        return cls(
            codec_id=value["codec_id"],
            codec_version=value["codec_version"],
            configuration_sha256=value["configuration_sha256"],
        )


_GENERIC_CODEC_IDENTITY = PhysicalOutputCodecIdentity.from_settings(
    "physical-output-generic-osc",
    "v1",
    {
        "osc_address": PHYSICAL_OUTPUT_OSC_ADDRESS,
        "wire_schema_version": PHYSICAL_OUTPUT_WIRE_SCHEMA_VERSION,
    },
)


@dataclass(frozen=True, slots=True)
class PhysicalOutputOperatorEnable:
    """permission snapshotと照合するopaque operator enable identity。"""

    operator_id: str
    enable_token_id: str

    def __post_init__(self) -> None:
        _identifier("operator_id", self.operator_id)
        _identifier("enable_token_id", self.enable_token_id)

    def to_json_value(self) -> dict[str, object]:
        return {
            "enable_token_id": self.enable_token_id,
            "operator_id": self.operator_id,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "PhysicalOutputOperatorEnable":
        if not isinstance(value, Mapping) or set(value) != _OPERATOR_ENABLE_FIELDS:
            raise ValueError("operator_enable fields are incomplete or unknown")
        if type(value["operator_id"]) is not str or type(value["enable_token_id"]) is not str:
            raise ValueError("operator_enable values must be strings")
        return cls(
            operator_id=value["operator_id"],
            enable_token_id=value["enable_token_id"],
        )


@dataclass(frozen=True, slots=True)
class PhysicalOutputTransportConfig:
    """Version付きtarget、revision、endpoint、mode、freshness設定。"""

    target_robot_id: str
    software_revision: str
    endpoint: OscUdpEndpointConfig
    expected_codec_identity: PhysicalOutputCodecIdentity = _GENERIC_CODEC_IDENTITY
    mode: PhysicalOutputTransportMode = "disabled"
    operator_enable: PhysicalOutputOperatorEnable | None = None
    max_request_age_s: float = 0.25
    max_safety_age_s: float = 0.25
    minimum_cadence_s: float = 0.0
    external_authorization_required: bool = False
    schema_version: str = PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in {
            PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1,
            PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2,
        }:
            raise ValueError(
                f"unsupported transport config schema_version: {self.schema_version!r}"
            )
        if type(self.external_authorization_required) is not bool:
            raise TypeError("external_authorization_required must be a boolean")
        if (
            self.schema_version == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1
            and self.external_authorization_required
        ):
            raise ValueError("transport config v1 cannot require external authorization")
        _identifier("target_robot_id", self.target_robot_id)
        _identifier("software_revision", self.software_revision)
        if type(self.endpoint) is not OscUdpEndpointConfig:
            raise TypeError("endpoint must be OscUdpEndpointConfig")
        if type(self.expected_codec_identity) is not PhysicalOutputCodecIdentity:
            raise TypeError("expected_codec_identity must be PhysicalOutputCodecIdentity")
        if self.endpoint.schema_version != OSC_UDP_ENDPOINT_SCHEMA_VERSION:
            raise ValueError("transport endpoint schema_version is unsupported")
        if type(self.mode) is not str or self.mode not in _TRANSPORT_MODES:
            raise ValueError(f"transport mode must be one of {sorted(_TRANSPORT_MODES)!r}")
        if self.operator_enable is not None and type(self.operator_enable) is not PhysicalOutputOperatorEnable:
            raise TypeError("operator_enable must be PhysicalOutputOperatorEnable or None")
        if self.mode == "transmission_enabled" and self.operator_enable is None:
            raise ValueError("transmission_enabled mode requires operator_enable")
        if self.mode != "transmission_enabled" and self.operator_enable is not None:
            raise ValueError("operator_enable is only valid in transmission_enabled mode")
        object.__setattr__(
            self,
            "max_request_age_s",
            _finite_non_negative("max_request_age_s", self.max_request_age_s),
        )
        object.__setattr__(
            self,
            "max_safety_age_s",
            _finite_non_negative("max_safety_age_s", self.max_safety_age_s),
        )
        object.__setattr__(
            self,
            "minimum_cadence_s",
            _finite_non_negative("minimum_cadence_s", self.minimum_cadence_s),
        )

    def to_json_value(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint.to_json_value(),
            "expected_codec_identity": self.expected_codec_identity.to_json_value(),
            "max_request_age_s": self.max_request_age_s,
            "max_safety_age_s": self.max_safety_age_s,
            "minimum_cadence_s": self.minimum_cadence_s,
            **(
                {"external_authorization_required": self.external_authorization_required}
                if self.schema_version == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2
                else {}
            ),
            "mode": self.mode,
            "operator_enable": (
                None if self.operator_enable is None else self.operator_enable.to_json_value()
            ),
            "schema_version": self.schema_version,
            "software_revision": self.software_revision,
            "target_robot_id": self.target_robot_id,
        }

    def to_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_json_value())

    @classmethod
    def from_mapping(cls, value: object) -> "PhysicalOutputTransportConfig":
        if not isinstance(value, Mapping):
            raise ValueError("transport config must be an object")
        schema_version = value.get("schema_version")
        if type(schema_version) is not str:
            raise ValueError("transport config schema_version must be a string")
        expected_fields = (
            _CONFIG_FIELDS_V1
            if schema_version == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1
            else _CONFIG_FIELDS_V2
            if schema_version == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2
            else None
        )
        if expected_fields is None:
            raise ValueError(f"unsupported transport config schema_version: {schema_version!r}")
        if set(value) != expected_fields:
            raise ValueError("transport config fields are incomplete or unknown")
        if schema_version == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2 and type(value["external_authorization_required"]) is not bool:
            raise ValueError("external_authorization_required must be a boolean")
        if type(value["target_robot_id"]) is not str:
            raise ValueError("target_robot_id must be a string")
        if type(value["software_revision"]) is not str:
            raise ValueError("software_revision must be a string")
        if type(value["mode"]) is not str:
            raise ValueError("transport mode must be a string")
        endpoint_value = value["endpoint"]
        if not isinstance(endpoint_value, Mapping) or set(endpoint_value) != _ENDPOINT_FIELDS:
            raise ValueError("endpoint fields are incomplete or unknown")
        endpoint = OscUdpEndpointConfig.from_mapping(endpoint_value)
        codec_identity = PhysicalOutputCodecIdentity.from_mapping(
            value["expected_codec_identity"]
        )
        operator_enable_value = value["operator_enable"]
        operator_enable = (
            None
            if operator_enable_value is None
            else PhysicalOutputOperatorEnable.from_mapping(operator_enable_value)
        )
        numeric_fields = ("max_request_age_s", "max_safety_age_s", "minimum_cadence_s")
        if any(type(value[name]) not in {int, float} for name in numeric_fields):
            raise ValueError("transport age and cadence fields must be numeric")
        return cls(
            target_robot_id=value["target_robot_id"],
            software_revision=value["software_revision"],
            endpoint=endpoint,
            expected_codec_identity=codec_identity,
            mode=value["mode"],  # type: ignore[arg-type]
            operator_enable=operator_enable,
            max_request_age_s=value["max_request_age_s"],  # type: ignore[arg-type]
            max_safety_age_s=value["max_safety_age_s"],  # type: ignore[arg-type]
            minimum_cadence_s=value["minimum_cadence_s"],  # type: ignore[arg-type]
            external_authorization_required=(
                value["external_authorization_required"]
                if schema_version == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2
                else False
            ),  # type: ignore[arg-type]
            schema_version=schema_version,
        )

    @classmethod
    def from_json(cls, document: bytes | str | Mapping[str, object]) -> "PhysicalOutputTransportConfig":
        if isinstance(document, Mapping):
            return cls.from_mapping(document)
        return cls.from_mapping(_parse_config_json(document))

    @classmethod
    def from_toml(cls, document: bytes | str) -> "PhysicalOutputTransportConfig":
        if type(document) is bytes:
            text = document.decode("utf-8", errors="strict")
        elif type(document) is str:
            text = document
        else:
            raise TypeError("transport TOML config must be UTF-8 bytes or text")
        if text.startswith("\ufeff"):
            raise ValueError("transport config must not contain a UTF-8 BOM")
        value = tomllib.loads(text)
        if "operator_enable" not in value and value.get("mode") != "transmission_enabled":
            # TOMLにはnullがないため、enable不要modeでは省略をnullと同じ意味にする。
            value["operator_enable"] = None
        return cls.from_mapping(value)



class _AuthorizationGrantUseState:
    __slots__ = ("lock", "consumed", "revoked")

    def __init__(self) -> None:
        self.lock = Lock()
        self.consumed = False
        self.revoked = False


@dataclass(frozen=True, slots=True)
class PhysicalOutputTransportAuthorizationGrant:
    """使い切りのexternal authorization snapshot。再送やdurable authorityではない。"""

    target_robot_id: str
    endpoint_id: str
    software_revision: str
    session_id: str
    sequence: int
    request_sha256: str
    safety_binding_sha256: str
    candidate_id: str
    codec_identity_sha256: str
    config_sha256: str
    transmission_permission_sha256: str
    actuation_permission_sha256: str
    authorization_context_sha256: str
    issued_at_s: float
    expires_at_s: float
    schema_version: str = PHYSICAL_OUTPUT_TRANSPORT_AUTHORIZATION_SCHEMA_VERSION
    _state: _AuthorizationGrantUseState = field(
        default_factory=_AuthorizationGrantUseState,
        repr=False,
        compare=False,
    )
    _issuer_token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer_token is not _TRANSPORT_AUTHORIZATION_ISSUER:
            raise ValueError("transport authorization grants can only be issued by runtime composition")
        if self.schema_version != PHYSICAL_OUTPUT_TRANSPORT_AUTHORIZATION_SCHEMA_VERSION:
            raise ValueError("unsupported transport authorization schema_version")
        for name in (
            "target_robot_id",
            "endpoint_id",
            "software_revision",
            "session_id",
            "candidate_id",
        ):
            _identifier(name, getattr(self, name))
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("authorization sequence must be a non-negative integer")
        for name in (
            "request_sha256",
            "safety_binding_sha256",
            "codec_identity_sha256",
            "config_sha256",
            "transmission_permission_sha256",
            "actuation_permission_sha256",
            "authorization_context_sha256",
        ):
            _sha256_hex(name, getattr(self, name))
        issued_at_s = _timestamp("authorization issued_at_s", self.issued_at_s)
        expires_at_s = _timestamp("authorization expires_at_s", self.expires_at_s)
        if expires_at_s <= issued_at_s:
            raise ValueError("authorization expiry must follow issuance")
        if type(self._state) is not _AuthorizationGrantUseState:
            raise TypeError("authorization grant state is invalid")
        object.__setattr__(self, "issued_at_s", issued_at_s)
        object.__setattr__(self, "expires_at_s", expires_at_s)

    def _binding_error(
        self,
        request: PhysicalOutputSendableRequest,
        config: PhysicalOutputTransportConfig,
        permission: PhysicalOutputPermission,
        *,
        authorization_context_sha256: str,
        now_s: float,
    ) -> str | None:
        if type(request) is not PhysicalOutputSendableRequest:
            return "physical_output_transport_authorization_request_mismatch"
        if (
            config.schema_version != PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2
            or not config.external_authorization_required
        ):
            return "physical_output_transport_external_authorization_not_required"
        if type(permission) is not PhysicalOutputPermission:
            return "physical_output_transport_authorization_permission_mismatch"
        evaluation = request.evaluation
        raw_request = request.request
        expected = (
            (self.target_robot_id, raw_request.target_robot_id),
            (self.endpoint_id, raw_request.endpoint_id),
            (self.software_revision, raw_request.software_revision),
            (self.session_id, raw_request.session_id),
            (self.sequence, raw_request.sequence),
            (self.request_sha256, request.request_sha256),
            (self.safety_binding_sha256, request.binding_sha256),
            (self.candidate_id, evaluation.candidate_id),
            (self.codec_identity_sha256, config.expected_codec_identity.identity_sha256),
            (self.config_sha256, sha256(config.to_json_bytes()).hexdigest()),
            (self.transmission_permission_sha256, sha256(permission.to_json_bytes()).hexdigest()),
            (self.authorization_context_sha256, authorization_context_sha256),
        )
        if any(actual != required for actual, required in expected):
            return "physical_output_transport_authorization_grant_mismatch"
        if (
            permission.mode != "transmission_enabled"
            or permission.operator_id is None
            or permission.enable_token_id is None
        ):
            return "physical_output_transport_authorization_permission_mismatch"
        if now_s < self.issued_at_s or now_s >= self.expires_at_s:
            return "physical_output_transport_authorization_grant_expired"
        return None

    def validate_for(
        self,
        request: PhysicalOutputSendableRequest,
        config: PhysicalOutputTransportConfig,
        permission: PhysicalOutputPermission,
        *,
        authorization_context_sha256: str,
        now_s: float,
    ) -> str | None:
        """再検証のみ行う。送信権限の消費はconsume_forだけで行う。"""

        now = _timestamp("authorization now_s", now_s)
        _sha256_hex("authorization_context_sha256", authorization_context_sha256)
        with self._state.lock:
            if self._state.revoked:
                return "physical_output_transport_authorization_grant_revoked"
            if self._state.consumed:
                return "physical_output_transport_authorization_grant_consumed"
            return self._binding_error(
                request,
                config,
                permission,
                authorization_context_sha256=authorization_context_sha256,
                now_s=now,
            )

    def consume_for(
        self,
        request: PhysicalOutputSendableRequest,
        config: PhysicalOutputTransportConfig,
        permission: PhysicalOutputPermission,
        *,
        authorization_context_sha256: str,
        now_s: float,
    ) -> str | None:
        """有効なgrantを一度だけ消費し、2回目以降はfail closedにする。"""

        now = _timestamp("authorization now_s", now_s)
        _sha256_hex("authorization_context_sha256", authorization_context_sha256)
        with self._state.lock:
            if self._state.revoked:
                return "physical_output_transport_authorization_grant_revoked"
            if self._state.consumed:
                return "physical_output_transport_authorization_grant_consumed"
            reason = self._binding_error(
                request,
                config,
                permission,
                authorization_context_sha256=authorization_context_sha256,
                now_s=now,
            )
            if reason is None:
                self._state.consumed = True
            return reason

    def revoke(self) -> None:
        """operator disarm/stop時に未使用grantを無効にする。"""

        with self._state.lock:
            self._state.revoked = True


def _create_physical_output_transport_authorization_grant(
    *,
    target_robot_id: str,
    endpoint_id: str,
    software_revision: str,
    session_id: str,
    sequence: int,
    request_sha256: str,
    safety_binding_sha256: str,
    candidate_id: str,
    codec_identity_sha256: str,
    config_sha256: str,
    transmission_permission_sha256: str,
    actuation_permission_sha256: str,
    authorization_context_sha256: str,
    issued_at_s: float,
    expires_at_s: float,
) -> PhysicalOutputTransportAuthorizationGrant:
    """runtime compositionがgate通過後にだけgrantを作るinternal factory."""

    return PhysicalOutputTransportAuthorizationGrant(
        target_robot_id=target_robot_id,
        endpoint_id=endpoint_id,
        software_revision=software_revision,
        session_id=session_id,
        sequence=sequence,
        request_sha256=request_sha256,
        safety_binding_sha256=safety_binding_sha256,
        candidate_id=candidate_id,
        codec_identity_sha256=codec_identity_sha256,
        config_sha256=config_sha256,
        transmission_permission_sha256=transmission_permission_sha256,
        actuation_permission_sha256=actuation_permission_sha256,
        authorization_context_sha256=authorization_context_sha256,
        issued_at_s=issued_at_s,
        expires_at_s=expires_at_s,
        _issuer_token=_TRANSPORT_AUTHORIZATION_ISSUER,
    )


def _attempt_id(
    *,
    request_sha256: str,
    binding_sha256: str,
    candidate_id: str,
    target_robot_id: str,
    endpoint_id: str,
    software_revision: str,
    session_id: str,
    sequence: int,
) -> str:
    identity = {
        "binding_sha256": binding_sha256,
        "candidate_id": candidate_id,
        "endpoint_id": endpoint_id,
        "request_sha256": request_sha256,
        "sequence": sequence,
        "session_id": session_id,
        "software_revision": software_revision,
        "target_robot_id": target_robot_id,
        "wire_schema_version": PHYSICAL_OUTPUT_WIRE_SCHEMA_VERSION,
    }
    digest = sha256(_canonical_json_bytes(identity)).hexdigest()
    return f"physical-output-attempt/v1:sha256:{digest}"


@dataclass(frozen=True, slots=True)
class PhysicalOutputWireMessage:
    """元のtyped request byte列とP5 identityを保持する論理envelope。"""

    attempt_id: str
    target_robot_id: str
    endpoint_id: str
    software_revision: str
    session_id: str
    sequence: int
    request_sha256: str
    safety_binding_sha256: str
    candidate_id: str
    request_json_bytes: bytes
    request: PhysicalOutputRequest

    def __post_init__(self) -> None:
        _identifier("attempt_id", self.attempt_id)
        _identifier("target_robot_id", self.target_robot_id)
        _identifier("endpoint_id", self.endpoint_id)
        _identifier("software_revision", self.software_revision)
        _identifier("session_id", self.session_id)
        _identifier("candidate_id", self.candidate_id)
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("wire sequence must be a non-negative integer")
        _sha256_hex("request_sha256", self.request_sha256)
        _sha256_hex("safety_binding_sha256", self.safety_binding_sha256)
        if type(self.request_json_bytes) is not bytes:
            raise TypeError("request_json_bytes must be bytes")
        if type(self.request) is not PhysicalOutputRequest:
            raise TypeError("request must be PhysicalOutputRequest")
        request = self.request
        if request.to_json_bytes() != self.request_json_bytes:
            raise ValueError("wire request bytes must be canonical JSON")
        if sha256(self.request_json_bytes).hexdigest() != self.request_sha256:
            raise ValueError("wire request_sha256 does not match request bytes")
        if (
            request.target_robot_id != self.target_robot_id
            or request.endpoint_id != self.endpoint_id
            or request.software_revision != self.software_revision
            or request.session_id != self.session_id
            or request.sequence != self.sequence
        ):
            raise ValueError("wire envelope identity does not match request bytes")
        if physical_output_candidate_id(request) != self.candidate_id:
            raise ValueError("wire candidate_id does not match request bytes")
        expected_attempt_id = _attempt_id(
            request_sha256=self.request_sha256,
            binding_sha256=self.safety_binding_sha256,
            candidate_id=self.candidate_id,
            target_robot_id=self.target_robot_id,
            endpoint_id=self.endpoint_id,
            software_revision=self.software_revision,
            session_id=self.session_id,
            sequence=self.sequence,
        )
        if self.attempt_id != expected_attempt_id:
            raise ValueError("wire attempt_id does not match envelope identity")

    def to_osc_message(self) -> OscMessage:
        return OscMessage(
            address=PHYSICAL_OUTPUT_OSC_ADDRESS,
            arguments=(
                PHYSICAL_OUTPUT_WIRE_SCHEMA_VERSION,
                self.attempt_id,
                self.target_robot_id,
                self.endpoint_id,
                self.software_revision,
                self.session_id,
                str(self.sequence),
                self.request_sha256,
                self.safety_binding_sha256,
                self.candidate_id,
                self.request_json_bytes,
            ),
        )

    def to_bytes(self) -> bytes:
        return encode_osc_message(self.to_osc_message())


def build_physical_output_wire_message(
    request: PhysicalOutputSendableRequest,
    config: PhysicalOutputTransportConfig,
) -> PhysicalOutputWireMessage:
    """validated sendable intentをcanonical byte-preservingOSC envelopeにする。"""

    if type(request) is not PhysicalOutputSendableRequest:
        raise TypeError("wire encoding requires PhysicalOutputSendableRequest")
    if type(config) is not PhysicalOutputTransportConfig:
        raise TypeError("wire encoding requires PhysicalOutputTransportConfig")
    validate_physical_output_sendable_request(request)
    evaluation = request.evaluation
    raw_request = request.request
    if (
        raw_request.target_robot_id != config.target_robot_id
        or raw_request.endpoint_id != config.endpoint.endpoint_id
        or raw_request.software_revision != config.software_revision
    ):
        raise ValueError("wire request target, endpoint, or revision does not match config")
    candidate_id = evaluation.candidate_id
    if candidate_id is None:
        raise ValueError("wire request lacks a P5 candidate identity")
    request_bytes = raw_request.to_json_bytes()
    request_digest = sha256(request_bytes).hexdigest()
    if request_digest != evaluation.request_sha256:
        raise ValueError("wire request digest differs from P5 binding")
    attempt_id = _attempt_id(
        request_sha256=request_digest,
        binding_sha256=evaluation.binding_sha256,
        candidate_id=candidate_id,
        target_robot_id=raw_request.target_robot_id,
        endpoint_id=raw_request.endpoint_id,
        software_revision=raw_request.software_revision,
        session_id=raw_request.session_id,
        sequence=raw_request.sequence,
    )
    return PhysicalOutputWireMessage(
        attempt_id=attempt_id,
        target_robot_id=raw_request.target_robot_id,
        endpoint_id=raw_request.endpoint_id,
        software_revision=raw_request.software_revision,
        session_id=raw_request.session_id,
        sequence=raw_request.sequence,
        request_sha256=request_digest,
        safety_binding_sha256=evaluation.binding_sha256,
        candidate_id=candidate_id,
        request_json_bytes=request_bytes,
        request=raw_request,
    )


def decode_physical_output_wire_message(document: bytes) -> PhysicalOutputWireMessage:
    """generic OSC envelopeを検証し、embedded requestとの意味的一致を確認する。"""

    osc_message = decode_osc_message(document)
    if osc_message.address != PHYSICAL_OUTPUT_OSC_ADDRESS:
        raise ValueError("unexpected physical output OSC address")
    arguments = osc_message.arguments
    if len(arguments) != 11:
        raise ValueError("physical output OSC envelope has an unexpected argument count")
    version, attempt_id, target, endpoint, revision, session, sequence_text = arguments[:7]
    request_digest, binding_digest, candidate_id, request_bytes = arguments[7:]
    if version != PHYSICAL_OUTPUT_WIRE_SCHEMA_VERSION:
        raise ValueError("unsupported physical output wire schema version")
    string_values = (
        attempt_id,
        target,
        endpoint,
        revision,
        session,
        sequence_text,
        request_digest,
        binding_digest,
        candidate_id,
    )
    if any(type(value) is not str for value in string_values) or type(request_bytes) is not bytes:
        raise ValueError("physical output OSC envelope has invalid argument types")
    try:
        sequence = int(sequence_text, 10)
    except ValueError as exc:
        raise ValueError("physical output wire sequence is invalid") from exc
    if str(sequence) != sequence_text:
        raise ValueError("physical output wire sequence is not canonical")
    request = PhysicalOutputRequest.from_json(request_bytes)
    wire_message = PhysicalOutputWireMessage(
        attempt_id=attempt_id,
        target_robot_id=target,
        endpoint_id=endpoint,
        software_revision=revision,
        session_id=session,
        sequence=sequence,
        request_sha256=request_digest,
        safety_binding_sha256=binding_digest,
        candidate_id=candidate_id,
        request_json_bytes=request_bytes,
        request=request,
    )
    if wire_message.to_bytes() != document:
        raise ValueError("physical output OSC envelope is not canonical")
    return wire_message


class PhysicalOutputWireEncoder(Protocol):
    """immutable identityを持ち、logical envelopeをOSC semanticsへ写すpure codec。"""

    @property
    def requires_external_authorization(self) -> bool: ...

    @property
    def identity(self) -> PhysicalOutputCodecIdentity: ...

    def encode(self, envelope: PhysicalOutputWireMessage) -> OscMessage: ...


class GenericPhysicalOutputWireEncoder:
    """既定のphysical-output-wire/v1 OSC envelope encoder。"""

    identity = _GENERIC_CODEC_IDENTITY
    requires_external_authorization = False

    def encode(self, envelope: PhysicalOutputWireMessage) -> OscMessage:
        if type(envelope) is not PhysicalOutputWireMessage:
            raise TypeError("generic wire codec requires PhysicalOutputWireMessage")
        return envelope.to_osc_message()


@dataclass(frozen=True, slots=True)
class PhysicalOutputEncodedDatagram:
    """codec identity、logical envelope、実際に送るOSC bytesを結合した値。"""

    wire_message: PhysicalOutputWireMessage
    codec_identity: PhysicalOutputCodecIdentity
    osc_message: OscMessage
    datagram: bytes
    datagram_sha256: str

    def __post_init__(self) -> None:
        if type(self.wire_message) is not PhysicalOutputWireMessage:
            raise TypeError("encoded datagram requires PhysicalOutputWireMessage")
        if type(self.codec_identity) is not PhysicalOutputCodecIdentity:
            raise TypeError("encoded datagram requires PhysicalOutputCodecIdentity")
        if type(self.osc_message) is not OscMessage:
            raise TypeError("encoded datagram requires OscMessage")
        if type(self.datagram) is not bytes:
            raise TypeError("encoded datagram bytes must be bytes")
        if encode_osc_message(self.osc_message) != self.datagram:
            raise ValueError("encoded datagram bytes differ from OSC semantics")
        _sha256_hex("datagram_sha256", self.datagram_sha256)
        if sha256(self.datagram).hexdigest() != self.datagram_sha256:
            raise ValueError("encoded datagram digest differs from bytes")


@dataclass(frozen=True, slots=True)
class PhysicalOutputTransportAttempt:
    """実行経路の証拠levelとencoded datagram identityを持つattempt。"""

    attempt_id: str
    target_robot_id: str
    endpoint_id: str
    software_revision: str
    session_id: str
    sequence: int
    request_sha256: str
    safety_binding_sha256: str
    candidate_id: str
    codec_identity: PhysicalOutputCodecIdentity
    datagram_sha256: str
    evidence_kind: DatagramEvidenceKind
    started_at_s: float

    def __post_init__(self) -> None:
        for name in (
            "attempt_id",
            "target_robot_id",
            "endpoint_id",
            "software_revision",
            "session_id",
            "candidate_id",
        ):
            _identifier(name, getattr(self, name))
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("attempt sequence must be a non-negative integer")
        for name in ("request_sha256", "safety_binding_sha256", "datagram_sha256"):
            _sha256_hex(name, getattr(self, name))
        if type(self.codec_identity) is not PhysicalOutputCodecIdentity:
            raise TypeError("attempt codec_identity must be PhysicalOutputCodecIdentity")
        if self.evidence_kind not in {"simulated", "local_socket"}:
            raise ValueError("attempt sender evidence kind is unknown")
        object.__setattr__(self, "started_at_s", _timestamp("started_at_s", self.started_at_s))


@dataclass(frozen=True, slots=True)
class PhysicalOutputLocalSendResult:
    """simulatedまたはlocal socket結果。receiver / robot受理を表さない。"""

    status: Literal[
        "not_attempted",
        "accepted_by_local_socket",
        "simulated_acceptance",
        "failed",
    ]
    evidence_kind: Literal["none", "simulated", "local_socket"] = "none"
    datagram_bytes_accepted: int | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "not_attempted",
            "accepted_by_local_socket",
            "simulated_acceptance",
            "failed",
        }:
            raise ValueError("local send result status is unknown")
        if self.status == "not_attempted":
            if (
                self.evidence_kind != "none"
                or self.datagram_bytes_accepted is not None
                or self.error_type is not None
            ):
                raise ValueError("not_attempted result cannot carry send outcome")
        elif self.status in {"accepted_by_local_socket", "simulated_acceptance"}:
            if type(self.datagram_bytes_accepted) is not int or self.datagram_bytes_accepted < 0:
                raise ValueError("accepted byte count must be non-negative")
            if self.error_type is not None:
                raise ValueError("successful local send cannot carry error_type")
            expected_kind = (
                "local_socket"
                if self.status == "accepted_by_local_socket"
                else "simulated"
            )
            if self.evidence_kind != expected_kind:
                raise ValueError("send status differs from its evidence kind")
        else:
            if self.datagram_bytes_accepted is not None:
                raise ValueError("failed local send cannot carry accepted byte count")
            if self.evidence_kind not in {"simulated", "local_socket"}:
                raise ValueError("failed send must preserve sender evidence kind")
            _identifier("error_type", self.error_type)


@dataclass(frozen=True, slots=True)
class PhysicalOutputRecordingResult:
    """recording-only sinkの結果。network sendとは独立したevidence。"""

    status: Literal["not_requested", "recorded", "failed"] = "not_requested"
    byte_count: int | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"not_requested", "recorded", "failed"}:
            raise ValueError("recording result status is unknown")
        if self.status == "not_requested":
            if self.byte_count is not None or self.error_type is not None:
                raise ValueError("not_requested recording cannot carry outcome")
        elif self.status == "recorded":
            if type(self.byte_count) is not int or self.byte_count < 0:
                raise ValueError("recorded byte count must be non-negative")
            if self.error_type is not None:
                raise ValueError("successful recording cannot carry error_type")
        else:
            if self.byte_count is not None:
                raise ValueError("failed recording cannot carry byte_count")
            _identifier("error_type", self.error_type)


@dataclass(frozen=True, slots=True)
class PhysicalOutputAcknowledgementEvidence:
    """receiver側の相関済みACKが未実装であることを明示する。"""

    status: Literal["unavailable"] = "unavailable"
    reason: str = "receiver_ack_not_configured"
    correlated_attempt_id: None = None

    def __post_init__(self) -> None:
        if self.status != "unavailable" or self.correlated_attempt_id is not None:
            raise ValueError("unavailable ACK evidence cannot claim receiver correlation")
        _identifier("acknowledgement reason", self.reason)


@dataclass(frozen=True, slots=True)
class PhysicalOutputTransportResult:
    """attempt、local send、recording、ACKを分けたtransport結果。"""

    status: PhysicalOutputTransportResultStatus
    reason: str | None = None
    wire_message: PhysicalOutputWireMessage | None = None
    datagram: bytes | None = None
    encoded_datagram: PhysicalOutputEncodedDatagram | None = None
    attempt: PhysicalOutputTransportAttempt | None = None
    local_send_result: PhysicalOutputLocalSendResult = PhysicalOutputLocalSendResult(
        status="not_attempted"
    )
    recording_result: PhysicalOutputRecordingResult = PhysicalOutputRecordingResult()
    acknowledgement: PhysicalOutputAcknowledgementEvidence = (
        PhysicalOutputAcknowledgementEvidence()
    )
    lifecycle_dispatch: PhysicalOutputLifecycleDispatchResult | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "disabled",
            "dry_run_ready",
            "recorded",
            "rejected",
            "transmission_attempted",
        }:
            raise ValueError("physical output transport result status is unknown")
        if self.reason is not None:
            _identifier("transport result reason", self.reason)
        if self.wire_message is None:
            if self.datagram is not None or self.encoded_datagram is not None:
                raise ValueError("encoded datagram requires a typed wire_message")
        else:
            if type(self.wire_message) is not PhysicalOutputWireMessage:
                raise TypeError("wire_message must be PhysicalOutputWireMessage")
        if self.encoded_datagram is None:
            if self.datagram is not None:
                raise ValueError("datagram requires typed encoded evidence")
        else:
            if type(self.encoded_datagram) is not PhysicalOutputEncodedDatagram:
                raise TypeError("encoded_datagram must be PhysicalOutputEncodedDatagram")
            if self.encoded_datagram.wire_message != self.wire_message:
                raise ValueError("encoded datagram differs from logical wire envelope")
            if self.datagram != self.encoded_datagram.datagram:
                raise ValueError("transport datagram differs from typed encoded bytes")
        if self.status == "rejected" and self.reason is None:
            raise ValueError("rejected transport result requires a reason")
        if self.status == "dry_run_ready" and self.encoded_datagram is None:
            raise ValueError("dry_run_ready result requires typed encoded bytes")
        if self.status == "recorded" and self.recording_result.status != "recorded":
            raise ValueError("recorded status requires recording evidence")
        if self.status == "recorded" and self.encoded_datagram is None:
            raise ValueError("recorded result requires typed encoded bytes")
        if self.status == "transmission_attempted":
            if self.attempt is None or self.encoded_datagram is None:
                raise ValueError("transmission_attempted result requires attempt evidence")
            if self.local_send_result.status == "not_attempted":
                raise ValueError("transport attempt requires a local sender result")
            if self.attempt.attempt_id != self.wire_message.attempt_id:
                raise ValueError("attempt identity differs from wire message")
            attempt_identity = (
                ("target_robot_id", self.wire_message.target_robot_id),
                ("endpoint_id", self.wire_message.endpoint_id),
                ("software_revision", self.wire_message.software_revision),
                ("session_id", self.wire_message.session_id),
                ("sequence", self.wire_message.sequence),
                ("request_sha256", self.wire_message.request_sha256),
                ("safety_binding_sha256", self.wire_message.safety_binding_sha256),
                ("candidate_id", self.wire_message.candidate_id),
            )
            if any(
                getattr(self.attempt, name) != expected
                for name, expected in attempt_identity
            ):
                raise ValueError("attempt identity differs from logical wire envelope")
            if self.attempt.datagram_sha256 != self.encoded_datagram.datagram_sha256:
                raise ValueError("attempt digest differs from encoded datagram")
            if self.attempt.codec_identity != self.encoded_datagram.codec_identity:
                raise ValueError("attempt codec identity differs from encoded datagram")
            if self.attempt.evidence_kind != self.local_send_result.evidence_kind:
                raise ValueError("attempt sender evidence differs from local result")
        elif self.attempt is not None:
            raise ValueError("only a transmission_attempted result can carry attempt evidence")
        if self.local_send_result.status != "not_attempted" and self.attempt is None:
            raise ValueError("local send result requires a transport attempt")
        if self.local_send_result.status in {
            "accepted_by_local_socket",
            "simulated_acceptance",
        } and self.encoded_datagram is not None:
            if self.local_send_result.datagram_bytes_accepted != len(
                self.encoded_datagram.datagram
            ):
                raise ValueError("accepted byte count differs from encoded datagram")
        if self.acknowledgement.status != "unavailable":
            raise ValueError("this adapter cannot assert receiver acknowledgement")


class PhysicalOutputTransportRecordingSink(Protocol):
    """network送信を行わないlocal datagram recording interface。"""

    def record_datagram(self, datagram: PhysicalOutputEncodedDatagram) -> object: ...


@dataclass(frozen=True, slots=True)
class PhysicalOutputTransportPreparedDispatch:
    """preflight済みdatagram。lifecycle lock内で再検証してからのみ送れる。"""

    _adapter_token: object = field(repr=False, compare=False)
    _config: PhysicalOutputTransportConfig = field(repr=False, compare=False)
    config_sha256: str
    lifecycle: PhysicalOutputLifecycle
    request: PhysicalOutputSendableRequest
    permission: PhysicalOutputPermission
    wire_message: PhysicalOutputWireMessage
    encoded_datagram: PhysicalOutputEncodedDatagram
    destination: PreparedDatagramDestination | None
    prepared_at_s: float
    authorization_grant: PhysicalOutputTransportAuthorizationGrant | None = field(
        repr=False,
        compare=False,
    )
    authorization_context_sha256: str | None = None

    def __post_init__(self) -> None:
        _sha256_hex("prepared config_sha256", self.config_sha256)
        object.__setattr__(self, "prepared_at_s", _timestamp("prepared_at_s", self.prepared_at_s))
        if type(self._config) is not PhysicalOutputTransportConfig:
            raise TypeError("prepared dispatch requires transport config")
        if type(self.lifecycle) is not PhysicalOutputLifecycle:
            raise TypeError("prepared dispatch requires lifecycle")
        if type(self.request) is not PhysicalOutputSendableRequest:
            raise TypeError("prepared dispatch requires P5 sendable request")
        if type(self.permission) is not PhysicalOutputPermission:
            raise TypeError("prepared dispatch requires permission")
        if type(self.wire_message) is not PhysicalOutputWireMessage:
            raise TypeError("prepared dispatch requires wire message")
        if type(self.encoded_datagram) is not PhysicalOutputEncodedDatagram:
            raise TypeError("prepared dispatch requires encoded datagram")
        if self.encoded_datagram.wire_message != self.wire_message:
            raise ValueError("prepared wire and datagram identities differ")
        if self.destination is not None and type(self.destination) is not PreparedDatagramDestination:
            raise TypeError("prepared destination must be typed or None")
        if self.authorization_grant is not None and type(self.authorization_grant) is not PhysicalOutputTransportAuthorizationGrant:
            raise TypeError("prepared authorization grant has an invalid type")
        if self.authorization_context_sha256 is not None:
            _sha256_hex("prepared authorization_context_sha256", self.authorization_context_sha256)


class PhysicalOutputTransportAdapter:
    """latest P5 allowをidentity/freshnessでguardし、必要時だけproviderを呼ぶ。"""

    def __init__(
        self,
        config: PhysicalOutputTransportConfig,
        *,
        sender: DatagramSender | None = None,
        encoder: PhysicalOutputWireEncoder | None = None,
        recording_sink: PhysicalOutputTransportRecordingSink | None = None,
        clock: object | None = None,
    ) -> None:
        if type(config) is not PhysicalOutputTransportConfig:
            raise TypeError("adapter requires PhysicalOutputTransportConfig")
        if sender is not None and not callable(getattr(sender, "prepare", None)):
            raise TypeError("sender must implement prepare")
        if sender is not None and not callable(getattr(sender, "send", None)):
            raise TypeError("sender must implement send")
        if encoder is not None and not callable(getattr(encoder, "encode", None)):
            raise TypeError("encoder must implement encode")
        if recording_sink is not None and not callable(
            getattr(recording_sink, "record_datagram", None)
        ):
            raise TypeError("recording_sink must implement record_datagram")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        selected_encoder = GenericPhysicalOutputWireEncoder() if encoder is None else encoder
        if type(getattr(selected_encoder, "identity", None)) is not PhysicalOutputCodecIdentity:
            raise TypeError("encoder must expose a typed immutable identity")
        if selected_encoder.identity != config.expected_codec_identity:
            raise ValueError("encoder identity does not match expected_codec_identity")
        requires_external_authorization = getattr(
            selected_encoder,
            "requires_external_authorization",
            False,
        )
        if type(requires_external_authorization) is not bool:
            raise TypeError("encoder authorization capability must be a boolean")
        if requires_external_authorization and not (
            config.schema_version == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2
            and config.external_authorization_required
        ):
            raise ValueError(
                "encoder requires transport config v2 external authorization"
            )
        self.config = config
        self._adapter_token = object()
        self._config_sha256 = sha256(config.to_json_bytes()).hexdigest()
        self._sender = sender
        self._encoder = selected_encoder
        self._codec_identity = selected_encoder.identity
        self._encoder_requires_external_authorization = requires_external_authorization
        self._recording_sink = recording_sink
        self._clock = monotonic if clock is None else clock

    def dispatch(
        self,
        lifecycle: PhysicalOutputLifecycle,
        request: PhysicalOutputSendableRequest | PhysicalOutputRequest,
        *,
        now_s: float | None = None,
        authorization_grant: PhysicalOutputTransportAuthorizationGrant | None = None,
        authorization_context_sha256: str | None = None,
    ) -> PhysicalOutputTransportResult:
        """preflightとguarded dispatchを順に行う互換wrapper。"""

        prepared = self.prepare_dispatch(
            lifecycle,
            request,
            now_s=now_s,
            authorization_grant=authorization_grant,
            authorization_context_sha256=authorization_context_sha256,
        )
        if type(prepared) is PhysicalOutputTransportResult:
            return prepared
        return self.dispatch_prepared(prepared, now_s=now_s)

    def prepare_dispatch(
        self,
        lifecycle: PhysicalOutputLifecycle,
        request: PhysicalOutputSendableRequest | PhysicalOutputRequest,
        *,
        now_s: float | None = None,
        authorization_grant: PhysicalOutputTransportAuthorizationGrant | None = None,
        authorization_context_sha256: str | None = None,
    ) -> PhysicalOutputTransportPreparedDispatch | PhysicalOutputTransportResult:
        """送信せずにcodec/endpointをpreflightし、後続のguarded dispatchを準備する。"""

        if type(lifecycle) is not PhysicalOutputLifecycle:
            raise TypeError("prepare_dispatch requires PhysicalOutputLifecycle")
        if self.config.mode == "disabled":
            return PhysicalOutputTransportResult(
                status="disabled",
                reason="physical_output_transport_disabled",
                acknowledgement=PhysicalOutputAcknowledgementEvidence(
                    reason="transport_not_attempted"
                ),
            )
        if type(request) is not PhysicalOutputSendableRequest:
            return self._stop_and_reject(
                lifecycle,
                "physical_output_sendable_binding_required",
                now_s=now_s,
            )
        if lifecycle.state != "active":
            return self._rejected(
                "physical_output_lifecycle_not_active",
                acknowledgement_reason="transport_not_attempted",
            )
        if lifecycle.latest_sendable_request is not request:
            return self._stop_and_reject(
                lifecycle,
                "physical_output_sendable_request_not_latest",
                now_s=now_s,
            )
        try:
            validate_physical_output_sendable_request(request)
        except Exception:
            return self._stop_and_reject(
                lifecycle,
                "physical_output_safety_binding_invalid",
                now_s=now_s,
            )
        raw_request = request.request
        evaluation = request.evaluation
        if (
            raw_request.target_robot_id != self.config.target_robot_id
            or raw_request.endpoint_id != self.config.endpoint.endpoint_id
            or raw_request.software_revision != self.config.software_revision
            or evaluation.target_robot_id != self.config.target_robot_id
            or evaluation.software_revision != self.config.software_revision
        ):
            return self._stop_and_reject(
                lifecycle,
                "physical_output_transport_identity_mismatch",
                now_s=now_s,
            )

        permission = lifecycle.permission
        if type(permission) is not PhysicalOutputPermission:
            return self._stop_and_reject(
                lifecycle,
                "physical_output_permission_unavailable",
                now_s=now_s,
            )
        if self.config.mode == "transmission_enabled":
            gate = self.config.operator_enable
            if (
                permission.mode != "transmission_enabled"
                or gate is None
                or permission.operator_id != gate.operator_id
                or permission.enable_token_id != gate.enable_token_id
            ):
                return self._stop_and_reject(
                    lifecycle,
                    "physical_output_operator_enable_mismatch",
                    now_s=now_s,
                )
            grant_required = (
                self._encoder_requires_external_authorization
                or (
                    self.config.schema_version
                    == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2
                    and self.config.external_authorization_required
                )
            )
            if grant_required:
                if (
                    type(authorization_grant)
                    is not PhysicalOutputTransportAuthorizationGrant
                    or authorization_context_sha256 is None
                ):
                    return self._stop_and_reject(
                        lifecycle,
                        "physical_output_transport_authorization_grant_required",
                        now_s=now_s,
                    )
            elif authorization_grant is not None or authorization_context_sha256 is not None:
                return self._stop_and_reject(
                    lifecycle,
                    "physical_output_transport_authorization_unexpected",
                    now_s=now_s,
                )

        try:
            current_time = self._now(now_s)
            decision = evaluate_physical_output_permission(
                raw_request,
                permission,
                known_target_robot_ids={self.config.target_robot_id},
                known_endpoint_ids={self.config.endpoint.endpoint_id},
                now_s=current_time,
                max_age_s=self.config.max_request_age_s,
            )
        except Exception:
            return self._stop_and_reject(
                lifecycle,
                "physical_output_permission_context_invalid",
                now_s=now_s,
            )
        if decision.status != "accepted":
            reason = decision.reason or "physical_output_permission_rejected"
            if reason in {
                "physical_output_request_stale",
                "physical_output_timestamp_in_future",
            }:
                self._enter_stale_hold(lifecycle, reason, current_time)
                return self._rejected(reason, acknowledgement_reason="transport_not_attempted")
            return self._stop_and_reject(lifecycle, reason, now_s=current_time)

        wire_message: PhysicalOutputWireMessage | None = None
        datagram: bytes | None = None
        encoded_datagram: PhysicalOutputEncodedDatagram | None = None
        try:
            wire_message = build_physical_output_wire_message(request, self.config)
            if (
                self._encoder.identity != self._codec_identity
                or self._codec_identity != self.config.expected_codec_identity
            ):
                raise ValueError("encoder identity changed after adapter construction")
            if self.config.mode == "transmission_enabled" and self.config.external_authorization_required:
                assert authorization_grant is not None
                assert authorization_context_sha256 is not None
                grant_error = authorization_grant.validate_for(
                    request,
                    self.config,
                    permission,
                    authorization_context_sha256=authorization_context_sha256,
                    now_s=current_time,
                )
                if grant_error is not None:
                    raise PermissionError(grant_error)
            osc_message = self._encoder.encode(wire_message)
            if self._encoder.identity != self._codec_identity:
                raise ValueError("encoder identity changed during encoding")
            if type(osc_message) is not OscMessage:
                raise TypeError("wire encoder must return OscMessage")
            datagram = encode_osc_message(osc_message)
            encoded_datagram = PhysicalOutputEncodedDatagram(
                wire_message=wire_message,
                codec_identity=self._codec_identity,
                osc_message=osc_message,
                datagram=datagram,
                datagram_sha256=sha256(datagram).hexdigest(),
            )
            if len(datagram) > self.config.endpoint.max_datagram_bytes:
                raise ValueError("physical_output_datagram_too_large")
        except Exception as exc:
            reason = (
                "physical_output_datagram_too_large"
                if isinstance(exc, ValueError)
                and str(exc) == "physical_output_datagram_too_large"
                else "physical_output_transport_authorization_invalid"
                if isinstance(exc, PermissionError)
                else "physical_output_wire_encoding_failed"
            )
            return self._stop_and_reject(
                lifecycle,
                reason,
                now_s=current_time,
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
            )

        destination: PreparedDatagramDestination | None = None
        if self.config.mode == "transmission_enabled":
            if self._sender is None:
                return self._stop_and_reject(
                    lifecycle,
                    "physical_output_datagram_sender_unavailable",
                    now_s=current_time,
                    wire_message=wire_message,
                    datagram=datagram,
                    encoded_datagram=encoded_datagram,
                )
            try:
                destination = self._sender.prepare(self.config.endpoint)
                if (
                    type(destination) is not PreparedDatagramDestination
                    or destination.endpoint != self.config.endpoint
                ):
                    raise ValueError("prepared destination identity mismatch")
            except Exception:
                return self._stop_and_reject(
                    lifecycle,
                    "physical_output_endpoint_preflight_failed",
                    now_s=current_time,
                    wire_message=wire_message,
                    datagram=datagram,
                    encoded_datagram=encoded_datagram,
                )

        assert wire_message is not None and encoded_datagram is not None and datagram is not None
        return PhysicalOutputTransportPreparedDispatch(
            _adapter_token=self._adapter_token,
            _config=self.config,
            config_sha256=self._config_sha256,
            lifecycle=lifecycle,
            request=request,
            permission=permission,
            wire_message=wire_message,
            encoded_datagram=encoded_datagram,
            destination=destination,
            prepared_at_s=current_time,
            authorization_grant=authorization_grant,
            authorization_context_sha256=authorization_context_sha256,
        )

    def dispatch_prepared(
        self,
        prepared: PhysicalOutputTransportPreparedDispatch,
        *,
        now_s: float | None = None,
    ) -> PhysicalOutputTransportResult:
        """prepared bytesをcurrent lifecycleのfresh gate下だけで送信する。"""

        if type(prepared) is not PhysicalOutputTransportPreparedDispatch:
            raise TypeError("dispatch_prepared requires PhysicalOutputTransportPreparedDispatch")
        lifecycle = prepared.lifecycle
        request = prepared.request
        permission = prepared.permission
        wire_message = prepared.wire_message
        encoded_datagram = prepared.encoded_datagram
        datagram = encoded_datagram.datagram
        if (
            prepared._adapter_token is not self._adapter_token
            or prepared._config is not self.config
            or prepared.config_sha256 != self._config_sha256
            or encoded_datagram.codec_identity != self._codec_identity
            or encoded_datagram.codec_identity != self.config.expected_codec_identity
            or wire_message != encoded_datagram.wire_message
            or wire_message.to_bytes() != encoded_datagram.wire_message.to_bytes()
        ):
            return self._stop_and_reject(
                lifecycle,
                "physical_output_prepared_dispatch_binding_invalid",
                now_s=now_s,
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
            )
        if self.config.mode == "disabled":
            return PhysicalOutputTransportResult(
                status="disabled",
                reason="physical_output_transport_disabled",
                acknowledgement=PhysicalOutputAcknowledgementEvidence(
                    reason="transport_not_attempted"
                ),
            )
        if lifecycle.state != "active" or lifecycle.latest_sendable_request is not request:
            return self._rejected(
                "physical_output_sendable_request_not_latest",
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
                acknowledgement_reason="transport_not_attempted",
            )
        if lifecycle.permission != permission:
            return self._stop_and_reject(
                lifecycle,
                "physical_output_permission_changed_after_preflight",
                now_s=now_s,
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
            )

        try:
            current_time = self._now(now_s)
        except Exception:
            return self._stop_and_reject(
                lifecycle,
                "physical_output_transport_clock_invalid",
                now_s=now_s,
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
            )
        grant_required = (
            self.config.mode == "transmission_enabled"
            and (
                self._encoder_requires_external_authorization
                or (
                    self.config.schema_version
                    == PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2
                    and self.config.external_authorization_required
                )
            )
        )
        if grant_required:
            grant = prepared.authorization_grant
            context_digest = prepared.authorization_context_sha256
            if grant is None or context_digest is None:
                return self._stop_and_reject(
                    lifecycle,
                    "physical_output_transport_authorization_grant_required",
                    now_s=current_time,
                    wire_message=wire_message,
                    datagram=datagram,
                    encoded_datagram=encoded_datagram,
                )
            grant_error = grant.validate_for(
                request,
                self.config,
                permission,
                authorization_context_sha256=context_digest,
                now_s=current_time,
            )
            if grant_error is not None:
                return self._stop_and_reject(
                    lifecycle,
                    grant_error,
                    now_s=current_time,
                    wire_message=wire_message,
                    datagram=datagram,
                    encoded_datagram=encoded_datagram,
                )
        elif prepared.authorization_grant is not None or prepared.authorization_context_sha256 is not None:
            return self._stop_and_reject(
                lifecycle,
                "physical_output_transport_authorization_unexpected",
                now_s=current_time,
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
            )

        if self.config.mode in {"dry_run", "recording"}:
            dispatch = self._claim_without_transport(
                lifecycle,
                request,
                permission,
                current_time,
                wire_message,
                datagram,
            )
            if not dispatch.claimed:
                return self._rejected(
                    dispatch.reason or "physical_output_request_claim_failed",
                    wire_message=wire_message,
                    datagram=datagram,
                    encoded_datagram=encoded_datagram,
                    lifecycle_dispatch=dispatch,
                    acknowledgement_reason="transport_not_attempted",
                )
            if self.config.mode == "dry_run":
                return PhysicalOutputTransportResult(
                    status="dry_run_ready",
                    wire_message=wire_message,
                    datagram=datagram,
                    encoded_datagram=encoded_datagram,
                    acknowledgement=PhysicalOutputAcknowledgementEvidence(
                        reason="transport_not_attempted"
                    ),
                    lifecycle_dispatch=dispatch,
                )
            return self._record_datagram(
                lifecycle,
                encoded_datagram,
                dispatch,
                current_time,
            )

        destination = prepared.destination
        if (
            self._sender is None
            or destination is None
            or type(destination) is not PreparedDatagramDestination
            or destination.endpoint != self.config.endpoint
        ):
            return self._stop_and_reject(
                lifecycle,
                "physical_output_endpoint_preflight_failed",
                now_s=current_time,
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
            )

        attempts: list[PhysicalOutputTransportAttempt] = []
        local_results: list[PhysicalOutputLocalSendResult] = []

        def send_once(active_request: PhysicalOutputSendableRequest) -> DatagramSendReceipt:
            if active_request is not request:
                raise RuntimeError("active request changed before guarded send")
            sender_evidence_kind = self._sender.evidence_kind
            if sender_evidence_kind not in {"simulated", "local_socket"}:
                raise TypeError("sender evidence_kind is invalid")
            started_at_s = self._now(None)
            if grant_required:
                assert prepared.authorization_grant is not None
                assert prepared.authorization_context_sha256 is not None
                grant_error = prepared.authorization_grant.consume_for(
                    active_request,
                    self.config,
                    permission,
                    authorization_context_sha256=prepared.authorization_context_sha256,
                    now_s=started_at_s,
                )
                if grant_error is not None:
                    raise PermissionError(grant_error)
            attempt = PhysicalOutputTransportAttempt(
                attempt_id=wire_message.attempt_id,
                target_robot_id=wire_message.target_robot_id,
                endpoint_id=wire_message.endpoint_id,
                software_revision=wire_message.software_revision,
                session_id=wire_message.session_id,
                sequence=wire_message.sequence,
                request_sha256=wire_message.request_sha256,
                safety_binding_sha256=wire_message.safety_binding_sha256,
                candidate_id=wire_message.candidate_id,
                codec_identity=encoded_datagram.codec_identity,
                datagram_sha256=encoded_datagram.datagram_sha256,
                evidence_kind=sender_evidence_kind,
                started_at_s=started_at_s,
            )
            attempts.append(attempt)
            try:
                receipt = self._sender.send(destination, datagram)
                if type(receipt) is not DatagramSendReceipt:
                    raise TypeError("datagram sender must return DatagramSendReceipt")
                if receipt.evidence_kind != sender_evidence_kind:
                    raise ValueError("datagram receipt evidence differs from sender identity")
                if receipt.datagram_bytes_accepted != len(datagram):
                    raise OSError("local datagram sender returned an incomplete byte count")
            except Exception as exc:
                local_results.append(
                    PhysicalOutputLocalSendResult(
                        status="failed",
                        evidence_kind=sender_evidence_kind,
                        error_type=type(exc).__name__,
                    )
                )
                raise
            local_results.append(
                PhysicalOutputLocalSendResult(
                    status=(
                        "accepted_by_local_socket"
                        if receipt.evidence_kind == "local_socket"
                        else "simulated_acceptance"
                    ),
                    evidence_kind=receipt.evidence_kind,
                    datagram_bytes_accepted=receipt.datagram_bytes_accepted,
                )
            )
            return receipt

        try:
            dispatch = lifecycle.guarded_dispatch_latest_sendable_request(
                request,
                expected_permission=permission,
                expected_request_sha256=request.request_sha256,
                expected_binding_sha256=request.binding_sha256,
                now_s=now_s,
                max_age_s=self.config.max_request_age_s,
                max_safety_age_s=self.config.max_safety_age_s,
                minimum_cadence_s=self.config.minimum_cadence_s,
                operation=send_once,
            )
        except Exception:
            dispatch = None
            if not attempts:
                return self._rejected(
                    "physical_output_guarded_dispatch_failed",
                    wire_message=wire_message,
                    datagram=datagram,
                    encoded_datagram=encoded_datagram,
                    acknowledgement_reason="transport_not_attempted",
                )
        if dispatch is not None and not dispatch.claimed:
            return self._rejected(
                dispatch.reason or "physical_output_request_claim_failed",
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
                lifecycle_dispatch=dispatch,
                acknowledgement_reason="transport_not_attempted",
            )
        if not attempts or not local_results:
            return self._rejected(
                "physical_output_guarded_dispatch_failed",
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
                lifecycle_dispatch=dispatch,
                acknowledgement_reason="transport_not_attempted",
            )
        local_result = local_results[-1]
        reason = (
            "physical_output_local_datagram_send_failed"
            if local_result.status == "failed"
            else None
        )
        return PhysicalOutputTransportResult(
            status="transmission_attempted",
            reason=reason,
            wire_message=wire_message,
            datagram=datagram,
            encoded_datagram=encoded_datagram,
            attempt=attempts[-1],
            local_send_result=local_result,
            acknowledgement=PhysicalOutputAcknowledgementEvidence(
                reason="receiver_ack_not_configured"
            ),
            lifecycle_dispatch=dispatch,
        )

    def stop(
        self,
        lifecycle: PhysicalOutputLifecycle,
        *,
        now_s: float,
        reason: str = "physical_output_transport_stopped",
    ) -> PhysicalOutputLifecycleResult:
        """bounded lifecycle stopを行う。persistent transportは保持しない。"""

        if type(lifecycle) is not PhysicalOutputLifecycle:
            raise TypeError("stop requires PhysicalOutputLifecycle")
        now = _timestamp("now_s", now_s)
        stop_result = lifecycle.operator_stop(reason, now_s=now)
        if stop_result.state == "stopping":
            return lifecycle.complete_stop(now_s=now)
        return stop_result

    def disconnect(
        self,
        lifecycle: PhysicalOutputLifecycle,
        *,
        now_s: float,
        reason: str = "physical_output_transport_disconnected",
    ) -> PhysicalOutputLifecycleResult:
        """disconnect evidenceでactive latest requestをclearする。"""

        if type(lifecycle) is not PhysicalOutputLifecycle:
            raise TypeError("disconnect requires PhysicalOutputLifecycle")
        return lifecycle.source_disconnected(reason, timestamp_s=_timestamp("now_s", now_s))

    def _claim_without_transport(
        self,
        lifecycle: PhysicalOutputLifecycle,
        request: PhysicalOutputSendableRequest,
        permission: PhysicalOutputPermission,
        now_s: float,
        wire_message: PhysicalOutputWireMessage,
        datagram: bytes,
    ) -> PhysicalOutputLifecycleDispatchResult:
        del wire_message, datagram
        try:
            return lifecycle.claim_latest_sendable_request(
                request,
                expected_permission=permission,
                expected_request_sha256=request.request_sha256,
                expected_binding_sha256=request.binding_sha256,
                now_s=now_s,
                max_age_s=self.config.max_request_age_s,
                max_safety_age_s=self.config.max_safety_age_s,
                minimum_cadence_s=self.config.minimum_cadence_s,
            )
        except Exception:
            return PhysicalOutputLifecycleDispatchResult(
                claimed=False,
                state=lifecycle.state,
                reason="physical_output_request_claim_failed",
            )

    def _record_datagram(
        self,
        lifecycle: PhysicalOutputLifecycle,
        encoded_datagram: PhysicalOutputEncodedDatagram,
        dispatch: PhysicalOutputLifecycleDispatchResult,
        now_s: float,
    ) -> PhysicalOutputTransportResult:
        wire_message = encoded_datagram.wire_message
        datagram = encoded_datagram.datagram
        if self._recording_sink is None:
            self._stop_and_reject(
                lifecycle,
                "physical_output_recording_sink_unavailable",
                now_s=now_s,
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
            )
            return self._rejected(
                "physical_output_recording_sink_unavailable",
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
                lifecycle_dispatch=dispatch,
                acknowledgement_reason="transport_not_attempted",
            )
        try:
            self._recording_sink.record_datagram(encoded_datagram)
        except Exception as exc:
            self._stop_and_reject(
                lifecycle,
                "physical_output_recording_failed",
                now_s=now_s,
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
            )
            recording_result = PhysicalOutputRecordingResult(
                status="failed",
                error_type=type(exc).__name__,
            )
            return self._rejected(
                "physical_output_recording_failed",
                wire_message=wire_message,
                datagram=datagram,
                encoded_datagram=encoded_datagram,
                lifecycle_dispatch=dispatch,
                recording_result=recording_result,
                acknowledgement_reason="transport_not_attempted",
            )
        return PhysicalOutputTransportResult(
            status="recorded",
            wire_message=wire_message,
            datagram=datagram,
            encoded_datagram=encoded_datagram,
            recording_result=PhysicalOutputRecordingResult(
                status="recorded",
                byte_count=len(datagram),
            ),
            acknowledgement=PhysicalOutputAcknowledgementEvidence(
                reason="transport_not_attempted"
            ),
            lifecycle_dispatch=dispatch,
        )

    def _now(self, override: float | None) -> float:
        return _timestamp("clock", self._clock() if override is None else override)

    def _stop_and_reject(
        self,
        lifecycle: PhysicalOutputLifecycle,
        reason: str,
        *,
        now_s: float | None,
        wire_message: PhysicalOutputWireMessage | None = None,
        datagram: bytes | None = None,
        encoded_datagram: PhysicalOutputEncodedDatagram | None = None,
    ) -> PhysicalOutputTransportResult:
        try:
            stop_time = self._now(now_s)
        except Exception:
            try:
                lifecycle.fail("physical_output_transport_clock_invalid")
            except Exception:
                pass
        else:
            try:
                stop_result = lifecycle.operator_stop(reason, now_s=stop_time)
                if stop_result.state == "stopping":
                    lifecycle.complete_stop(now_s=stop_time)
            except Exception:
                pass
        return self._rejected(
            reason,
            wire_message=wire_message,
            datagram=datagram,
            encoded_datagram=encoded_datagram,
            acknowledgement_reason="transport_not_attempted",
        )

    def _enter_stale_hold(
        self,
        lifecycle: PhysicalOutputLifecycle,
        reason: str,
        now_s: float,
    ) -> None:
        try:
            lifecycle.source_stale(reason, timestamp_s=now_s)
        except Exception:
            return

    @staticmethod
    def _rejected(
        reason: str,
        *,
        wire_message: PhysicalOutputWireMessage | None = None,
        datagram: bytes | None = None,
        encoded_datagram: PhysicalOutputEncodedDatagram | None = None,
        lifecycle_dispatch: PhysicalOutputLifecycleDispatchResult | None = None,
        recording_result: PhysicalOutputRecordingResult | None = None,
        acknowledgement_reason: str = "transport_not_attempted",
    ) -> PhysicalOutputTransportResult:
        return PhysicalOutputTransportResult(
            status="rejected",
            reason=reason,
            wire_message=wire_message,
            datagram=datagram,
            encoded_datagram=encoded_datagram,
            recording_result=(
                PhysicalOutputRecordingResult()
                if recording_result is None
                else recording_result
            ),
            acknowledgement=PhysicalOutputAcknowledgementEvidence(
                reason=acknowledgement_reason
            ),
            lifecycle_dispatch=lifecycle_dispatch,
        )


__all__ = [
    "PHYSICAL_OUTPUT_OSC_ADDRESS",
    "PHYSICAL_OUTPUT_TRANSPORT_AUTHORIZATION_SCHEMA_VERSION",
    "PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION",
    "PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V1",
    "PHYSICAL_OUTPUT_TRANSPORT_CONFIG_SCHEMA_VERSION_V2",
    "PHYSICAL_OUTPUT_WIRE_SCHEMA_VERSION",
    "PhysicalOutputAcknowledgementEvidence",
    "PhysicalOutputCodecIdentity",
    "PhysicalOutputEncodedDatagram",
    "PhysicalOutputWireEncoder",
    "GenericPhysicalOutputWireEncoder",
    "PhysicalOutputLocalSendResult",
    "PhysicalOutputOperatorEnable",
    "PhysicalOutputRecordingResult",
    "PhysicalOutputTransportAdapter",
    "PhysicalOutputTransportAuthorizationGrant",
    "PhysicalOutputTransportAttempt",
    "PhysicalOutputTransportConfig",
    "PhysicalOutputTransportMode",
    "PhysicalOutputTransportPreparedDispatch",
    "PhysicalOutputTransportResult",
    "PhysicalOutputTransportResultStatus",
    "PhysicalOutputTransportRecordingSink",
    "PhysicalOutputWireMessage",
    "build_physical_output_wire_message",
    "decode_physical_output_wire_message",
]
