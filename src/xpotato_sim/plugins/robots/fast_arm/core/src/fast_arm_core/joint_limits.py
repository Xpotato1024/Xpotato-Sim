"""Pure parsing and value validation for fast_arm software qpos limits."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from math import isfinite
from pathlib import Path

from fast_arm_core.definition import FAST_ARM_ID, FAST_ARM_JOINT_NAMES


FAST_ARM_JOINT_LIMIT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class FastArmJointLimit:
    name: str
    lower_rad: float
    upper_rad: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("joint name must not be empty")
        if not isfinite(self.lower_rad) or not isfinite(self.upper_rad):
            raise ValueError(f"joint {self.name!r} limits must be finite")
        if self.lower_rad >= self.upper_rad:
            raise ValueError(
                f"joint {self.name!r} lower_rad must be less than upper_rad"
            )


@dataclass(frozen=True, slots=True)
class FastArmJointLimitViolation:
    joint_name: str
    candidate_value_rad: float
    lower_rad: float
    upper_rad: float


@dataclass(frozen=True, slots=True)
class FastArmJointLimitConfig:
    schema_version: int
    robot: str
    model: str
    angle_unit: str
    status: str
    joints: tuple[FastArmJointLimit, ...]

    def __post_init__(self) -> None:
        if self.schema_version != FAST_ARM_JOINT_LIMIT_SCHEMA_VERSION:
            raise ValueError(f"unsupported fast_arm joint-limit schema version: {self.schema_version!r}")
        if self.robot != FAST_ARM_ID:
            raise ValueError("fast_arm joint-limit robot must be 'fast_arm'")
        if self.model != FAST_ARM_ID:
            raise ValueError("fast_arm joint-limit model must be 'fast_arm'")
        if self.angle_unit != "rad":
            raise ValueError("fast_arm joint-limit angle_unit must be 'rad'")
        if self.status not in {"provisional", "validated"}:
            raise ValueError("fast_arm joint-limit status must be 'provisional' or 'validated'")
        names = tuple(joint.name for joint in self.joints)
        if len(names) != len(set(names)):
            raise ValueError("fast_arm joint-limit joint names must be unique")
        missing = tuple(name for name in FAST_ARM_JOINT_NAMES if name not in names)
        unknown = tuple(name for name in names if name not in FAST_ARM_JOINT_NAMES)
        if missing or unknown:
            raise ValueError(
                "fast_arm joint-limit joints must contain exactly the required joints: "
                f"missing={missing}, unknown={unknown}"
            )
        if names != FAST_ARM_JOINT_NAMES:
            raise ValueError(
                "fast_arm joint-limit joints must match the canonical order: "
                f"expected {FAST_ARM_JOINT_NAMES}, got {names}"
            )

    def limit_for(self, joint_name: str) -> FastArmJointLimit:
        for joint in self.joints:
            if joint.name == joint_name:
                return joint
        raise KeyError(joint_name)

    @property
    def joint_names(self) -> tuple[str, ...]:
        return tuple(joint.name for joint in self.joints)

    def violations_for_qpos(self, qpos_rad: Sequence[float]) -> tuple[FastArmJointLimitViolation, ...]:
        values = tuple(float(value) for value in qpos_rad)
        if len(values) != len(self.joints):
            raise ValueError(
                "candidate qpos length does not match fast_arm joint-limit contract: "
                f"expected {len(self.joints)}, got {len(values)}"
            )
        return tuple(
            FastArmJointLimitViolation(joint.name, value, joint.lower_rad, joint.upper_rad)
            for joint, value in zip(self.joints, values, strict=True)
            if not isfinite(value) or value < joint.lower_rad or value > joint.upper_rad
        )


def _require_mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a TOML table")
    return value


def _require_string(data: Mapping[str, object], *, name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_schema_version(data: Mapping[str, object]) -> int:
    value = data.get("schema_version")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("schema_version must be an integer")
    return value


def _require_finite_float(data: Mapping[str, object], *, name: str) -> float:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{name} must be a finite number")
    return numeric


def parse_fast_arm_joint_limit_bytes(data: bytes) -> FastArmJointLimitConfig:
    raw = tomllib.load(BytesIO(data))
    schema_version = _require_schema_version(raw)
    robot = _require_string(raw, name="robot")
    model = _require_string(raw, name="model")
    angle_unit = _require_string(raw, name="angle_unit")
    status = _require_string(raw, name="status")
    joints_table = _require_mapping(raw.get("joints"), name="joints")
    joints: list[FastArmJointLimit] = []
    for joint_name, raw_joint in joints_table.items():
        if not isinstance(joint_name, str):
            raise ValueError("joint names must be strings")
        joint = _require_mapping(raw_joint, name=f"joints.{joint_name}")
        joints.append(FastArmJointLimit(
            name=joint_name,
            lower_rad=_require_finite_float(joint, name="lower_rad"),
            upper_rad=_require_finite_float(joint, name="upper_rad"),
        ))
    return FastArmJointLimitConfig(schema_version, robot, model, angle_unit, status, tuple(joints))


def parse_fast_arm_joint_limit_file(path: str | Path) -> FastArmJointLimitConfig:
    resolved_path = Path(path).expanduser().resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(resolved_path)
    return parse_fast_arm_joint_limit_bytes(resolved_path.read_bytes())


__all__ = [
    "FAST_ARM_JOINT_LIMIT_SCHEMA_VERSION",
    "FastArmJointLimit",
    "FastArmJointLimitConfig",
    "FastArmJointLimitViolation",
    "parse_fast_arm_joint_limit_bytes",
    "parse_fast_arm_joint_limit_file",
]
