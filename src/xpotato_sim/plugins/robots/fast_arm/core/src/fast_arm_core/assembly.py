"""片腕・双腕に共通する配置と名前対応。実機の配線・校正は推論しない。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite, sqrt
import re
from typing import Any

from .definition import FAST_ARM_JOINT_NAMES, FAST_ARM_MODEL_CONTRACT_VERSION

ASSEMBLY_SCHEMA = "fast-arm-assembly/v1"
_ID = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")


def finite_tuple(value: object, size: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or len(value) != size:
        raise ValueError(f"{label} requires {size} values")
    if any(type(v) not in (int, float) for v in value):
        raise ValueError(f"{label} requires finite numbers")
    try:
        result = tuple(float(v) for v in value)
    except OverflowError as exc:
        raise ValueError(f"{label} requires finite numbers") from exc
    if not all(isfinite(v) for v in result):
        raise ValueError(f"{label} requires finite numbers")
    return result


@dataclass(frozen=True, slots=True)
class FastArmInstance:
    """配置は必須。arm_idの綴りから左右の鏡映・実機targetを推論しない。"""
    arm_id: str
    mirror_y: bool
    position_m: tuple[float, float, float]
    quaternion_wxyz: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if type(self.arm_id) is not str or not _ID.fullmatch(self.arm_id):
            raise ValueError("arm_id must be a canonical bounded identifier")
        if type(self.mirror_y) is not bool:
            raise TypeError("mirror_y must be bool")
        object.__setattr__(self, "position_m", finite_tuple(self.position_m, 3, "position_m"))
        q = finite_tuple(self.quaternion_wxyz, 4, "quaternion_wxyz")
        norm = sqrt(sum(v*v for v in q))
        if abs(norm - 1.0) > 1e-10:
            raise ValueError("mount quaternion must be unit length")
        # qと-qを同じ配置identityへ正規化する。
        sign = next((1 if v > 0 else -1 for v in q if v != 0), 1)
        object.__setattr__(self, "quaternion_wxyz", tuple(0.0 if v == 0 else sign*v/norm for v in q))

    def name(self, local_name: str) -> str:
        return f"{self.arm_id}__{local_name}"

    @property
    def joint_names(self) -> tuple[str, ...]:
        return tuple(self.name(name) for name in FAST_ARM_JOINT_NAMES)

    def to_dict(self) -> dict[str, object]:
        return {"arm_id": self.arm_id, "mirror_y": self.mirror_y,
                "position_m": self.position_m, "quaternion_wxyz": self.quaternion_wxyz}


@dataclass(frozen=True, slots=True)
class FastArmAssembly:
    """宣言順を正本とする。名前順のsortや暗黙の片腕追加は行わない。"""
    instances: tuple[FastArmInstance, ...]

    def __post_init__(self) -> None:
        if type(self.instances) is not tuple or not 1 <= len(self.instances) <= 2:
            raise ValueError("assembly requires one or two explicit instances")
        if any(type(item) is not FastArmInstance for item in self.instances):
            raise TypeError("assembly requires validated FastArmInstance values")
        if len({item.arm_id for item in self.instances}) != len(self.instances):
            raise ValueError("duplicate arm_id")

    @property
    def arm_ids(self) -> tuple[str, ...]:
        return tuple(item.arm_id for item in self.instances)

    @property
    def joint_names(self) -> tuple[str, ...]:
        return tuple(name for arm in self.instances for name in arm.joint_names)

    def to_dict(self) -> dict[str, object]:
        return {"schema": ASSEMBLY_SCHEMA, "source_model_contract": FAST_ARM_MODEL_CONTRACT_VERSION,
                "instances": [item.to_dict() for item in self.instances]}

    @property
    def configuration_sha256(self) -> str:
        return sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ArmModelAddresses:
    arm_id: str
    joint_names: tuple[str, ...]
    joint_ids: tuple[int, ...]
    qpos_addresses: tuple[int, ...]
    dof_addresses: tuple[int, ...]
    actuator_ids: tuple[int, ...]
    tip_site_id: int
    tip_body_id: int


def resolve_assembly_addresses(model: Any, assembly: FastArmAssembly) -> tuple[ArmModelAddresses, ...]:
    """コンパイル済みmodelの名前から取得。object freejointの有無や配列順に依存しない。"""
    if type(assembly) is not FastArmAssembly:
        raise TypeError("assembly must be FastArmAssembly")
    resolved = []
    for arm in assembly.instances:
        joints = tuple(int(model.joint(name).id) for name in arm.joint_names)
        # MuJoCo mjJNT_HINGE=3 / mjTRN_JOINT=0。型番号は公式MJCF contract。
        if any(int(model.jnt_type[j]) != 3 for j in joints):
            raise ValueError("FastArm assembly requires hinge joints")
        actuators = tuple(int(model.actuator(arm.name(f"actuator{i+1}")).id) for i in range(len(joints)))
        if any(int(model.actuator_trntype[a]) != 0 or int(model.actuator_trnid[a, 0]) != j
               for a, j in zip(actuators, joints, strict=True)):
            raise ValueError("actuator transmission does not match the named joint")
        site = int(model.site(arm.name("tip")).id)
        body = int(model.body(arm.name("fore_arm_link")).id)
        if int(model.site_bodyid[site]) != body:
            raise ValueError("tip site is attached to the wrong body")
        resolved.append(ArmModelAddresses(arm.arm_id, arm.joint_names, joints,
                        tuple(int(model.jnt_qposadr[j]) for j in joints),
                        tuple(int(model.jnt_dofadr[j]) for j in joints), actuators, site, body))
    return tuple(resolved)
