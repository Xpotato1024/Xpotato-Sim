"""R7-H contact taskのMuJoCo scene compositionとtrial reset owner。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Final
import xml.etree.ElementTree as ET

from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
from xpotato_sim.runtime.composition.robot_bundle import RobotBundle
from xpotato_sim.runtime.experiment.contracts import (
    EnvironmentRole,
    VersionedIdentity,
)
from xpotato_sim.runtime.contact.manifest import (
    CONTACT_OBJECT_IDENTITY,
    ContactTaskManifest,
    contact_manifest_digest,
)


CONTACT_SCENE_CONTRACT_VERSION: Final[int] = 1
CONTACT_SCENE_IDENTITY: Final[VersionedIdentity] = VersionedIdentity(
    "contact_cube_scene", 1
)
_OBJECT_FREEJOINT_SUFFIX: Final[str] = "_freejoint"


class ContactSceneError(ValueError):
    """contact sceneの構成、model compatibility、resetのfail-closed error。"""


def _format_values(values: Sequence[float]) -> str:
    """浮動小数点属性をlocale非依存の決定的なXML文字列へ変換する。"""

    return " ".join(format(float(value), ".17g") for value in values)


def _require_utf8_xml(model_xml: bytes) -> ET.Element:
    if not isinstance(model_xml, bytes) or not model_xml:
        raise ContactSceneError("base MuJoCo model XML must be non-empty UTF-8 bytes")
    if model_xml.startswith(b"\xef\xbb\xbf"):
        raise ContactSceneError("base MuJoCo model XML must not contain a UTF-8 BOM")
    try:
        text = model_xml.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContactSceneError("base MuJoCo model XML must be UTF-8") from exc
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ContactSceneError("base MuJoCo model XML is not well-formed") from exc
    if root.tag != "mujoco":
        raise ContactSceneError("base MuJoCo model XML root must be <mujoco>")
    return root


def _named_elements(root: ET.Element, tag: str) -> set[str]:
    return {
        str(element.attrib["name"])
        for element in root.iter(tag)
        if "name" in element.attrib
    }


def _find_or_create_asset(root: ET.Element) -> ET.Element:
    assets = root.find("asset")
    if assets is not None:
        return assets
    worldbody = root.find("worldbody")
    if worldbody is None:
        raise ContactSceneError("base MuJoCo model must declare <worldbody>")
    assets = ET.Element("asset")
    root.insert(list(root).index(worldbody), assets)
    return assets


def _require_worldbody(root: ET.Element) -> ET.Element:
    worldbody = root.find("worldbody")
    if worldbody is None:
        raise ContactSceneError("base MuJoCo model must declare <worldbody>")
    return worldbody


def _compose_model_xml(
    model_xml: bytes,
    *,
    manifest: ContactTaskManifest,
) -> bytes:
    """Manifestからobjectを一度だけ追加した決定的なMJCF bytesを構成する。"""

    root = _require_utf8_xml(model_xml)
    worldbody = _require_worldbody(root)
    assets = _find_or_create_asset(root)
    object_value = manifest.scene.object
    if object_value.identity != CONTACT_OBJECT_IDENTITY:
        raise ContactSceneError(
            "unsupported contact object identity: "
            f"{object_value.identity.canonical_id}"
        )
    if manifest.scene.identity != CONTACT_SCENE_IDENTITY:
        raise ContactSceneError(
            "unsupported contact scene identity: "
            f"{manifest.scene.identity.canonical_id}"
        )
    body_name = object_value.body_name
    geom_name = object_value.geom_name
    material_name = object_value.material.material_id
    freejoint_name = f"{body_name}{_OBJECT_FREEJOINT_SUFFIX}"

    existing_bodies = _named_elements(root, "body")
    existing_geoms = _named_elements(root, "geom")
    existing_materials = _named_elements(root, "material")
    existing_joints = _named_elements(root, "joint")
    for name, existing, kind in (
        (body_name, existing_bodies, "body"),
        (geom_name, existing_geoms, "geom"),
        (material_name, existing_materials, "material"),
        (freejoint_name, existing_joints, "joint"),
    ):
        if name in existing:
            raise ContactSceneError(
                f"contact object {kind} name collides with base model: {name!r}"
            )

    if object_value.enabled:
        ET.SubElement(
            assets,
            "material",
            {
                "name": material_name,
                "rgba": _format_values(object_value.material.rgba),
            },
        )
        body = ET.SubElement(
            worldbody,
            "body",
            {
                "name": body_name,
                "pos": _format_values(object_value.position_m),
                "quat": _format_values(object_value.orientation_wxyz),
            },
        )
        ET.SubElement(body, "freejoint", {"name": freejoint_name})
        # MuJoCo box sizeは各local axisのhalf-extentであり、manifestのsize_mも同じ表現である。
        ET.SubElement(
            body,
            "geom",
            {
                "name": geom_name,
                "type": object_value.shape,
                "size": _format_values(object_value.size_m),
                "mass": format(object_value.mass_kg, ".17g"),
                "material": material_name,
                "friction": _format_values(object_value.friction),
                "contype": str(object_value.contype),
                "conaffinity": str(object_value.conaffinity),
                "condim": str(object_value.condim),
            },
        )

    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


def _identity_matches(
    selection_id: str,
    selection_version: int,
    actual: VersionedIdentity,
    *,
    axis: str,
) -> None:
    if selection_id != actual.name or selection_version != actual.version:
        raise ContactSceneError(
            f"contact scene {axis} identity mismatch: "
            f"manifest={selection_id}/v{selection_version}, "
            f"actual={actual.canonical_id}"
        )


def validate_contact_scene_compatibility(
    manifest: ContactTaskManifest,
    *,
    robot_bundle_identity: VersionedIdentity | None = None,
    environment_identity: VersionedIdentity | None = None,
    viewer_scene_identity: str | None = None,
    robot_capabilities: Sequence[VersionedIdentity] | None = None,
    robot_roles: Sequence[EnvironmentRole] | None = None,
) -> None:
    """manifestのRobot / Environment / viewer / role境界を明示照合する。"""

    if not isinstance(manifest, ContactTaskManifest):
        raise TypeError("contact scene compatibility requires ContactTaskManifest")
    if robot_bundle_identity is not None:
        if not isinstance(robot_bundle_identity, VersionedIdentity):
            raise TypeError("robot bundle identity must use VersionedIdentity")
        _identity_matches(
            manifest.robot_bundle.plugin_id,
            manifest.robot_bundle.contract_version,
            robot_bundle_identity,
            axis="robot bundle",
        )
    if environment_identity is not None:
        if not isinstance(environment_identity, VersionedIdentity):
            raise TypeError("environment identity must use VersionedIdentity")
        _identity_matches(
            manifest.environment.plugin_id,
            manifest.environment.contract_version,
            environment_identity,
            axis="environment",
        )
    if viewer_scene_identity is not None:
        if not isinstance(viewer_scene_identity, str) or not viewer_scene_identity:
            raise TypeError("viewer scene identity must be a non-empty string")
        expected = manifest.scene.presentation.visual_feedback_identity
        if viewer_scene_identity != expected:
            raise ContactSceneError(
                "contact scene viewer identity mismatch: "
                f"manifest={expected!r}, actual={viewer_scene_identity!r}"
            )
    if robot_capabilities is not None:
        capabilities = frozenset(robot_capabilities)
        if any(not isinstance(item, VersionedIdentity) for item in capabilities):
            raise TypeError("robot capabilities must use VersionedIdentity")
        missing = manifest.scene.required_capabilities - capabilities
        if missing:
            raise ContactSceneError(
                "contact scene required robot capability is unavailable: "
                + ", ".join(item.canonical_id for item in sorted(missing))
            )
    if robot_roles is not None:
        descriptors = tuple(robot_roles)
        if any(not isinstance(item, EnvironmentRole) for item in descriptors):
            raise TypeError("robot roles must use EnvironmentRole")
        for requirement in sorted(
            manifest.scene.required_robot_roles,
            key=lambda item: (item.role.name, item.object_kind, item.frame, item.unit),
        ):
            if not any(requirement.matches(descriptor) for descriptor in descriptors):
                raise ContactSceneError(
                    "contact scene required robot role is unavailable: "
                    f"{requirement.role.name}/{requirement.object_kind}/"
                    f"{requirement.frame}/{requirement.unit}"
                )


def _mujoco_enum_value(mujoco: object, enum_name: str, value: str) -> int:
    normalized = value.strip().casefold()
    enum = getattr(mujoco, enum_name)
    prefix = {
        "mjtIntegrator": "mjint_",
        "mjtSolver": "mjsol_",
    }.get(enum_name)
    if prefix is None:
        raise ContactSceneError(f"unsupported MuJoCo enum: {enum_name!r}")
    for candidate in dir(enum):
        matches_prefix = candidate.casefold().startswith(prefix)
        if matches_prefix and candidate[len(prefix) :].casefold() == normalized:
            return int(getattr(enum, candidate))
    raise ContactSceneError(f"unsupported MuJoCo {enum_name} value: {value!r}")


def _configure_mujoco_options(model: object, manifest: ContactTaskManifest) -> None:
    try:
        import mujoco
    except ImportError as exc:  # pragma: no cover - dependency is project-required
        raise ContactSceneError("MuJoCo is required for contact scene loading") from exc

    settings = manifest.scene.mujoco
    try:
        model.opt.timestep = settings.timestep_s
        model.opt.integrator = _mujoco_enum_value(
            mujoco, "mjtIntegrator", settings.integrator
        )
        model.opt.solver = _mujoco_enum_value(mujoco, "mjtSolver", settings.solver)
        model.opt.iterations = settings.iterations
        model.opt.ls_iterations = settings.ls_iterations
        model.opt.noslip_iterations = settings.noslip_iterations
    except (AttributeError, TypeError, ValueError) as exc:
        raise ContactSceneError("MuJoCo setting identity cannot be applied") from exc


def _joint_spans(model: object, joint_type: int) -> tuple[int, int]:
    try:
        import mujoco
    except ImportError as exc:  # pragma: no cover
        raise ContactSceneError("MuJoCo is required for contact scene reset") from exc
    spans = {
        int(mujoco.mjtJoint.mjJNT_FREE): (7, 6),
        int(mujoco.mjtJoint.mjJNT_BALL): (4, 3),
        int(mujoco.mjtJoint.mjJNT_SLIDE): (1, 1),
        int(mujoco.mjtJoint.mjJNT_HINGE): (1, 1),
    }
    try:
        return spans[int(joint_type)]
    except KeyError as exc:
        raise ContactSceneError(f"unsupported MuJoCo joint type: {joint_type}") from exc


def _object_ids(model: object, *, body_name: str, geom_name: str) -> tuple[int, int, int | None]:
    try:
        import mujoco
    except ImportError as exc:  # pragma: no cover
        raise ContactSceneError("MuJoCo is required for contact scene reset") from exc
    body_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name))
    geom_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, geom_name))
    if body_id < 0 or geom_id < 0:
        raise ContactSceneError(
            "contact scene object is missing from the compiled MuJoCo model"
        )
    if int(model.geom_bodyid[geom_id]) != body_id:
        raise ContactSceneError("contact scene object geom/body identity mismatch")
    object_joint_ids = [
        joint_id
        for joint_id in range(int(model.njnt))
        if int(model.jnt_bodyid[joint_id]) == body_id
    ]
    if len(object_joint_ids) != 1:
        raise ContactSceneError(
            "contact scene object must have exactly one freejoint"
        )
    object_joint_id = object_joint_ids[0]
    try:
        import mujoco
    except ImportError as exc:  # pragma: no cover
        raise ContactSceneError("MuJoCo is required for contact scene reset") from exc
    if int(model.jnt_type[object_joint_id]) != int(mujoco.mjtJoint.mjJNT_FREE):
        raise ContactSceneError("contact scene object joint must be freejoint")
    return body_id, geom_id, object_joint_id


def _robot_addresses(
    model: object,
    *,
    object_body_id: int | None,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    qpos_addresses: list[int] = []
    qvel_addresses: list[int] = []
    for joint_id in range(int(model.njnt)):
        if object_body_id is not None and int(model.jnt_bodyid[joint_id]) == object_body_id:
            continue
        qpos_address = int(model.jnt_qposadr[joint_id])
        qvel_address = int(model.jnt_dofadr[joint_id])
        qpos_span, qvel_span = _joint_spans(model, int(model.jnt_type[joint_id]))
        qpos_addresses.extend(range(qpos_address, qpos_address + qpos_span))
        qvel_addresses.extend(range(qvel_address, qvel_address + qvel_span))
    return tuple(qpos_addresses), tuple(qvel_addresses)


def _assign_vector(
    target: object,
    addresses: Sequence[int],
    values: Sequence[float],
    *,
    name: str,
) -> None:
    if len(addresses) != len(values):
        raise ContactSceneError(
            f"contact reset {name} dimension mismatch: "
            f"expected {len(addresses)}, got {len(values)}"
        )
    for address, value in zip(addresses, values, strict=True):
        target[address] = value


def _contact_pair_has_object(contact: object, object_geom_id: int) -> bool:
    return int(contact.geom1) == object_geom_id or int(contact.geom2) == object_geom_id


@dataclass(frozen=True, slots=True)
class ContactSceneBuildRequest:
    """manifest、Robot-owned base model resource、compatibility期待値の入力。"""

    manifest: ContactTaskManifest
    model_xml: bytes
    assets: Mapping[str, bytes]
    logical_model_path: str | Path
    robot_bundle_identity: VersionedIdentity
    environment_identity: VersionedIdentity
    viewer_scene_identity: str
    robot_capabilities: tuple[VersionedIdentity, ...] | None = None
    robot_roles: tuple[EnvironmentRole, ...] | None = None
    initial_penetration_tolerance_m: float | None = None

    @classmethod
    def from_robot_bundle(
        cls,
        manifest: ContactTaskManifest,
        robot_bundle: RobotBundle,
        *,
        environment_identity: VersionedIdentity | None = None,
        viewer_scene_identity: str | None = None,
    ) -> "ContactSceneBuildRequest":
        """Robot Bundleの宣言resourceからtyped scene requestを組み立てる。"""

        if not isinstance(robot_bundle, RobotBundle):
            raise TypeError("contact scene request requires RobotBundle")
        if not isinstance(manifest, ContactTaskManifest):
            raise TypeError("contact scene request requires ContactTaskManifest")
        _identity_matches(
            manifest.robot_bundle.plugin_id,
            manifest.robot_bundle.contract_version,
            robot_bundle.identity,
            axis="robot bundle",
        )
        resource = robot_bundle.profile.mujoco_model_asset
        if resource is None or not hasattr(resource, "model_xml_and_assets"):
            raise ContactSceneError(
                "contact scene Robot Bundle does not expose a model resource"
            )
        model_xml, assets = resource.model_xml_and_assets()
        resolved_environment = (
            VersionedIdentity(
                manifest.environment.plugin_id,
                manifest.environment.contract_version,
            )
            if environment_identity is None
            else environment_identity
        )
        resolved_viewer = (
            manifest.scene.presentation.visual_feedback_identity
            if viewer_scene_identity is None
            else viewer_scene_identity
        )
        return cls(
            manifest=manifest,
            model_xml=model_xml,
            assets=assets,
            logical_model_path=resource.logical_identifier,
            robot_bundle_identity=robot_bundle.identity,
            environment_identity=resolved_environment,
            viewer_scene_identity=resolved_viewer,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, ContactTaskManifest):
            raise TypeError("contact scene request requires ContactTaskManifest")
        _require_utf8_xml(self.model_xml)
        if not isinstance(self.assets, Mapping):
            raise TypeError("contact scene assets must be a mapping")
        normalized_assets: dict[str, bytes] = {}
        for name, value in self.assets.items():
            if not isinstance(name, str) or not name or name.startswith(("/", "\\")):
                raise ContactSceneError("contact scene asset names must be relative strings")
            if not isinstance(value, bytes):
                raise TypeError("contact scene asset values must be bytes")
            normalized_assets[name] = value
        object.__setattr__(
            self,
            "assets",
            MappingProxyType(dict(sorted(normalized_assets.items()))),
        )
        path = str(self.logical_model_path)
        if not path:
            raise ContactSceneError("contact scene logical model path must not be empty")
        object.__setattr__(self, "logical_model_path", path)
        for name, value in (
            ("robot_bundle_identity", self.robot_bundle_identity),
            ("environment_identity", self.environment_identity),
        ):
            if not isinstance(value, VersionedIdentity):
                raise TypeError(f"{name} must use VersionedIdentity")
        if not isinstance(self.viewer_scene_identity, str) or not self.viewer_scene_identity:
            raise TypeError("viewer_scene_identity must be a non-empty string")
        if self.robot_capabilities is not None:
            object.__setattr__(
                self,
                "robot_capabilities",
                tuple(self.robot_capabilities),
            )
        if self.robot_roles is not None:
            object.__setattr__(self, "robot_roles", tuple(self.robot_roles))
        declared_tolerance = self.manifest.scene.initial_penetration_tolerance_m
        if self.initial_penetration_tolerance_m is None:
            tolerance = declared_tolerance
        else:
            if isinstance(self.initial_penetration_tolerance_m, bool):
                raise TypeError(
                    "contact scene initial penetration tolerance must be numeric"
                )
            tolerance = float(self.initial_penetration_tolerance_m)
            if not isfinite(tolerance) or tolerance < 0.0:
                raise ContactSceneError(
                    "contact scene initial penetration tolerance must be finite and non-negative"
                )
            if tolerance != declared_tolerance:
                raise ContactSceneError(
                    "contact scene initial penetration tolerance must match manifest"
                )
        object.__setattr__(self, "initial_penetration_tolerance_m", tolerance)
        validate_contact_scene_compatibility(
            self.manifest,
            robot_bundle_identity=self.robot_bundle_identity,
            environment_identity=self.environment_identity,
            viewer_scene_identity=self.viewer_scene_identity,
            robot_capabilities=self.robot_capabilities,
            robot_roles=self.robot_roles,
        )


@dataclass(frozen=True, slots=True)
class ContactScene:
    """compiled前の決定的scene variantとmanifest identity。"""

    manifest: ContactTaskManifest
    model_xml: bytes
    assets: Mapping[str, bytes]
    logical_model_path: str
    manifest_digest: str
    object_body_name: str
    object_geom_name: str
    enabled: bool
    initial_penetration_tolerance_m: float

    def build_simulator(self) -> HeadlessMuJoCoSimulator:
        """scene variantをMuJoCoへloadし、宣言resetとreadinessを適用する。"""

        simulator = HeadlessMuJoCoSimulator.from_xml_resources(
            self.model_xml,
            assets=dict(self.assets),
            logical_model_path=self.logical_model_path,
            # ContactResetStateがtask resetの正本であり、Robot keyframeでqpos/qvelを上書きしない。
            initial_keyframe_name=None,
        )
        _configure_mujoco_options(simulator.model, self.manifest)
        self.reset(simulator)
        return simulator

    def load(self) -> "ContactSceneInstance":
        """MuJoCo model/dataを所有する実行時scene instanceを生成する。"""

        simulator = self.build_simulator()
        return ContactSceneInstance(definition=self, simulator=simulator)

    def reset(self, simulator: HeadlessMuJoCoSimulator) -> None:
        """trial boundaryでmodel/dataを完全に初期化し、初期接触を検証する。"""

        if not isinstance(simulator, HeadlessMuJoCoSimulator):
            raise TypeError("contact scene reset requires HeadlessMuJoCoSimulator")
        if Path(simulator.model_path) != Path(self.logical_model_path):
            raise ContactSceneError("contact scene logical model identity mismatch")
        _configure_mujoco_options(simulator.model, self.manifest)
        simulator.reset()
        model = simulator.model
        data = simulator.data
        object_body_id: int | None = None
        object_geom_id: int | None = None
        object_joint_id: int | None = None
        if self.enabled:
            object_body_id, object_geom_id, object_joint_id = _object_ids(
                model,
                body_name=self.object_body_name,
                geom_name=self.object_geom_name,
            )
        qpos_addresses, qvel_addresses = _robot_addresses(
            model,
            object_body_id=object_body_id,
        )
        reset = self.manifest.scene.reset
        _assign_vector(data.qpos, qpos_addresses, reset.qpos_rad, name="qpos_rad")
        _assign_vector(data.qvel, qvel_addresses, reset.qvel_rad_s, name="qvel_rad_s")
        if self.enabled:
            assert object_joint_id is not None
            object_qpos_address = int(model.jnt_qposadr[object_joint_id])
            object_qvel_address = int(model.jnt_dofadr[object_joint_id])
            object_qpos = (*reset.object_position_m, *reset.object_orientation_wxyz)
            for offset, value in enumerate(object_qpos):
                data.qpos[object_qpos_address + offset] = value
            for offset in range(6):
                data.qvel[object_qvel_address + offset] = 0.0
        if len(reset.ctrl) not in (0, int(model.nu)):
            raise ContactSceneError(
                f"contact reset ctrl dimension mismatch: expected {model.nu}, "
                f"got {len(reset.ctrl)}"
            )
        data.ctrl[:] = 0.0
        if reset.ctrl:
            data.ctrl[:] = reset.ctrl
        if len(reset.act) not in (0, int(model.na)):
            raise ContactSceneError(
                f"contact reset act dimension mismatch: expected {model.na}, "
                f"got {len(reset.act)}"
            )
        data.act[:] = 0.0
        if reset.act:
            data.act[:] = reset.act
        if len(reset.warm_start) not in (0, int(model.nv)):
            raise ContactSceneError(
                f"contact reset warm_start dimension mismatch: expected {model.nv}, "
                f"got {len(reset.warm_start)}"
            )
        data.qacc_warmstart[:] = 0.0
        if reset.warm_start:
            data.qacc_warmstart[:] = reset.warm_start
        data.time = reset.simulation_time_s
        import mujoco

        mujoco.mj_forward(model, data)
        self._validate_initial_contact(data, object_geom_id)

    def _validate_initial_contact(self, data: object, object_geom_id: int | None) -> None:
        for contact_index in range(int(data.ncon)):
            contact = data.contact[contact_index]
            distance = float(contact.dist)
            if not isfinite(distance):
                raise ContactSceneError(
                    "contact scene initial contact distance is non-finite"
                )
            if distance <= self.initial_penetration_tolerance_m:
                is_object_contact = (
                    object_geom_id is not None
                    and _contact_pair_has_object(contact, object_geom_id)
                )
                category = "penetration" if distance < 0.0 else "contact"
                subject = "object" if is_object_contact else "scene"
                raise ContactSceneError(
                    "contact scene initial "
                    f"{subject} "
                    f"{category} is not contact-free: distance={distance:.17g}"
                )


@dataclass(slots=True)
class ContactSceneInstance:
    """backend model/dataとimmutable scene definitionを束ねるruntime handle。"""

    definition: ContactScene
    simulator: HeadlessMuJoCoSimulator

    @property
    def manifest(self) -> ContactTaskManifest:
        return self.definition.manifest

    @property
    def model(self) -> object:
        return self.simulator.model

    @property
    def data(self) -> object:
        return self.simulator.data

    def reset(self) -> None:
        """同一scene instanceを宣言されたtrial初期値へ戻す。"""

        self.definition.reset(self.simulator)

    def observe_contact_evidence(
        self,
        *,
        robot_geom_ids: Sequence[int] | None = None,
        robot_geom_names: Sequence[str] | None = None,
        sample_time_s: float | None = None,
        frame_index: int | None = None,
    ) -> object:
        """MuJoCoのraw contact recordからevidenceを抽出する。"""

        from xpotato_sim.runtime.contact.evidence import (
            extract_contact_evidence_from_scene_instance,
        )

        return extract_contact_evidence_from_scene_instance(
            self,
            robot_geom_ids=robot_geom_ids,
            robot_geom_names=robot_geom_names,
            sample_time_s=sample_time_s,
            frame_index=frame_index,
        )

    def measure_contact_evidence(
        self,
        *,
        robot_geom_ids: Sequence[int] | None = None,
        robot_geom_names: Sequence[str] | None = None,
        sample_time_s: float | None = None,
        frame_index: int | None = None,
    ) -> object:
        """observe_contact_evidenceの明示的なmeasurement alias。"""

        return self.observe_contact_evidence(
            robot_geom_ids=robot_geom_ids,
            robot_geom_names=robot_geom_names,
            sample_time_s=sample_time_s,
            frame_index=frame_index,
        )


class ContactSceneComposer:
    """Manifestからbackend-ownedなContactScene variantを構成する。"""

    def __init__(self, request: ContactSceneBuildRequest) -> None:
        if not isinstance(request, ContactSceneBuildRequest):
            raise TypeError("contact scene composer requires ContactSceneBuildRequest")
        self.request = request

    @classmethod
    def from_robot_bundle(
        cls,
        manifest: ContactTaskManifest,
        robot_bundle: RobotBundle,
        *,
        environment_identity: VersionedIdentity | None = None,
        viewer_scene_identity: str | None = None,
    ) -> "ContactSceneComposer":
        """Robot Bundle resourceを使うproduction composition入口。"""

        return cls(
            ContactSceneBuildRequest.from_robot_bundle(
                manifest,
                robot_bundle,
                environment_identity=environment_identity,
                viewer_scene_identity=viewer_scene_identity,
            )
        )

    def compose(self) -> ContactScene:
        """model XMLを構成するが、まだphysics stepは実行しない。"""

        manifest = self.request.manifest
        if (
            manifest.environment.plugin_id != "contact_cube_environment"
            or manifest.environment.contract_version != 1
        ):
            raise ContactSceneError(
                "contact scene composer requires contact_cube_environment/v1"
            )
        model_xml = _compose_model_xml(self.request.model_xml, manifest=manifest)
        object_value = manifest.scene.object
        return ContactScene(
            manifest=manifest,
            model_xml=model_xml,
            assets=self.request.assets,
            logical_model_path=self.request.logical_model_path,
            manifest_digest=contact_manifest_digest(manifest),
            object_body_name=object_value.body_name,
            object_geom_name=object_value.geom_name,
            enabled=manifest.scene.enabled,
            initial_penetration_tolerance_m=manifest.scene.initial_penetration_tolerance_m,
        )

    def build(self) -> ContactSceneInstance:
        """sceneを構成してMuJoCo model/dataをloadする。"""

        return self.compose().load()

    def compose_scene(self) -> ContactScene:
        """EnvironmentSceneProvider相当の明示的なalias。"""

        return self.compose()


def build_contact_scene(request: ContactSceneBuildRequest) -> ContactSceneInstance:
    """typed requestからload済みのcontact scene instanceを構築する。"""

    return ContactSceneComposer(request).build()


__all__ = [
    "CONTACT_SCENE_CONTRACT_VERSION",
    "CONTACT_SCENE_IDENTITY",
    "ContactScene",
    "ContactSceneBuildRequest",
    "ContactSceneComposer",
    "ContactSceneError",
    "ContactSceneInstance",
    "build_contact_scene",
    "validate_contact_scene_compatibility",
]
