"""同じassembly関節名から各腕の既存physical requestへ投影する。送信・許可は持たない。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from fast_arm_core.assembly import FastArmAssembly
from fast_arm_core.definition import FAST_ARM_JOINT_NAMES
from xpotato_sim.schemas.command import JointPositionCommand, PhysicalOutputRequest
from .physical_output import FastArmOutputMapping, build_fast_arm_joint_wire_command


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be a canonical non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class FastArmOutputBinding:
    """instanceから実装既存target/endpointへ明示対応。左右の符号はmappingで個別指定。"""
    arm_id: str
    target_robot_id: str
    endpoint_id: str
    mapping: FastArmOutputMapping

    def __post_init__(self) -> None:
        for label in ("arm_id", "target_robot_id", "endpoint_id"):
            _identifier(getattr(self, label), label)
        if type(self.mapping) is not FastArmOutputMapping:
            raise TypeError("binding requires an explicit FastArmOutputMapping")
        if self.mapping.profile_joint_order != FAST_ARM_JOINT_NAMES:
            raise ValueError("mapping must use the core local joint order")


@dataclass(frozen=True, slots=True)
class FastArmBoundRequest:
    arm_id: str
    request: PhysicalOutputRequest
    mapping: FastArmOutputMapping


@dataclass(frozen=True, slots=True)
class FastArmAssemblyRequestBatch:
    """全腕分を検証したrequested-level記録。双腕送信可能性・原子的送信は保証しない。"""
    assembly_sha256: str
    model_sha256: str
    items: tuple[FastArmBoundRequest, ...]

    @property
    def identity_sha256(self) -> str:
        value = {"schema": "fast-arm-assembly-output-batch/v1",
                 "assembly_sha256": self.assembly_sha256, "model_sha256": self.model_sha256,
                 "items": [{"arm_id": item.arm_id, "mapping_sha256": item.mapping.identity_sha256,
                            "request": item.request.to_json_value()} for item in self.items]}
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()


def build_fast_arm_assembly_requests(
    assembly: FastArmAssembly, command: JointPositionCommand, *,
    joint_names: tuple[str, ...], bindings: tuple[FastArmOutputBinding, ...],
    model_sha256: str, session_id: str, sequence: int, cadence_s: float,
    software_revision: str,
) -> FastArmAssemblyRequestBatch:
    """左右を一度に検証し、名前で分割する。scene qposのsliceや左右の暗黙補完は禁止。"""
    if type(assembly) is not FastArmAssembly or type(command) is not JointPositionCommand:
        raise TypeError("explicit assembly and JointPositionCommand required")
    if (type(joint_names) is not tuple or any(type(name) is not str for name in joint_names)
            or len(joint_names) != len(set(joint_names))
            or set(joint_names) != set(assembly.joint_names)
            or len(joint_names) != len(command.joint_angles_rad)):
        raise ValueError("command must name every assembly joint exactly once")
    if (type(bindings) is not tuple or any(type(item) is not FastArmOutputBinding for item in bindings)
            or len(bindings) != len(assembly.instances)
            or {item.arm_id for item in bindings} != set(assembly.arm_ids)):
        raise ValueError("output bindings must cover every assembly arm exactly once")
    # 既存OSCアドレスはendpoint_idを含まない。同一targetを両腕へ割り当てることは拒否。
    if len({item.target_robot_id for item in bindings}) != len(bindings):
        raise ValueError("assembly arms require distinct OSC targets")
    if (type(model_sha256) is not str or len(model_sha256) != 64
            or any(c not in "0123456789abcdef" for c in model_sha256)):
        raise ValueError("model_sha256 must be an explicit lowercase SHA-256")
    _identifier(session_id, "session_id")
    named = dict(zip(joint_names, command.joint_angles_rad, strict=True))
    by_arm = {item.arm_id: item for item in bindings}
    items = []
    for arm in assembly.instances:
        binding = by_arm[arm.arm_id]
        per_arm = JointPositionCommand(timestamp_s=command.timestamp_s,
                      joint_angles_rad=tuple(named[name] for name in arm.joint_names))
        request = PhysicalOutputRequest(
            target_robot_id=binding.target_robot_id, endpoint_id=binding.endpoint_id,
            command_semantics=binding.mapping.command_semantics, command=per_arm,
            session_id=session_id + ":" + arm.arm_id, sequence=sequence,
            timestamp_s=command.timestamp_s, cadence_s=cadence_s, software_revision=software_revision,
        )
        # 既存pure変換でOSC pathとfloat32まで検査。途中失敗してもI/Oは起きない。
        build_fast_arm_joint_wire_command(request, binding.mapping, attempt_id="assembly-validation")
        items.append(FastArmBoundRequest(arm.arm_id, request, binding.mapping))
    return FastArmAssemblyRequestBatch(assembly.configuration_sha256, model_sha256, tuple(items))
