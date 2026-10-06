"""R7-H contact task/object manifestとscene contract。

The manifest is a strict, immutable input to later scene, evidence, and task
owners.  It deliberately does not load a MuJoCo model, create a scene, or run
physics.  All physical values use SI units and explicit MuJoCo world/object
frames so that unavailable values cannot be replaced by an implicit default.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, overload

from xpotato_sim.runtime.experiment.contracts import (
    EnvironmentRole,
    PluginSelection,
    SemanticRole,
    SemanticRoleRequirement,
    VersionedIdentity,
)


CONTACT_MANIFEST_SCHEMA_VERSION: Final[str] = "contact-task-manifest/v1"
CONTACT_MANIFEST_CONTRACT_VERSION: Final[int] = 1
CONTACT_MANIFEST_DIGEST_ALGORITHM: Final[str] = "sha256"
CONTACT_INITIAL_PENETRATION_TOLERANCE_M: Final[float] = 1e-12
CONTACT_TASK_IDENTITY: Final[VersionedIdentity] = VersionedIdentity(
    "contact_press_hold_task", 1
)
CONTACT_OBJECT_IDENTITY: Final[VersionedIdentity] = VersionedIdentity(
    "contact_cube", 1
)
CONTACT_ENVIRONMENT_ROLE: Final[SemanticRole] = SemanticRole(
    "environment.target_object"
)
CONTACT_TOOL_ROLE: Final[SemanticRole] = SemanticRole("robot.tool_endpoint")
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ContactManifestError(ValueError):
    """Contact manifestのstrict validation failure。"""


class ContactManifestDecodeError(ContactManifestError):
    """Canonical documentをstrictにdecodeできない場合のfailure。"""


def _identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContactManifestError(f"{name} must be a non-empty string")
    if "\x00" in value:
        raise ContactManifestError(f"{name} must not contain NUL")
    return value


def _stable_identifier(name: str, value: object) -> str:
    result = _identifier(name, value)
    if result.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", result):
        raise ContactManifestError(f"{name} must not contain a local path")
    return result


def _finite(
    name: str,
    value: object,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContactManifestError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ContactManifestError(f"{name} must be finite")
    if positive and result <= 0.0:
        raise ContactManifestError(f"{name} must be positive")
    if non_negative and result < 0.0:
        raise ContactManifestError(f"{name} must be non-negative")
    return 0.0 if result == 0.0 else result


# 長さは既存の実行時検査で確定する。数値検査・許容差・変換順は変更しない。
@overload
def _vector(name: str, value: object, *, length: Literal[3], positive: bool = False) -> tuple[float, float, float]: ...

@overload
def _vector(name: str, value: object, *, length: Literal[4], positive: bool = False) -> tuple[float, float, float, float]: ...

@overload
def _vector(name: str, value: object, *, length: int, positive: bool = False) -> tuple[float, ...]: ...


def _vector(name: str, value: object, *, length: int, positive: bool = False) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContactManifestError(f"{name} must be a numeric array")
    if len(value) != length:
        raise ContactManifestError(f"{name} must contain exactly {length} values")
    return tuple(
        _finite(f"{name}[{index}]", item, positive=positive)
        for index, item in enumerate(value)
    )


def _unit_vector(name: str, value: object) -> tuple[float, float, float]:
    result = _vector(name, value, length=3)
    norm = math.sqrt(sum(component * component for component in result))
    if not math.isfinite(norm) or norm <= 0.0 or abs(norm - 1.0) > 1e-12:
        raise ContactManifestError(f"{name} must be a unit vector")
    return result


def _unit_quaternion(name: str, value: object) -> tuple[float, float, float, float]:
    result = _vector(name, value, length=4)
    norm = math.sqrt(sum(component * component for component in result))
    if not math.isfinite(norm) or norm <= 0.0 or abs(norm - 1.0) > 1e-12:
        raise ContactManifestError(f"{name} must be a unit quaternion")
    return result


def _identity(name: str, value: object) -> VersionedIdentity:
    if not isinstance(value, VersionedIdentity):
        raise ContactManifestError(f"{name} must use VersionedIdentity")
    _stable_identifier(f"{name}.name", value.name)
    if type(value.version) is not int or value.version < 1:
        raise ContactManifestError(f"{name}.version must be positive")
    return value


def _selection(name: str, value: object) -> PluginSelection:
    if not isinstance(value, PluginSelection):
        raise ContactManifestError(f"{name} must use PluginSelection")
    _stable_identifier(f"{name}.plugin_id", value.plugin_id)
    if type(value.contract_version) is not int or value.contract_version < 1:
        raise ContactManifestError(f"{name}.contract_version must be positive")
    return value


def _tuple_values(name: str, values: object, *, length: int | None = None) -> tuple[float, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ContactManifestError(f"{name} must be a numeric array")
    if length is not None and len(values) != length:
        raise ContactManifestError(f"{name} must contain exactly {length} values")
    if not values:
        raise ContactManifestError(f"{name} must not be empty")
    return tuple(_finite(f"{name}[{index}]", item) for index, item in enumerate(values))


@dataclass(frozen=True, slots=True)
class ContactMaterial:
    """対象cubeのmaterial identityとvisual色。"""

    material_id: str
    rgba: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        _stable_identifier("material.material_id", self.material_id)
        rgba = _vector("material.rgba", self.rgba, length=4)
        if any(component < 0.0 or component > 1.0 for component in rgba):
            raise ContactManifestError("material.rgba components must be within [0, 1]")
        object.__setattr__(self, "rgba", rgba)


@dataclass(frozen=True, slots=True)
class ContactCubeObject:
    """MuJoCoへ構成するcubeの全physical identity。"""

    identity: VersionedIdentity = CONTACT_OBJECT_IDENTITY
    shape: str = "box"
    position_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    orientation_wxyz: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    size_m: tuple[float, float, float] = (0.05, 0.05, 0.05)
    mass_kg: float = 0.1
    material: ContactMaterial = ContactMaterial("contact_cube_default", (0.7, 0.2, 0.1, 1.0))
    friction: tuple[float, float, float] = (0.8, 0.005, 0.0001)
    body_name: str = "contact_cube"
    geom_name: str = "contact_cube_geom"
    enabled: bool = True
    contype: int = 1
    conaffinity: int = 1
    condim: int = 3

    def __post_init__(self) -> None:
        _identity("object.identity", self.identity)
        if self.shape != "box":
            raise ContactManifestError("object.shape must be 'box'")
        object.__setattr__(
            self, "position_m", _vector("object.position_m", self.position_m, length=3)
        )
        object.__setattr__(
            self,
            "orientation_wxyz",
            _unit_quaternion("object.orientation_wxyz", self.orientation_wxyz),
        )
        object.__setattr__(
            self,
            "size_m",
            _vector("object.size_m", self.size_m, length=3, positive=True),
        )
        object.__setattr__(self, "mass_kg", _finite("object.mass_kg", self.mass_kg, positive=True))
        if not isinstance(self.material, ContactMaterial):
            raise ContactManifestError("object.material must use ContactMaterial")
        friction = _vector("object.friction", self.friction, length=3)
        if any(value < 0.0 for value in friction) or friction[0] <= 0.0:
            raise ContactManifestError(
                "object.friction must be non-negative with positive sliding friction"
            )
        object.__setattr__(self, "friction", friction)
        _stable_identifier("object.body_name", self.body_name)
        _stable_identifier("object.geom_name", self.geom_name)
        if type(self.enabled) is not bool:
            raise ContactManifestError("object.enabled must be a bool")
        for name, value in (
            ("object.contype", self.contype),
            ("object.conaffinity", self.conaffinity),
        ):
            if type(value) is not int or value < 0:
                raise ContactManifestError(
                    f"{name} must be a non-negative integer bitmask"
                )
        if type(self.condim) is not int or self.condim not in (1, 3, 4, 6):
            raise ContactManifestError("object.condim must be one of 1, 3, 4, or 6")

    @property
    def pose_position_m(self) -> tuple[float, float, float]:
        """Compatibility alias for callers that call the object pose explicitly."""

        return self.position_m

    @property
    def pose_orientation_wxyz(self) -> tuple[float, float, float, float]:
        return self.orientation_wxyz


@dataclass(frozen=True, slots=True)
class ContactResetState:
    """trial boundaryで再現するrobot / object / simulator state。"""

    qpos_rad: tuple[float, ...]
    qvel_rad_s: tuple[float, ...]
    # 旧callerが渡すcontrol値をctrlへ正規化するcompatibility alias。
    actuator: tuple[float, ...] = ()
    object_position_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    object_orientation_wxyz: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    simulation_time_s: float = 0.0
    warm_start: tuple[float, ...] = ()
    ctrl: tuple[float, ...] = ()
    act: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        qpos = _tuple_values("reset.qpos_rad", self.qpos_rad)
        qvel = _tuple_values("reset.qvel_rad_s", self.qvel_rad_s)
        if len(qpos) != len(qvel):
            raise ContactManifestError("reset qpos/qvel dimensions must match")
        object.__setattr__(self, "qpos_rad", qpos)
        object.__setattr__(self, "qvel_rad_s", qvel)
        legacy_ctrl = (
            ()
            if self.actuator == ()
            else _tuple_values("reset.actuator", self.actuator)
        )
        declared_ctrl = (
            () if self.ctrl == () else _tuple_values("reset.ctrl", self.ctrl)
        )
        object.__setattr__(self, "ctrl", declared_ctrl or legacy_ctrl)
        object.__setattr__(self, "actuator", declared_ctrl or legacy_ctrl)
        object.__setattr__(
            self,
            "act",
            () if self.act == () else _tuple_values("reset.act", self.act),
        )
        object.__setattr__(
            self,
            "object_position_m",
            _vector("reset.object_position_m", self.object_position_m, length=3),
        )
        object.__setattr__(
            self,
            "object_orientation_wxyz",
            _unit_quaternion(
                "reset.object_orientation_wxyz", self.object_orientation_wxyz
            ),
        )
        object.__setattr__(
            self,
            "simulation_time_s",
            _finite("reset.simulation_time_s", self.simulation_time_s, non_negative=True),
        )
        object.__setattr__(
            self,
            "warm_start",
            ()
            if self.warm_start == ()
            else _tuple_values("reset.warm_start", self.warm_start),
        )
        if self.simulation_time_s != 0.0:
            raise ContactManifestError("reset.simulation_time_s must be exactly zero")


@dataclass(frozen=True, slots=True)
class ContactTarget:
    """対象face、normal、approach directionを固定するtask target。"""

    face: str
    normal_object: tuple[float, float, float]
    approach_direction_world: tuple[float, float, float]
    penetration_band_m: tuple[float, float]

    def __post_init__(self) -> None:
        _stable_identifier("target.face", self.face)
        object.__setattr__(
            self,
            "normal_object",
            _unit_vector("target.normal_object", self.normal_object),
        )
        object.__setattr__(
            self,
            "approach_direction_world",
            _unit_vector(
                "target.approach_direction_world", self.approach_direction_world
            ),
        )
        if not isinstance(self.penetration_band_m, Sequence) or len(self.penetration_band_m) != 2:
            raise ContactManifestError("target.penetration_band_m must contain two values")
        low = _finite("target.penetration_band_m[0]", self.penetration_band_m[0], non_negative=True)
        high = _finite(
            "target.penetration_band_m[1]",
            self.penetration_band_m[1],
            non_negative=True,
        )
        if high < low:
            raise ContactManifestError(
                "target penetration band upper bound must not be below lower bound"
            )
        object.__setattr__(self, "penetration_band_m", (low, high))


@dataclass(frozen=True, slots=True)
class MuJoCoSettingsIdentity:
    """接触結果へ影響するMuJoCo設定のidentity。"""

    timestep_s: float
    integrator: str
    solver: str
    iterations: int
    ls_iterations: int = 20
    noslip_iterations: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timestep_s",
            _finite("mujoco.timestep_s", self.timestep_s, positive=True),
        )
        _stable_identifier("mujoco.integrator", self.integrator)
        _stable_identifier("mujoco.solver", self.solver)
        for name, value in (
            ("mujoco.iterations", self.iterations),
            ("mujoco.ls_iterations", self.ls_iterations),
            ("mujoco.noslip_iterations", self.noslip_iterations),
        ):
            if type(value) is not int or value < 0:
                raise ContactManifestError(f"{name} must be a non-negative integer")
        if self.iterations < 1:
            raise ContactManifestError("mujoco.iterations must be positive")


@dataclass(frozen=True, slots=True)
class ScenePresentationIdentity:
    """viewer presentationをphysical sceneから分離して固定するidentity。"""

    camera_identity: str
    visual_feedback_identity: str

    def __post_init__(self) -> None:
        _stable_identifier("presentation.camera_identity", self.camera_identity)
        _stable_identifier("presentation.visual_feedback_identity", self.visual_feedback_identity)


@dataclass(frozen=True, slots=True)
class ContactSceneContract:
    """Environment / scene ownerが解釈するmanifestのbound contract。"""

    identity: VersionedIdentity
    object: ContactCubeObject
    reset: ContactResetState
    target: ContactTarget
    mujoco: MuJoCoSettingsIdentity
    required_capabilities: frozenset[VersionedIdentity]
    required_robot_roles: frozenset[SemanticRoleRequirement]
    presentation: ScenePresentationIdentity
    enabled: bool = True
    initial_penetration_tolerance_m: float = CONTACT_INITIAL_PENETRATION_TOLERANCE_M

    def __post_init__(self) -> None:
        _identity("scene.identity", self.identity)
        if not isinstance(self.object, ContactCubeObject):
            raise ContactManifestError("scene.object must use ContactCubeObject")
        if not isinstance(self.reset, ContactResetState):
            raise ContactManifestError("scene.reset must use ContactResetState")
        if not isinstance(self.target, ContactTarget):
            raise ContactManifestError("scene.target must use ContactTarget")
        if not isinstance(self.mujoco, MuJoCoSettingsIdentity):
            raise ContactManifestError("scene.mujoco must use MuJoCoSettingsIdentity")
        if not isinstance(self.presentation, ScenePresentationIdentity):
            raise ContactManifestError("scene.presentation must use ScenePresentationIdentity")
        capabilities = frozenset(self.required_capabilities)
        if any(not isinstance(item, VersionedIdentity) for item in capabilities):
            raise ContactManifestError("scene required capabilities must use VersionedIdentity")
        object.__setattr__(self, "required_capabilities", capabilities)
        roles = frozenset(self.required_robot_roles)
        if any(not isinstance(item, SemanticRoleRequirement) for item in roles):
            raise ContactManifestError(
                "scene required robot roles must use SemanticRoleRequirement"
            )
        object.__setattr__(self, "required_robot_roles", roles)
        if type(self.enabled) is not bool:
            raise ContactManifestError("scene.enabled must be a bool")
        object.__setattr__(
            self,
            "initial_penetration_tolerance_m",
            _finite(
                "scene.initial_penetration_tolerance_m",
                self.initial_penetration_tolerance_m,
                non_negative=True,
            ),
        )
        if self.enabled != self.object.enabled:
            raise ContactManifestError("scene/object enabled conditions must match")
        if self.reset.object_position_m != self.object.position_m:
            raise ContactManifestError(
                "scene reset object position must match object pose"
            )
        if self.reset.object_orientation_wxyz != self.object.orientation_wxyz:
            raise ContactManifestError(
                "scene reset object orientation must match object pose"
            )

    @property
    def roles(self) -> tuple[EnvironmentRole, ...]:
        return (
            EnvironmentRole(
                role=CONTACT_ENVIRONMENT_ROLE,
                object_kind="target_object",
                frame="mujoco_world",
                unit="meter",
            ),
        )


@dataclass(frozen=True, slots=True)
class ContactTaskManifest:
    """R7-Hの全再現条件を束ねるversioned task/object manifest。"""

    robot_bundle: PluginSelection
    environment: PluginSelection
    task: PluginSelection
    evaluators: tuple[PluginSelection, ...]
    scene: ContactSceneContract
    software_revision_identity: str
    schema_version: str = CONTACT_MANIFEST_SCHEMA_VERSION
    contract_version: int = CONTACT_MANIFEST_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTACT_MANIFEST_SCHEMA_VERSION:
            raise ContactManifestError("unsupported contact manifest schema version")
        if self.contract_version != CONTACT_MANIFEST_CONTRACT_VERSION:
            raise ContactManifestError("unsupported contact manifest contract version")
        _selection("robot_bundle", self.robot_bundle)
        _selection("environment", self.environment)
        _selection("task", self.task)
        if not isinstance(self.evaluators, tuple):
            object.__setattr__(self, "evaluators", tuple(self.evaluators))
        if not self.evaluators:
            raise ContactManifestError("evaluators must not be empty")
        for index, evaluator in enumerate(self.evaluators):
            _selection(f"evaluators[{index}]", evaluator)
        if len(set(self.evaluators)) != len(self.evaluators):
            raise ContactManifestError("duplicate evaluator selection")
        if not isinstance(self.scene, ContactSceneContract):
            raise ContactManifestError("scene must use ContactSceneContract")
        _stable_identifier("software_revision_identity", self.software_revision_identity)

    @property
    def robot(self) -> PluginSelection:
        return self.robot_bundle

    @property
    def environment_plugin(self) -> PluginSelection:
        return self.environment

    @property
    def task_plugin(self) -> PluginSelection:
        return self.task

    @property
    def evaluation_plugins(self) -> tuple[PluginSelection, ...]:
        return self.evaluators

    @property
    def object(self) -> ContactCubeObject:
        return self.scene.object

    @property
    def object_manifest(self) -> ContactCubeObject:
        return self.scene.object

    @property
    def reset(self) -> ContactResetState:
        return self.scene.reset

    @property
    def task_identity(self) -> VersionedIdentity:
        return VersionedIdentity(self.task.plugin_id, self.task.contract_version)

    def to_document(self) -> dict[str, builtins.object]:
        return _manifest_document(self)


def _identity_document(value: VersionedIdentity) -> dict[str, object]:
    return {"name": value.name, "version": value.version}


def _selection_document(value: PluginSelection) -> dict[str, object]:
    return {"plugin_id": value.plugin_id, "contract_version": value.contract_version}


def _vector_document(value: Sequence[float]) -> list[float]:
    return list(value)


def _role_requirement_document(value: SemanticRoleRequirement) -> dict[str, object]:
    return {
        "role": value.role.name,
        "object_kind": value.object_kind,
        "frame": value.frame,
        "unit": value.unit,
    }


def _manifest_document(manifest: ContactTaskManifest) -> dict[str, object]:
    scene = manifest.scene
    object_value = scene.object
    reset = scene.reset
    target = scene.target
    mujoco = scene.mujoco
    material = object_value.material
    return {
        "contract_version": manifest.contract_version,
        "environment": _selection_document(manifest.environment),
        "evaluators": [_selection_document(item) for item in manifest.evaluators],
        "robot_bundle": _selection_document(manifest.robot_bundle),
        "scene": {
            "enabled": scene.enabled,
            "identity": _identity_document(scene.identity),
            "initial_penetration_tolerance_m": scene.initial_penetration_tolerance_m,
            "mujoco": {
                "integrator": mujoco.integrator,
                "iterations": mujoco.iterations,
                "ls_iterations": mujoco.ls_iterations,
                "noslip_iterations": mujoco.noslip_iterations,
                "solver": mujoco.solver,
                "timestep_s": mujoco.timestep_s,
            },
            "object": {
                "body_name": object_value.body_name,
                "conaffinity": object_value.conaffinity,
                "condim": object_value.condim,
                "contype": object_value.contype,
                "enabled": object_value.enabled,
                "friction": _vector_document(object_value.friction),
                "geom_name": object_value.geom_name,
                "identity": _identity_document(object_value.identity),
                "mass_kg": object_value.mass_kg,
                "material": {
                    "material_id": material.material_id,
                    "rgba": _vector_document(material.rgba),
                },
                "orientation_wxyz": _vector_document(object_value.orientation_wxyz),
                "position_m": _vector_document(object_value.position_m),
                "shape": object_value.shape,
                "size_m": _vector_document(object_value.size_m),
            },
            "presentation": {
                "camera_identity": scene.presentation.camera_identity,
                "visual_feedback_identity": scene.presentation.visual_feedback_identity,
            },
            "required_capabilities": [
                _identity_document(item) for item in sorted(scene.required_capabilities)
            ],
            "required_robot_roles": [
                _role_requirement_document(item)
                for item in sorted(
                    scene.required_robot_roles,
                    key=lambda item: (item.role.name, item.object_kind, item.frame, item.unit),
                )
            ],
            "reset": {
                "act": _vector_document(reset.act),
                "ctrl": _vector_document(reset.ctrl),
                "object_orientation_wxyz": _vector_document(reset.object_orientation_wxyz),
                "object_position_m": _vector_document(reset.object_position_m),
                "qpos_rad": _vector_document(reset.qpos_rad),
                "qvel_rad_s": _vector_document(reset.qvel_rad_s),
                "simulation_time_s": reset.simulation_time_s,
                "warm_start": _vector_document(reset.warm_start),
            },
            "target": {
                "approach_direction_world": _vector_document(target.approach_direction_world),
                "face": target.face,
                "normal_object": _vector_document(target.normal_object),
                "penetration_band_m": _vector_document(target.penetration_band_m),
            },
        },
        "schema_version": manifest.schema_version,
        "software_revision_identity": manifest.software_revision_identity,
        "task": _selection_document(manifest.task),
    }


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ContactManifestError(f"manifest canonical serialization failed: {exc}") from exc


def encode_contact_manifest(manifest: ContactTaskManifest) -> bytes:
    if not isinstance(manifest, ContactTaskManifest):
        raise TypeError("encode_contact_manifest requires ContactTaskManifest")
    return _canonical_json_bytes(manifest.to_document())


def contact_manifest_digest(manifest: ContactTaskManifest) -> str:
    digest = hashlib.sha256(encode_contact_manifest(manifest)).hexdigest()
    return f"{CONTACT_MANIFEST_DIGEST_ALGORITHM}:{digest}"


def compute_manifest_digest(manifest: ContactTaskManifest) -> str:
    return contact_manifest_digest(manifest)


def _require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContactManifestDecodeError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ContactManifestDecodeError(f"{name} keys must be strings")
    return value


def _strict_mapping(value: object, name: str, fields: frozenset[str]) -> Mapping[str, object]:
    mapping = _require_mapping(value, name)
    missing = fields - set(mapping)
    unknown = set(mapping) - fields
    if missing:
        raise ContactManifestDecodeError(f"{name} missing fields: {sorted(missing)}")
    if unknown:
        raise ContactManifestDecodeError(f"{name} has unknown fields: {sorted(unknown)}")
    return mapping


def _as_identity(value: object, name: str) -> VersionedIdentity:
    mapping = _strict_mapping(value, name, frozenset({"name", "version"}))
    try:
        identity_name = mapping["name"]
        if not isinstance(identity_name, str):
            raise ContactManifestDecodeError(f"{name}.name must be a string")
        return VersionedIdentity(identity_name, mapping["version"])  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ContactManifestDecodeError(f"{name} is invalid: {exc}") from exc


def _as_selection(value: object, name: str) -> PluginSelection:
    mapping = _strict_mapping(value, name, frozenset({"plugin_id", "contract_version"}))
    try:
        plugin_id = mapping["plugin_id"]
        if not isinstance(plugin_id, str):
            raise ContactManifestDecodeError(f"{name}.plugin_id must be a string")
        return PluginSelection(
            plugin_id, mapping["contract_version"]  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        raise ContactManifestDecodeError(f"{name} is invalid: {exc}") from exc


def _as_role_requirement(value: object, name: str) -> SemanticRoleRequirement:
    mapping = _strict_mapping(value, name, frozenset({"role", "object_kind", "frame", "unit"}))
    try:
        role_name = mapping["role"]
        object_kind = mapping["object_kind"]
        frame = mapping["frame"]
        unit = mapping["unit"]
        if (not isinstance(role_name, str) or not isinstance(object_kind, str)
                or not isinstance(frame, str) or not isinstance(unit, str)):
            raise ContactManifestDecodeError(f"{name} fields must be strings")
        return SemanticRoleRequirement(
            role=SemanticRole(role_name),
            object_kind=object_kind,
            frame=frame,
            unit=unit,
        )
    except (TypeError, ValueError) as exc:
        raise ContactManifestDecodeError(f"{name} is invalid: {exc}") from exc


def _json_document(value: bytes | str | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return _require_mapping(value, "manifest")
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContactManifestDecodeError("manifest must be UTF-8") from exc
    elif isinstance(value, str):
        text = value
    else:
        raise TypeError("manifest document must be UTF-8 bytes, text, or an object")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ContactManifestDecodeError(f"duplicate field in manifest object: {key!r}")
            result[key] = item
        return result

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ContactManifestDecodeError(f"non-finite JSON constant is not allowed: {value}")
            ),
        )
    except ContactManifestDecodeError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ContactManifestDecodeError(f"manifest JSON decode failed: {exc}") from exc
    return _require_mapping(decoded, "manifest")


def _enumerate_objects(value: object) -> Iterator[tuple[int, object]]:
    """未検証collectionを従来どおり列挙し、各要素は呼出側のdecoderへ渡す。"""
    # 型を保証するcastではない。非iterableのTypeErrorは既存decoder境界で変換する。
    return enumerate(value)  # type: ignore[arg-type]


def decode_contact_manifest(value: bytes | str | Mapping[str, object]) -> ContactTaskManifest:
    root = _strict_mapping(
        _json_document(value),
        "manifest",
        frozenset({
            "contract_version",
            "environment",
            "evaluators",
            "robot_bundle",
            "scene",
            "schema_version",
            "software_revision_identity",
            "task",
        }),
    )
    scene_root = _strict_mapping(
        root["scene"],
        "manifest.scene",
        frozenset({
            "enabled",
            "identity",
            "initial_penetration_tolerance_m",
            "mujoco",
            "object",
            "presentation",
            "required_capabilities",
            "required_robot_roles",
            "reset",
            "target",
        }),
    )
    object_root = _strict_mapping(
        scene_root["object"],
        "manifest.scene.object",
        frozenset({
            "body_name",
            "conaffinity",
            "condim",
            "contype",
            "enabled",
            "friction",
            "geom_name",
            "identity",
            "mass_kg",
            "material",
            "orientation_wxyz",
            "position_m",
            "shape",
            "size_m",
        }),
    )
    material_root = _strict_mapping(
        object_root["material"],
        "manifest.scene.object.material",
        frozenset({"material_id", "rgba"}),
    )
    reset_root = _strict_mapping(
        scene_root["reset"],
        "manifest.scene.reset",
        frozenset({
            "act",
            "ctrl",
            "object_orientation_wxyz",
            "object_position_m",
            "qpos_rad",
            "qvel_rad_s",
            "simulation_time_s",
            "warm_start",
        }),
    )
    target_root = _strict_mapping(
        scene_root["target"],
        "manifest.scene.target",
        frozenset({"approach_direction_world", "face", "normal_object", "penetration_band_m"}),
    )
    mujoco_root = _strict_mapping(
        scene_root["mujoco"],
        "manifest.scene.mujoco",
        frozenset(
            {
                "integrator",
                "iterations",
                "ls_iterations",
                "noslip_iterations",
                "solver",
                "timestep_s",
            }
        ),
    )
    presentation_root = _strict_mapping(
        scene_root["presentation"],
        "manifest.scene.presentation",
        frozenset({"camera_identity", "visual_feedback_identity"}),
    )
    try:
        material = ContactMaterial(
            material_root["material_id"],  # type: ignore[arg-type]
            tuple(material_root["rgba"]),  # type: ignore[arg-type]
        )
        object_value = ContactCubeObject(
            identity=_as_identity(object_root["identity"], "manifest.scene.object.identity"),
            shape=object_root["shape"],  # type: ignore[arg-type]
            position_m=tuple(object_root["position_m"]),  # type: ignore[arg-type]
            orientation_wxyz=tuple(object_root["orientation_wxyz"]),  # type: ignore[arg-type]
            size_m=tuple(object_root["size_m"]),  # type: ignore[arg-type]
            mass_kg=object_root["mass_kg"],  # type: ignore[arg-type]
            material=material,
            friction=tuple(object_root["friction"]),  # type: ignore[arg-type]
            body_name=object_root["body_name"],  # type: ignore[arg-type]
            geom_name=object_root["geom_name"],  # type: ignore[arg-type]
            enabled=object_root["enabled"],  # type: ignore[arg-type]
            contype=object_root["contype"],  # type: ignore[arg-type]
            conaffinity=object_root["conaffinity"],  # type: ignore[arg-type]
            condim=object_root["condim"],  # type: ignore[arg-type]
        )
        reset = ContactResetState(
            qpos_rad=tuple(reset_root["qpos_rad"]),  # type: ignore[arg-type]
            qvel_rad_s=tuple(reset_root["qvel_rad_s"]),  # type: ignore[arg-type]
            ctrl=tuple(reset_root["ctrl"]),  # type: ignore[arg-type]
            act=tuple(reset_root["act"]),  # type: ignore[arg-type]
            object_position_m=tuple(reset_root["object_position_m"]),  # type: ignore[arg-type]
            object_orientation_wxyz=tuple(
                reset_root["object_orientation_wxyz"]  # type: ignore[arg-type]
            ),
            simulation_time_s=reset_root["simulation_time_s"],  # type: ignore[arg-type]
            warm_start=tuple(reset_root["warm_start"]),  # type: ignore[arg-type]
        )
        target = ContactTarget(
            face=target_root["face"],  # type: ignore[arg-type]
            normal_object=tuple(target_root["normal_object"]),  # type: ignore[arg-type]
            approach_direction_world=tuple(
                target_root["approach_direction_world"]  # type: ignore[arg-type]
            ),
            penetration_band_m=tuple(target_root["penetration_band_m"]),  # type: ignore[arg-type]
        )
        scene = ContactSceneContract(
            identity=_as_identity(scene_root["identity"], "manifest.scene.identity"),
            object=object_value,
            reset=reset,
            target=target,
            mujoco=MuJoCoSettingsIdentity(
                timestep_s=mujoco_root["timestep_s"],  # type: ignore[arg-type]
                integrator=mujoco_root["integrator"],  # type: ignore[arg-type]
                solver=mujoco_root["solver"],  # type: ignore[arg-type]
                iterations=mujoco_root["iterations"],  # type: ignore[arg-type]
                ls_iterations=mujoco_root["ls_iterations"],  # type: ignore[arg-type]
                noslip_iterations=mujoco_root["noslip_iterations"],  # type: ignore[arg-type]
            ),
            required_capabilities=frozenset(
                _as_identity(item, f"manifest.scene.required_capabilities[{index}]")
                for index, item in _enumerate_objects(scene_root["required_capabilities"])
            ),
            required_robot_roles=frozenset(
                _as_role_requirement(item, f"manifest.scene.required_robot_roles[{index}]")
                for index, item in _enumerate_objects(scene_root["required_robot_roles"])
            ),
            presentation=ScenePresentationIdentity(
                camera_identity=presentation_root["camera_identity"],  # type: ignore[arg-type]
                visual_feedback_identity=presentation_root["visual_feedback_identity"],  # type: ignore[arg-type]
            ),
            enabled=scene_root["enabled"],  # type: ignore[arg-type]
            initial_penetration_tolerance_m=scene_root[
                "initial_penetration_tolerance_m"
            ],  # type: ignore[arg-type]
        )
        evaluators_value = root["evaluators"]
        if not isinstance(evaluators_value, Sequence) or isinstance(
            evaluators_value, (str, bytes, bytearray)
        ):
            raise ContactManifestDecodeError("manifest.evaluators must be an array")
        return ContactTaskManifest(
            robot_bundle=_as_selection(root["robot_bundle"], "manifest.robot_bundle"),
            environment=_as_selection(root["environment"], "manifest.environment"),
            task=_as_selection(root["task"], "manifest.task"),
            evaluators=tuple(
                _as_selection(item, f"manifest.evaluators[{index}]")
                for index, item in enumerate(evaluators_value)
            ),
            scene=scene,
            software_revision_identity=root["software_revision_identity"],  # type: ignore[arg-type]
            schema_version=root["schema_version"],  # type: ignore[arg-type]
            contract_version=root["contract_version"],  # type: ignore[arg-type]
        )
    except ContactManifestError as exc:
        raise ContactManifestDecodeError(str(exc)) from exc
    except (TypeError, ValueError, KeyError) as exc:
        raise ContactManifestDecodeError(f"manifest value has invalid type: {exc}") from exc


def canonical_encode(manifest: ContactTaskManifest) -> bytes:
    return encode_contact_manifest(manifest)


def canonical_decode(value: bytes | str | Mapping[str, object]) -> ContactTaskManifest:
    return decode_contact_manifest(value)


__all__ = [
    "CONTACT_ENVIRONMENT_ROLE",
    "CONTACT_INITIAL_PENETRATION_TOLERANCE_M",
    "CONTACT_MANIFEST_CONTRACT_VERSION",
    "CONTACT_MANIFEST_DIGEST_ALGORITHM",
    "CONTACT_MANIFEST_SCHEMA_VERSION",
    "CONTACT_OBJECT_IDENTITY",
    "CONTACT_TASK_IDENTITY",
    "CONTACT_TOOL_ROLE",
    "ContactCubeObject",
    "ContactManifestDecodeError",
    "ContactManifestError",
    "ContactMaterial",
    "ContactResetState",
    "ContactSceneContract",
    "ContactTarget",
    "ContactTaskManifest",
    "MuJoCoSettingsIdentity",
    "ScenePresentationIdentity",
    "canonical_decode",
    "canonical_encode",
    "compute_manifest_digest",
    "decode_contact_manifest",
    "encode_contact_manifest",
    "contact_manifest_digest",
]
