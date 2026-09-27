"""FastArm固有のphysical-output mappingとrouter observation parser。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite, pi
import re
import struct
import tomllib

from xpotato_sim.schemas.command import JointPositionCommand, PhysicalOutputRequest


FAST_ARM_OUTPUT_MAPPING_SCHEMA_VERSION = "fast-arm-physical-output-mapping/v1"
FAST_ARM_ROUTER_OBSERVATION_SCHEMA_VERSION = "fast-arm-router-observation/v1"
FAST_ARM_JOINT_COMMAND = "joint"
FAST_ARM_JOINT_POSITION_SEMANTICS = "joint_position_command/v1"
_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _identifier(name: str, value: object) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be a non-empty canonical string")
    return value


def _path_segment(name: str, value: object) -> str:
    text = _identifier(name, value)
    if "/" in text or not _SEGMENT_PATTERN.fullmatch(text):
        raise ValueError(f"{name} must be one OSC path segment")
    return text


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


def _parse_json(document: bytes | str) -> object:
    if type(document) is bytes:
        text = document.decode("utf-8", errors="strict")
    elif type(document) is str:
        text = document
    else:
        raise TypeError("mapping must be UTF-8 bytes or text")
    if text.startswith("\ufeff"):
        raise ValueError("mapping must not contain a UTF-8 BOM")
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"unsupported JSON constant: {value}")
        ),
    )


@dataclass(frozen=True, slots=True)
class FastArmOutputMapping:
    """Profileから明示的なjoint名対応とunit変換を提供する。"""

    profile_id: str
    profile_contract_version: int
    model_contract_version: str
    profile_joint_order: tuple[str, ...]
    wire_joint_order: tuple[str, ...]
    joint_map: tuple[tuple[str, str], ...]
    source_id: str
    angle_input_unit: str
    angle_output_unit: str
    angle_offset_unit: str
    joint_coordinate_signs: tuple[tuple[str, int], ...]
    joint_angle_offsets: tuple[tuple[str, float], ...]
    command_semantics: str
    schema_version: str = FAST_ARM_OUTPUT_MAPPING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FAST_ARM_OUTPUT_MAPPING_SCHEMA_VERSION:
            raise ValueError("unsupported FastArm output mapping schema_version")
        _identifier("profile_id", self.profile_id)
        if type(self.profile_contract_version) is not int or self.profile_contract_version < 1:
            raise ValueError("profile_contract_version must be a positive integer")
        _identifier("model_contract_version", self.model_contract_version)
        _path_segment("source_id", self.source_id)
        if type(self.profile_joint_order) is not tuple or not self.profile_joint_order:
            raise TypeError("profile_joint_order must be a non-empty tuple")
        if type(self.wire_joint_order) is not tuple or not self.wire_joint_order:
            raise TypeError("wire_joint_order must be a non-empty tuple")
        for joint in (*self.profile_joint_order, *self.wire_joint_order):
            _identifier("joint name", joint)
        if (
            len(set(self.profile_joint_order)) != len(self.profile_joint_order)
            or len(set(self.wire_joint_order)) != len(self.wire_joint_order)
        ):
            raise ValueError("profile and wire joint orders must be unique")
        if type(self.joint_map) is not tuple or len(self.joint_map) != len(self.profile_joint_order):
            raise ValueError("joint_map must explicitly map every profile joint")
        if any(
            type(pair) is not tuple or len(pair) != 2
            for pair in self.joint_map
        ):
            raise TypeError("joint_map must contain joint-name pairs")
        pairs = tuple((_identifier("profile joint", pair[0]), _identifier("wire joint", pair[1])) for pair in self.joint_map)
        if tuple(pair[0] for pair in pairs) != self.profile_joint_order:
            raise ValueError("joint_map order must match profile_joint_order")
        if {wire for _, wire in pairs} != set(self.wire_joint_order):
            raise ValueError("joint_map must explicitly cover the wire joint order")
        if self.angle_input_unit != "rad" or self.angle_output_unit != "degree":
            raise ValueError("FastArm joint output requires explicit rad to degree units")
        if type(self.angle_offset_unit) is not str or self.angle_offset_unit not in {"rad", "degree"}:
            raise ValueError("angle_offset_unit must explicitly be rad or degree")
        if type(self.joint_coordinate_signs) is not tuple or len(self.joint_coordinate_signs) != len(self.profile_joint_order):
            raise ValueError("joint_coordinate_signs must explicitly cover every profile joint")
        if any(type(pair) is not tuple or len(pair) != 2 for pair in self.joint_coordinate_signs):
            raise TypeError("joint_coordinate_signs must contain joint-name and sign pairs")
        signs = tuple((_identifier("coordinate-sign joint", pair[0]), pair[1]) for pair in self.joint_coordinate_signs)
        if tuple(joint for joint, _ in signs) != self.profile_joint_order:
            raise ValueError("joint_coordinate_signs order must match profile_joint_order")
        if any(type(sign) is not int or sign not in {-1, 1} for _, sign in signs):
            raise ValueError("each joint coordinate sign must be exactly -1 or 1")
        if type(self.joint_angle_offsets) is not tuple or len(self.joint_angle_offsets) != len(self.profile_joint_order):
            raise ValueError("joint_angle_offsets must explicitly cover every profile joint")
        if any(type(pair) is not tuple or len(pair) != 2 for pair in self.joint_angle_offsets):
            raise TypeError("joint_angle_offsets must contain joint-name and offset pairs")
        offsets: list[tuple[str, float]] = []
        for joint, offset in self.joint_angle_offsets:
            joint_name = _identifier("offset joint", joint)
            if type(offset) not in {int, float}:
                raise ValueError("joint angle offsets must be finite built-in numbers")
            try:
                offset_value = float(offset)
            except (OverflowError, ValueError) as exc:
                raise ValueError("joint angle offsets must be finite built-in numbers") from exc
            if not isfinite(offset_value):
                raise ValueError("joint angle offsets must be finite built-in numbers")
            offsets.append((joint_name, offset_value))
        canonical_offsets = tuple(offsets)
        if tuple(joint for joint, _ in canonical_offsets) != self.profile_joint_order:
            raise ValueError("joint_angle_offsets order must match profile_joint_order")
        if self.command_semantics != FAST_ARM_JOINT_POSITION_SEMANTICS:
            raise ValueError("unsupported FastArm joint command semantics")
        object.__setattr__(self, "joint_map", pairs)
        object.__setattr__(self, "joint_coordinate_signs", signs)
        object.__setattr__(self, "joint_angle_offsets", canonical_offsets)

    def to_json_value(self) -> dict[str, object]:
        return {
            "angle_input_unit": self.angle_input_unit,
            "angle_output_unit": self.angle_output_unit,
            "angle_offset_unit": self.angle_offset_unit,
            "command_semantics": self.command_semantics,
            "joint_map": [[source, target] for source, target in self.joint_map],
            "joint_coordinate_signs": [[joint, sign] for joint, sign in self.joint_coordinate_signs],
            "joint_angle_offsets": [[joint, offset] for joint, offset in self.joint_angle_offsets],
            "model_contract_version": self.model_contract_version,
            "profile_contract_version": self.profile_contract_version,
            "profile_id": self.profile_id,
            "profile_joint_order": list(self.profile_joint_order),
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "wire_joint_order": list(self.wire_joint_order),
        }

    def to_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.to_json_value())

    @property
    def identity_sha256(self) -> str:
        return sha256(self.to_json_bytes()).hexdigest()

    @classmethod
    def from_mapping(cls, value: object) -> "FastArmOutputMapping":
        expected = {
            "angle_input_unit",
            "angle_output_unit",
            "angle_offset_unit",
            "command_semantics",
            "joint_map",
            "joint_coordinate_signs",
            "joint_angle_offsets",
            "model_contract_version",
            "profile_contract_version",
            "profile_id",
            "profile_joint_order",
            "schema_version",
            "source_id",
            "wire_joint_order",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("FastArm output mapping fields are incomplete or unknown")
        string_fields = expected - {"joint_map", "joint_coordinate_signs", "joint_angle_offsets", "profile_contract_version", "profile_joint_order", "wire_joint_order"}
        if any(type(value[name]) is not str for name in string_fields):
            raise ValueError("FastArm output mapping string fields are invalid")
        if type(value["profile_contract_version"]) is not int:
            raise ValueError("profile_contract_version must be an integer")
        for name in ("profile_joint_order", "wire_joint_order"):
            if type(value[name]) is not list or not all(type(item) is str for item in value[name]):
                raise ValueError(f"{name} must be a string array")
        joint_map = value["joint_map"]
        if type(joint_map) is not list or any(
            type(pair) is not list or len(pair) != 2 or not all(type(item) is str for item in pair)
            for pair in joint_map
        ):
            raise ValueError("joint_map must be an array of string pairs")
        coordinate_signs = value["joint_coordinate_signs"]
        if type(coordinate_signs) is not list or any(
            type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str or type(pair[1]) is not int
            for pair in coordinate_signs
        ):
            raise ValueError("joint_coordinate_signs must be an array of string and integer pairs")
        angle_offsets = value["joint_angle_offsets"]
        if type(angle_offsets) is not list or any(
            type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str or type(pair[1]) not in {int, float}
            for pair in angle_offsets
        ):
            raise ValueError("joint_angle_offsets must be an array of string and numeric pairs")
        return cls(
            profile_id=value["profile_id"],
            profile_contract_version=value["profile_contract_version"],
            model_contract_version=value["model_contract_version"],
            profile_joint_order=tuple(value["profile_joint_order"]),
            wire_joint_order=tuple(value["wire_joint_order"]),
            joint_map=tuple(tuple(pair) for pair in joint_map),
            source_id=value["source_id"],
            angle_input_unit=value["angle_input_unit"],
            angle_output_unit=value["angle_output_unit"],
            angle_offset_unit=value["angle_offset_unit"],
            joint_coordinate_signs=tuple(tuple(pair) for pair in coordinate_signs),
            joint_angle_offsets=tuple(tuple(pair) for pair in angle_offsets),
            command_semantics=value["command_semantics"],
            schema_version=value["schema_version"],
        )

    @classmethod
    def from_json(cls, document: bytes | str | Mapping[str, object]) -> "FastArmOutputMapping":
        return cls.from_mapping(document if isinstance(document, Mapping) else _parse_json(document))

    @classmethod
    def from_toml(cls, document: bytes | str) -> "FastArmOutputMapping":
        if type(document) is bytes:
            text = document.decode("utf-8", errors="strict")
        elif type(document) is str:
            text = document
        else:
            raise TypeError("mapping TOML must be UTF-8 bytes or text")
        if text.startswith("\ufeff"):
            raise ValueError("mapping TOML must not contain a UTF-8 BOM")
        return cls.from_mapping(tomllib.loads(text))


@dataclass(frozen=True, slots=True)
class FastArmJointWireCommand:
    """routerへ渡すFastArm固有のOSC command semantics。"""

    source_token: str
    target_robot_id: str
    joint_order: tuple[str, ...]
    position_degrees: tuple[float, ...]
    command: str = FAST_ARM_JOINT_COMMAND
    schema_version: str = "fast-arm-joint-wire-command/v1"

    def __post_init__(self) -> None:
        if self.schema_version != "fast-arm-joint-wire-command/v1":
            raise ValueError("unsupported FastArm wire command schema_version")
        _path_segment("source_token", self.source_token)
        _path_segment("target_robot_id", self.target_robot_id)
        if self.command != FAST_ARM_JOINT_COMMAND:
            raise ValueError("unsupported FastArm router command")
        if type(self.joint_order) is not tuple or not self.joint_order:
            raise TypeError("joint_order must be a non-empty tuple")
        if type(self.position_degrees) is not tuple or len(self.position_degrees) != len(self.joint_order):
            raise ValueError("position_degrees must match joint_order")
        if len(set(self.joint_order)) != len(self.joint_order):
            raise ValueError("joint_order must be unique")
        for value in self.position_degrees:
            if type(value) not in {int, float} or not isfinite(float(value)):
                raise ValueError("joint position must be finite")
            _float32("joint position", float(value))

    @property
    def address(self) -> str:
        return f"/{self.source_token}/{self.target_robot_id}/{self.command}"

    @property
    def osc_arguments(self) -> tuple[float, ...]:
        return tuple(_float32("joint position", value) for value in self.position_degrees)


def _float32(name: str, value: float) -> float:
    try:
        result = struct.unpack("!f", struct.pack("!f", value))[0]
    except (OverflowError, struct.error) as exc:
        raise ValueError(f"{name} must fit in OSC float32") from exc
    if not isfinite(result):
        raise ValueError(f"{name} must fit in finite OSC float32")
    return result


def build_fast_arm_joint_wire_command(
    request: PhysicalOutputRequest,
    mapping: FastArmOutputMapping,
    *,
    attempt_id: str,
) -> FastArmJointWireCommand:
    if type(request) is not PhysicalOutputRequest:
        raise TypeError("FastArm mapping requires PhysicalOutputRequest")
    if type(mapping) is not FastArmOutputMapping:
        raise TypeError("FastArm mapping requires FastArmOutputMapping")
    if request.command_semantics != mapping.command_semantics:
        raise ValueError("request command semantics differ from FastArm mapping")
    if type(request.command) is not JointPositionCommand:
        raise TypeError("FastArm output supports only JointPositionCommand")
    if len(request.command.joint_angles_rad) != len(mapping.profile_joint_order):
        raise ValueError("request joint values do not match the declared profile order")
    _identifier("attempt_id", attempt_id)
    source_digest = sha256(
        _canonical_json_bytes(
            {
                "attempt_id": attempt_id,
                "mapping_sha256": mapping.identity_sha256,
                "request_sha256": sha256(request.to_json_bytes()).hexdigest(),
            }
        )
    ).hexdigest()
    source_token = f"{mapping.source_id}-{source_digest[:24]}"
    profile_values = dict(zip(mapping.profile_joint_order, request.command.joint_angles_rad, strict=True))
    map_by_wire = {wire: profile for profile, wire in mapping.joint_map}
    sign_by_joint = dict(mapping.joint_coordinate_signs)
    offset_by_joint = dict(mapping.joint_angle_offsets)

    def output_degrees(profile_joint: str) -> float:
        signed_value = float(profile_values[profile_joint]) * sign_by_joint[profile_joint]
        offset = offset_by_joint[profile_joint]
        if mapping.angle_offset_unit == "rad":
            return (signed_value + offset) * 180.0 / pi
        return signed_value * 180.0 / pi + offset

    degrees = tuple(
        output_degrees(map_by_wire[wire])
        for wire in mapping.wire_joint_order
    )
    return FastArmJointWireCommand(
        source_token=source_token,
        target_robot_id=request.target_robot_id,
        joint_order=mapping.wire_joint_order,
        position_degrees=degrees,
    )


@dataclass(frozen=True, slots=True)
class FastArmRouterCommandObservation:
    """routerが受け付けたcommandのcorrelation facts。"""

    target_robot_id: str
    source_token: str
    command: str
    position_degrees: tuple[float, ...]
    schema_version: str = FAST_ARM_ROUTER_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FAST_ARM_ROUTER_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("unsupported FastArm router observation schema_version")
        _path_segment("target_robot_id", self.target_robot_id)
        _path_segment("source_token", self.source_token)
        if self.command != FAST_ARM_JOINT_COMMAND:
            raise ValueError("router observation command is not joint")
        if type(self.position_degrees) is not tuple or not self.position_degrees:
            raise TypeError("router position_degrees must be a non-empty tuple")
        for value in self.position_degrees:
            if type(value) is not float or not isfinite(value):
                raise ValueError("router arguments must be finite floats")
            _float32("router argument", value)


def parse_fast_arm_router_observation(
    address: str,
    arguments: tuple[object, ...],
) -> FastArmRouterCommandObservation:
    if type(address) is not str:
        raise TypeError("router observation address must be a string")
    parts = address.split("/")
    if len(parts) != 4 or parts[0] != "" or parts[1] != "router" or parts[3] != "command":
        raise ValueError("unexpected FastArm router observation address")
    target_robot_id = _path_segment("target_robot_id", parts[2])
    if type(arguments) is not tuple or len(arguments) != 3 or any(type(item) is not str for item in arguments):
        raise ValueError("router observation requires exactly three OSC strings")
    source_token = _path_segment("source_token", arguments[0])
    command = arguments[1]
    if command != FAST_ARM_JOINT_COMMAND:
        raise ValueError("router observation command is not joint")
    encoded_values = arguments[2]
    if not encoded_values.startswith("[") or not encoded_values.endswith("]"):
        raise ValueError("router joint arguments are not a list")
    body = encoded_values[1:-1].strip()
    if not body:
        raise ValueError("router joint arguments are empty")
    pieces = body.split(",")
    try:
        values = tuple(float(piece.strip()) for piece in pieces)
    except ValueError as exc:
        raise ValueError("router joint arguments are invalid floats") from exc
    if any(not isfinite(value) for value in values):
        raise ValueError("router joint arguments must be finite")
    return FastArmRouterCommandObservation(
        target_robot_id=target_robot_id,
        source_token=source_token,
        command=command,
        position_degrees=tuple(_float32("router argument", value) for value in values),
    )


def router_observation_matches(
    observation: FastArmRouterCommandObservation,
    expected: FastArmJointWireCommand,
) -> bool:
    return (
        type(observation) is FastArmRouterCommandObservation
        and type(expected) is FastArmJointWireCommand
        and observation.target_robot_id == expected.target_robot_id
        and observation.source_token == expected.source_token
        and observation.command == expected.command
        and observation.position_degrees == expected.osc_arguments
    )


__all__ = [
    "FAST_ARM_JOINT_COMMAND",
    "FAST_ARM_JOINT_POSITION_SEMANTICS",
    "FAST_ARM_OUTPUT_MAPPING_SCHEMA_VERSION",
    "FAST_ARM_ROUTER_OBSERVATION_SCHEMA_VERSION",
    "FastArmJointWireCommand",
    "FastArmOutputMapping",
    "FastArmRouterCommandObservation",
    "build_fast_arm_joint_wire_command",
    "parse_fast_arm_router_observation",
    "router_observation_matches",
]
