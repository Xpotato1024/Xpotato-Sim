"""Run the deterministic software-only contact E2E fixture and write artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for _import_root in (
    REPOSITORY_ROOT / "src",
    REPOSITORY_ROOT
    / "src"
    / "xpotato_sim"
    / "plugins"
    / "robots"
    / "fast_arm"
    / "core"
    / "src",
):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

import mujoco
import numpy as np

from xpotato_sim.mujoco_backend import snapshot_mujoco_state
from xpotato_sim.plugins.robots.fast_arm.adapter.bundle import (
    FAST_ARM_INITIAL_STATE_QPOS_RAD,
    FAST_ARM_ROBOT_BUNDLE,
)
from xpotato_sim.plugins.tasks.contact_press_hold_task import (
    ContactTaskBinding,
    ContactTaskContext,
    ContactTaskObservation,
    ContactTrialIdentity,
    derive_contact_outcome,
)
from xpotato_sim.runtime.contact.evidence import (
    ContactEvidence,
    ContactEvidenceExtractor,
    ContactEvidenceStatus,
)
from xpotato_sim.runtime.contact.log import (
    ContactTaskLog,
    ContactTaskLogHeader,
    ContactTaskLogRecorder,
    ContactTaskLogSourceKind,
    ContactTaskLogTaskState,
    contact_task_log_artifact_name,
    decode_contact_task_log,
    write_contact_task_log,
)
from xpotato_sim.runtime.contact.manifest import (
    ContactCubeObject,
    ContactMaterial,
    ContactResetState,
    ContactSceneContract,
    ContactTarget,
    ContactTaskManifest,
    MuJoCoSettingsIdentity,
    ScenePresentationIdentity,
    contact_manifest_digest,
)
from xpotato_sim.runtime.contact.presentation import (
    CONTACT_SCENE_ROBOT_QPOS_METADATA_KEY,
    CONTACT_TASK_PRESENTATION_METADATA_KEY,
    contact_scene_robot_qpos_payload_metadata_v1,
    contact_task_payload_metadata_v1,
)
from xpotato_sim.runtime.contact.scene import (
    ContactSceneBuildRequest,
    ContactSceneComposer,
    ContactSceneError,
    ContactSceneInstance,
)
from xpotato_sim.runtime.contact.virtual_reaction_force import (
    VirtualReactionForceConfig,
    VirtualReactionForceFrame,
    VirtualReactionForceManifest,
    VirtualReactionForceProcessor,
    VirtualReactionForceSignal,
    VirtualReactionForceStatus,
)
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    SemanticRole,
    SemanticRoleRequirement,
    TaskTerminalClassification,
    VersionedIdentity,
)
from xpotato_sim.runtime.composition.robot_profile import (
    robot_profile_runtime_metadata,
)
from xpotato_sim.runtime.composition.robot_profile_metadata import (
    merge_runtime_metadata,
)
from xpotato_sim.transport import mujoco_state_to_payload

FIXTURE_SCHEMA_VERSION = "contact-e2e-software-fixture/v1"
SUMMARY_SCHEMA_VERSION = "contact-e2e-summary/v1"
SUMMARY_CONTRACT_VERSION = 1
SOFTWARE_REVISION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
GIT_COMMIT_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
TEST_ONLY_SOFTWARE_REVISION_PREFIX = "test-only-"
EXECUTION_METHOD = "cartesian_target_jacobian_ik_quasistatic_mj_forward_no_step/v1"
PROXY_GEOM_NAME = "contact_e2e_tool_proxy"
PROXY_RADIUS_M = 0.01
OBJECT_BODY_NAME = "contact_cube"
OBJECT_GEOM_NAME = "contact_cube_geom"
TRIAL = ContactTrialIdentity("contact-e2e-417-software-v1")
TASK_FORCE_BAND_N = (5.0, 15.0)
DERIVED_FORCE_CLAMP_N = 2.0
SAMPLE_TIMES_S = (0.0, 0.01, 0.02, 0.03, 0.04, 0.061)
TOOL_DISPLACEMENTS_M = (0.0, 0.01, 0.016, 0.016, 0.017, 0.017)
EXPECTED_PHASE_SEQUENCE = (
    "approach",
    "approach",
    "first_contact",
    "press",
    "hold",
    "success",
)
EXPECTED_NEGATIVE_CONTROLS = (
    ("no_contact", "no_contact", "no_contact", "failure"),
    (
        "measurement_unavailable",
        "measurement_unavailable",
        "measurement_unavailable",
        "technical_invalid",
    ),
    ("invalid_contact", "invalid_contact", "invalid", "technical_invalid"),
    ("invalid_scene", "scene_rejected", "not_created", "not_started"),
    ("solver_invalid", "solver_invalid", "invalid", "technical_invalid"),
)


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def _require_exact_keys(
    value: object,
    expected: set[str],
    *,
    name: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{name} must have exactly the declared fields")
    return value


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_software_revision(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or SOFTWARE_REVISION_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("software revision must be a stable identifier")
    if value.startswith(TEST_ONLY_SOFTWARE_REVISION_PREFIX):
        if len(value) == len(TEST_ONLY_SOFTWARE_REVISION_PREFIX):
            raise ValueError("test-only software revision identity must not be empty")
        return value
    if GIT_COMMIT_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "software revision must be a full Git commit SHA or an explicit test-only identity"
        )
    return value


def _validate_capture_software_revision(value: object) -> str:
    """Git HEADとtracked-cleanを確認し、capture用revision identityを返す。"""

    software_revision = _validate_software_revision(value)
    if software_revision.startswith(TEST_ONLY_SOFTWARE_REVISION_PREFIX):
        return software_revision

    source_root = REPOSITORY_ROOT.resolve()
    git_environment = os.environ.copy()
    for variable in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
    ):
        git_environment.pop(variable, None)
    git_environment["GIT_OPTIONAL_LOCKS"] = "0"

    def run_source_git(*arguments: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(source_root), *arguments],
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="strict",
                env=git_environment,
                text=True,
            )
        except OSError as exc:
            raise ValueError("could not verify the source checkout with Git") from exc
        if result.returncode != 0:
            raise ValueError("could not inspect the source checkout with Git")
        return result.stdout.strip()

    head = run_source_git("rev-parse", "--verify", "HEAD^{commit}").lower()
    if GIT_COMMIT_PATTERN.fullmatch(head) is None:
        raise ValueError("source checkout Git HEAD is not a full commit SHA")
    if head != software_revision:
        raise ValueError("software revision does not match source checkout Git HEAD")

    tracked_status = run_source_git(
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
    )
    if tracked_status:
        raise ValueError("source checkout has tracked changes")
    return software_revision


def decode_contact_e2e_summary(data: bytes | bytearray | memoryview) -> dict[str, object]:
    """Strictly decode the versioned deterministic summary document."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("contact E2E summary decoder requires bytes")
    raw = bytes(data)
    if not raw or raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise ValueError("summary must be UTF-8 canonical JSON with one final LF")
    try:
        document = json.loads(
            raw[:-1].decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid contact E2E summary JSON: {exc}") from exc
    if not isinstance(document, dict) or _canonical_json(document) + b"\n" != raw:
        raise ValueError("summary JSON is not canonical")

    root = _require_exact_keys(
        document,
        {
            "contract_version",
            "determinism",
            "execution",
            "fixture",
            "negative_controls",
            "reset",
            "schema_version",
            "valid_run",
        },
        name="summary",
    )
    if (
        root["schema_version"] != SUMMARY_SCHEMA_VERSION
        or root["contract_version"] != SUMMARY_CONTRACT_VERSION
    ):
        raise ValueError("unsupported contact E2E summary version")

    fixture = _require_exact_keys(
        root["fixture"],
        {
            "fixture_version",
            "manifest_software_revision_identity",
            "model_input_sha256",
            "object_geom_name",
            "proxy_geom_name",
            "proxy_radius_m",
            "robot_profile_qpos_dimension",
            "software_revision",
        },
        name="fixture",
    )
    if fixture["fixture_version"] != FIXTURE_SCHEMA_VERSION:
        raise ValueError("unsupported contact E2E fixture version")
    if not isinstance(fixture["model_input_sha256"], str) or re.fullmatch(
        r"[0-9a-f]{64}", fixture["model_input_sha256"]
    ) is None:
        raise ValueError("fixture model input digest is invalid")
    software_revision = _validate_software_revision(fixture["software_revision"])
    expected_revision_identity = (
        f"{software_revision}-contact-e2e-software-fixture-v1"
        f"-model-sha256-{fixture['model_input_sha256']}-proxy-{PROXY_GEOM_NAME}"
    )
    if (
        fixture["object_geom_name"] != OBJECT_GEOM_NAME
        or fixture["proxy_geom_name"] != PROXY_GEOM_NAME
        or fixture["proxy_radius_m"] != PROXY_RADIUS_M
        or type(fixture["robot_profile_qpos_dimension"]) is not int
        or fixture["robot_profile_qpos_dimension"] < 1
        or fixture["manifest_software_revision_identity"] != expected_revision_identity
    ):
        raise ValueError("fixture identity or geometry is unsupported")

    execution = _require_exact_keys(
        root["execution"],
        {
            "hardware_accessed",
            "integration_step_executed",
            "method",
            "network_accessed",
            "software_only",
        },
        name="execution",
    )
    if (
        execution["hardware_accessed"] is not False
        or execution["integration_step_executed"] is not False
        or execution["network_accessed"] is not False
        or execution["software_only"] is not True
        or execution["method"] != EXECUTION_METHOD
    ):
        raise ValueError("execution provenance does not match the software-only contract")

    determinism = _require_exact_keys(
        root["determinism"],
        {"log_bytes_equal", "payload_bytes_equal"},
        name="determinism",
    )
    if determinism != {"log_bytes_equal": True, "payload_bytes_equal": True}:
        raise ValueError("repeated valid runs were not semantically deterministic")

    reset = _require_exact_keys(
        root["reset"],
        {
            "post_reset_evidence_status",
            "same_manifest_digest",
            "same_scene_identity",
        },
        name="reset",
    )
    if (
        reset["post_reset_evidence_status"] != ContactEvidenceStatus.NO_CONTACT.value
        or reset["same_manifest_digest"] is not True
        or reset["same_scene_identity"] is not True
    ):
        raise ValueError("scene reset identity or contact-free state was not preserved")

    controls = root["negative_controls"]
    if not isinstance(controls, list) or len(controls) != len(EXPECTED_NEGATIVE_CONTROLS):
        raise ValueError("summary must contain all five declared negative controls")
    for control, expected in zip(controls, EXPECTED_NEGATIVE_CONTROLS, strict=True):
        item = _require_exact_keys(
            control,
            {"derived_status", "evidence_status", "name", "passed", "task_classification"},
            name="negative control",
        )
        expected_name, expected_evidence, expected_signal, expected_task = expected
        if (
            item["name"] != expected_name
            or item["evidence_status"] != expected_evidence
            or item["derived_status"] != expected_signal
            or item["task_classification"] != expected_task
            or item["passed"] is not True
        ):
            raise ValueError(f"negative control {expected_name} did not fail closed")

    valid = _require_exact_keys(
        root["valid_run"],
        {
            "classification",
            "completion_time_s",
            "contact_manifest_digest",
            "derived_force_below_raw_task_minimum",
            "derived_force_clamp_n",
            "derived_force_peak_magnitude_n",
            "log_artifact",
            "log_sha256",
            "manifest_scene_identity",
            "payload_artifact",
            "payload_frame_index",
            "payload_qpos_sha256",
            "payload_sha256",
            "payload_time_s",
            "phase_sequence",
            "raw_force_band_n",
            "raw_force_range_n",
            "sample_count",
            "signal_manifest_digest",
            "task_uses_raw_evidence_only",
            "trial",
        },
        name="valid run",
    )
    for digest_key in (
        "log_sha256",
        "payload_qpos_sha256",
        "payload_sha256",
    ):
        if not isinstance(valid[digest_key], str) or re.fullmatch(
            r"[0-9a-f]{64}", valid[digest_key]
        ) is None:
            raise ValueError(f"valid-run {digest_key} is invalid")
    for digest_key in ("contact_manifest_digest", "signal_manifest_digest"):
        if not isinstance(valid[digest_key], str) or re.fullmatch(
            r"sha256:[0-9a-f]{64}", valid[digest_key]
        ) is None:
            raise ValueError(f"valid-run {digest_key} is invalid")
    if (
        valid["classification"] != TaskTerminalClassification.SUCCESS.value
        or valid["manifest_scene_identity"] != "contact_cube_scene/v1"
        or valid["task_uses_raw_evidence_only"] is not True
        or valid["derived_force_below_raw_task_minimum"] is not True
        or valid["derived_force_clamp_n"] != DERIVED_FORCE_CLAMP_N
        or valid["derived_force_peak_magnitude_n"] != DERIVED_FORCE_CLAMP_N
        or valid["log_artifact"] != contact_task_log_artifact_name(TRIAL)
        or valid["payload_artifact"] != "contact-e2e-payload-v0.json"
        or type(valid["sample_count"]) is not int
        or valid["sample_count"] != len(EXPECTED_PHASE_SEQUENCE)
        or type(valid["payload_frame_index"]) is not int
        or valid["payload_frame_index"] != valid["sample_count"] - 1
        or not _is_finite_number(valid["completion_time_s"])
        or not _is_finite_number(valid["payload_time_s"])
        or float(valid["completion_time_s"]) != float(valid["payload_time_s"])
        or valid["phase_sequence"] != list(EXPECTED_PHASE_SEQUENCE)
        or not isinstance(valid["trial"], Mapping)
        or valid["trial"].get("trial_id") != TRIAL.trial_id
    ):
        raise ValueError("valid-run identity or Task outcome is inconsistent")
    for key in ("raw_force_band_n", "raw_force_range_n"):
        value = valid[key]
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(_is_finite_number(item) for item in value)
            or float(value[1]) < float(value[0])
        ):
            raise ValueError(f"valid-run {key} must contain an ordered finite pair")
    if (
        float(valid["raw_force_band_n"][0]) != TASK_FORCE_BAND_N[0]
        or float(valid["raw_force_band_n"][1]) != TASK_FORCE_BAND_N[1]
        or float(valid["raw_force_range_n"][0]) < TASK_FORCE_BAND_N[0]
        or float(valid["raw_force_range_n"][1]) > TASK_FORCE_BAND_N[1]
    ):
        raise ValueError("raw Task force evidence is outside its declared band")
    if not all(
        _is_finite_number(valid[key])
        for key in ("derived_force_peak_magnitude_n", "derived_force_clamp_n")
    ):
        raise ValueError("valid-run derived force metrics must be finite")
    return dict(document)


def _model_input_digest(model_xml: bytes, assets: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    digest.update(b"contact-e2e-mujoco-model-input/v1\n")
    digest.update(len(model_xml).to_bytes(8, "big"))
    digest.update(model_xml)
    for name in sorted(assets):
        encoded_name = name.encode("utf-8")
        encoded_data = bytes(assets[name])
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(encoded_data).to_bytes(8, "big"))
        digest.update(encoded_data)
    return digest.hexdigest()


def _initial_tool_position(model_xml: bytes, assets: Mapping[str, bytes]) -> tuple[float, ...]:
    model = mujoco.MjModel.from_xml_string(model_xml.decode("utf-8"), dict(assets))
    data = mujoco.MjData(model)
    initial_qpos = tuple(float(value) for value in FAST_ARM_INITIAL_STATE_QPOS_RAD)
    _require(
        int(model.nq) == len(initial_qpos),
        "fast_arm initial qpos length differs from its declared MuJoCo model",
    )
    data.qpos[:] = initial_qpos
    mujoco.mj_forward(model, data)
    site_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "tip"))
    _require(site_id >= 0, "fast_arm model does not expose the declared tip site")
    return tuple(float(value) for value in data.site_xpos[site_id])


def _add_tool_proxy(assets: Mapping[str, bytes]) -> dict[str, bytes]:
    """Add the explicitly named software proxy to a private copy of arm.xml."""

    _require("arm.xml" in assets, "fast_arm resource bundle is missing arm.xml")
    root = ET.fromstring(bytes(assets["arm.xml"]))
    bodies = tuple(
        element for element in root.iter("body") if element.get("name") == "fore_arm_link"
    )
    _require(len(bodies) == 1, "fast_arm fore_arm_link must be unique in arm.xml")
    body = bodies[0]
    sites = tuple(site for site in body.findall("site") if site.get("name") == "tip")
    _require(len(sites) == 1, "fast_arm tip site must be a direct child of fore_arm_link")
    _require(
        not any(element.get("name") == PROXY_GEOM_NAME for element in root.iter("geom")),
        "contact E2E proxy geom name already exists in the Robot model",
    )
    ET.SubElement(
        body,
        "geom",
        {
            "name": PROXY_GEOM_NAME,
            "type": "sphere",
            "pos": sites[0].get("pos", "0 0 0"),
            "size": repr(PROXY_RADIUS_M),
            "mass": "0.001",
            "contype": "1",
            "conaffinity": "1",
        },
    )
    derived = {name: bytes(value) for name, value in assets.items()}
    derived["arm.xml"] = ET.tostring(root, encoding="utf-8")
    return derived


def _build_manifest(
    *,
    object_position_m: Sequence[float],
    robot_initial_qpos: Sequence[float],
    model_digest: str,
    software_revision: str,
) -> ContactTaskManifest:
    software_revision = _validate_software_revision(software_revision)
    position = tuple(float(value) for value in object_position_m)
    initial_qpos = tuple(float(value) for value in robot_initial_qpos)
    object_value = ContactCubeObject(
        identity=VersionedIdentity("contact_cube", 1),
        position_m=position,
        size_m=(0.03, 0.03, 0.03),
        mass_kg=0.2,
        material=ContactMaterial("contact-red", (0.8, 0.1, 0.1, 1.0)),
        friction=(0.7, 0.01, 0.001),
    )
    scene = ContactSceneContract(
        identity=VersionedIdentity("contact_cube_scene", 1),
        object=object_value,
        reset=ContactResetState(
            qpos_rad=initial_qpos,
            qvel_rad_s=(0.0,) * len(initial_qpos),
            object_position_m=position,
            object_orientation_wxyz=object_value.orientation_wxyz,
        ),
        target=ContactTarget(
            face="+x",
            normal_object=(1.0, 0.0, 0.0),
            approach_direction_world=(-1.0, 0.0, 0.0),
            penetration_band_m=(0.002, 0.01),
        ),
        mujoco=MuJoCoSettingsIdentity(
            timestep_s=0.002,
            integrator="Euler",
            solver="Newton",
            iterations=20,
        ),
        required_capabilities=frozenset(),
        required_robot_roles=frozenset(
            {
                SemanticRoleRequirement(
                    role=SemanticRole("robot.tool_endpoint"),
                    object_kind="robot_endpoint",
                    frame="*",
                    unit="meter",
                )
            }
        ),
        presentation=ScenePresentationIdentity("contact-camera/v1", "contact-cube/v1"),
    )
    return ContactTaskManifest(
        robot_bundle=PluginSelection("fast_arm", 1),
        environment=PluginSelection("contact_cube_environment", 1),
        task=PluginSelection("contact_press_hold_task", 1),
        evaluators=(PluginSelection("contact_outcome", 1),),
        scene=scene,
        software_revision_identity=(
            f"{software_revision}-contact-e2e-software-fixture-v1"
            f"-model-sha256-{model_digest}"
            f"-proxy-{PROXY_GEOM_NAME}"
        ),
    )


def _build_fixture(
    software_revision: str,
) -> tuple[ContactTaskManifest, ContactSceneBuildRequest, str]:
    robot_bundle = FAST_ARM_ROBOT_BUNDLE
    model_xml, source_assets = robot_bundle.profile.mujoco_model_asset.model_xml_and_assets()
    source_assets = {name: bytes(value) for name, value in source_assets.items()}
    initial_qpos = tuple(float(value) for value in FAST_ARM_INITIAL_STATE_QPOS_RAD)
    initial_tip = _initial_tool_position(model_xml, source_assets)
    object_position = tuple(
        float(initial_tip[index] - (0.055 if index == 0 else 0.0))
        for index in range(3)
    )
    derived_assets = _add_tool_proxy(source_assets)
    model_digest = _model_input_digest(model_xml, derived_assets)
    manifest = _build_manifest(
        object_position_m=object_position,
        robot_initial_qpos=initial_qpos,
        model_digest=model_digest,
        software_revision=software_revision,
    )
    request = ContactSceneBuildRequest.from_robot_bundle(manifest, robot_bundle)
    _require(request.model_xml == model_xml, "Robot Bundle model XML changed during composition")
    _require(
        dict(request.assets) == source_assets,
        "Robot Bundle assets changed during composition",
    )
    return manifest, replace(request, assets=derived_assets), model_digest


def _task_context(manifest: ContactTaskManifest) -> ContactTaskContext:
    return ContactTaskContext(
        manifest=manifest,
        dwell_interval_s=0.02,
        timeout_s=0.1,
        target_normal_force_band_n=TASK_FORCE_BAND_N,
        require_pose_measurement=True,
        trial=TRIAL,
    )


def _force_manifest(manifest: ContactTaskManifest) -> VirtualReactionForceManifest:
    return VirtualReactionForceManifest(
        contact_manifest=manifest,
        config=VirtualReactionForceConfig(
            output_frame=VirtualReactionForceFrame.MUJOCO_WORLD,
            deadband_n=0.0,
            low_pass_time_constant_s=0.0,
            smoothing_window_samples=1,
            rate_limit_n_per_s=None,
            magnitude_clamp_n=DERIVED_FORCE_CLAMP_N,
            max_inter_sample_gap_s=0.025,
        ),
    )


@dataclass(frozen=True, slots=True)
class _RunResult:
    log: ContactTaskLog
    log_bytes: bytes
    payload: dict[str, object]
    payload_bytes: bytes
    model_digest: str
    reset_evidence_status: str
    reset_manifest_digest: str
    initial_manifest_digest: str
    same_scene_identity: bool
    robot_profile_qpos_dimension: int
    software_revision: str
    manifest_software_revision_identity: str


def _robot_joint_addresses(model: object) -> tuple[tuple[int, ...], tuple[int, ...]]:
    joint_names = FAST_ARM_ROBOT_BUNDLE.profile.canonical_joint_names
    _require(
        len(joint_names) == len(FAST_ARM_INITIAL_STATE_QPOS_RAD),
        "fast_arm canonical joint count differs from its initial qpos declaration",
    )
    joint_ids = tuple(
        int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name))
        for name in joint_names
    )
    _require(all(value >= 0 for value in joint_ids), "fast_arm joint lookup failed")
    qpos_addresses = tuple(int(model.jnt_qposadr[joint_id]) for joint_id in joint_ids)
    dof_addresses = tuple(int(model.jnt_dofadr[joint_id]) for joint_id in joint_ids)
    return qpos_addresses, dof_addresses


def _set_tool_target(
    instance: ContactSceneInstance,
    *,
    initial_qpos: np.ndarray,
    qpos_addresses: Sequence[int],
    dof_addresses: Sequence[int],
    site_id: int,
    target_position: np.ndarray,
    time_s: float,
) -> None:
    model = instance.model
    data = instance.data
    data.qpos[:] = initial_qpos
    data.qvel[:] = 0.0
    data.time = float(time_s)
    jacobian = np.zeros((3, int(model.nv)), dtype=np.float64)
    for _ in range(12):
        mujoco.mj_forward(model, data)
        delta = target_position - np.asarray(data.site_xpos[site_id], dtype=np.float64)
        if float(np.linalg.norm(delta)) <= 1e-10:
            break
        mujoco.mj_jacSite(model, data, jacobian, None, site_id)
        reduced = jacobian[:, tuple(dof_addresses)]
        update = np.linalg.pinv(reduced) @ delta
        qpos_indices = np.asarray(qpos_addresses, dtype=np.intp)
        data.qpos[qpos_indices] = (
            np.asarray(data.qpos[qpos_indices], dtype=np.float64) + update
        )
    mujoco.mj_forward(model, data)
    residual = float(
        np.linalg.norm(target_position - np.asarray(data.site_xpos[site_id], dtype=np.float64))
    )
    _require(residual <= 1e-6, f"prescribed tool target residual is too large: {residual}")
    data.time = float(time_s)


def _pose_observation(
    instance: ContactSceneInstance,
    evidence: ContactEvidence,
    *,
    elapsed_time_s: float,
    site_id: int,
    object_body_id: int,
) -> ContactTaskObservation:
    target_contacts = evidence.target_contacts
    return ContactTaskObservation(
        elapsed_time_s=elapsed_time_s,
        contact_evidence=evidence,
        tip_position_world_m=tuple(float(value) for value in instance.data.site_xpos[site_id]),
        object_position_world_m=tuple(
            float(value) for value in instance.data.xpos[object_body_id]
        ),
        object_orientation_wxyz=tuple(
            float(value) for value in instance.data.xquat[object_body_id]
        ),
        contact_location_world_m=(
            None if not target_contacts else target_contacts[0].point_world_m
        ),
    )


def _run_valid_capture(
    manifest: ContactTaskManifest,
    request: ContactSceneBuildRequest,
    model_digest: str,
    software_revision: str,
) -> _RunResult:
    instance = ContactSceneComposer(request).build()
    initial_manifest_digest = instance.definition.manifest_digest
    trial = TRIAL
    context = _task_context(manifest)
    force_manifest = _force_manifest(manifest)
    recorder = ContactTaskLogRecorder(
        ContactTaskLogHeader(
            context,
            force_manifest,
            source_kind=ContactTaskLogSourceKind.RUNTIME_CAPTURE,
        )
    )
    processor = VirtualReactionForceProcessor(force_manifest)
    binding = ContactTaskBinding(context)
    task_state = binding.initial_state()
    observations: list[ContactTaskObservation] = []
    phase_sequence: list[str] = []
    tip_id = int(mujoco.mj_name2id(instance.model, mujoco.mjtObj.mjOBJ_SITE, "tip"))
    object_body_id = int(
        mujoco.mj_name2id(instance.model, mujoco.mjtObj.mjOBJ_BODY, OBJECT_BODY_NAME)
    )
    _require(tip_id >= 0 and object_body_id >= 0, "contact fixture model identity lookup failed")
    qpos_addresses, dof_addresses = _robot_joint_addresses(instance.model)
    home_qpos = np.asarray(instance.data.qpos, dtype=np.float64).copy()
    home_tip = np.asarray(instance.data.site_xpos[tip_id], dtype=np.float64).copy()

    for frame_index, (elapsed_time_s, displacement_m) in enumerate(
        zip(SAMPLE_TIMES_S, TOOL_DISPLACEMENTS_M, strict=True)
    ):
        target_position = home_tip - np.asarray((displacement_m, 0.0, 0.0))
        _set_tool_target(
            instance,
            initial_qpos=home_qpos,
            qpos_addresses=qpos_addresses,
            dof_addresses=dof_addresses,
            site_id=tip_id,
            target_position=target_position,
            time_s=elapsed_time_s,
        )
        evidence = instance.measure_contact_evidence(
            robot_geom_names=(PROXY_GEOM_NAME,),
            sample_time_s=elapsed_time_s,
            frame_index=frame_index,
        )
        _require(isinstance(evidence, ContactEvidence), "scene returned untyped contact evidence")
        observation = _pose_observation(
            instance,
            evidence,
            elapsed_time_s=elapsed_time_s,
            site_id=tip_id,
            object_body_id=object_body_id,
        )
        transition = binding.advance(task_state, observation)
        task_state = transition.state
        phase_sequence.append(task_state.phase.value)
        observations.append(observation)
        signal = processor.process(evidence, trial=trial)
        recorder.append(
            observation,
            signal,
            ContactTaskLogTaskState(
                phase=task_state.phase,
                classification=task_state.classification,
                reason=task_state.terminal_reason,
            ),
        )

    outcome = derive_contact_outcome(context, observations)
    if (
        outcome.classification is not TaskTerminalClassification.SUCCESS
        or task_state.classification is not TaskTerminalClassification.SUCCESS
    ):
        evidence_trace = tuple(
            (
                item.elapsed_time_s,
                item.contact_evidence.status.value,
                item.contact_evidence.simulation_time_s,
                None
                if item.contact_evidence.aggregate is None
                else item.contact_evidence.aggregate.normal_force_n,
                tuple(record.penetration_m for record in item.contact_evidence.target_contacts),
            )
            for item in observations
        )
        raise ValueError(
            "raw-evidence Task did not succeed: "
            f"classification={outcome.classification.value}, "
            f"phase={task_state.phase.value}, reason={task_state.terminal_reason!r}, "
            f"phases={phase_sequence!r}, evidence={evidence_trace!r}"
        )
    _require(
        tuple(phase_sequence) == EXPECTED_PHASE_SEQUENCE,
        "Task phase sequence differs from the finite acceptance trajectory",
    )
    log = recorder.finalize(outcome)
    log_bytes = log.to_jsonl()
    decoded_log = decode_contact_task_log(log_bytes)
    _require(decoded_log == log, "contact-task-log/v1 strict round-trip changed the log")

    last_sample = log.samples[-1]
    final_evidence = last_sample.observation.contact_evidence
    _require(
        final_evidence.frame_index == len(log.samples) - 1,
        "final contact evidence frame index is not the last prescribed frame",
    )
    payload_time_s = float(instance.data.time)
    payload_frame_index = int(final_evidence.frame_index)
    robot_profile_metadata = robot_profile_runtime_metadata(
        FAST_ARM_ROBOT_BUNDLE.profile
    )
    metadata = merge_runtime_metadata(
        contact_task_payload_metadata_v1(
            log,
            payload_time_s=payload_time_s,
            payload_frame_index=payload_frame_index,
        ),
        contact_scene_robot_qpos_payload_metadata_v1(
            log,
            instance=instance,
            robot_profile=FAST_ARM_ROBOT_BUNDLE.profile,
            payload_time_s=payload_time_s,
            payload_frame_index=payload_frame_index,
        ),
        authoritative_profile_metadata=robot_profile_metadata,
    )
    snapshot = snapshot_mujoco_state(
        instance.model,
        instance.data,
        frame_index=payload_frame_index,
        metadata=metadata,
    )
    payload = mujoco_state_to_payload(snapshot)
    payload_bytes = _canonical_json(payload) + b"\n"
    _require(
        payload["frame_index"] == final_evidence.frame_index
        and payload["time_s"] == final_evidence.simulation_time_s,
        "viewer payload and final raw contact sample do not share frame/time identity",
    )
    presentation = payload["metadata"][CONTACT_TASK_PRESENTATION_METADATA_KEY]  # type: ignore[index]
    _require(
        presentation["status"] == "available"  # type: ignore[index]
        and presentation["binding"]["manifest_digest"] == context.manifest_digest,  # type: ignore[index]
        "same-state viewer presentation failed its contact manifest binding",
    )
    qpos_projection = payload["metadata"][CONTACT_SCENE_ROBOT_QPOS_METADATA_KEY]  # type: ignore[index]
    _require(
        qpos_projection["frame_index"] == payload_frame_index  # type: ignore[index]
        and qpos_projection["time_s"] == payload_time_s  # type: ignore[index]
        and qpos_projection["source_qpos_dimension"] == instance.model.nq  # type: ignore[index]
        and qpos_projection["robot_qpos_dimension"]  # type: ignore[index]
        == FAST_ARM_ROBOT_BUNDLE.profile.qpos_dimension,
        "viewer qpos projection does not match the same MuJoCo scene snapshot",
    )

    instance.reset()
    reset_evidence = instance.measure_contact_evidence(
        robot_geom_names=(PROXY_GEOM_NAME,),
        sample_time_s=0.0,
        frame_index=0,
    )
    _require(
        reset_evidence.status is ContactEvidenceStatus.NO_CONTACT,
        "scene reset did not restore its declared contact-free initial state",
    )
    same_scene_identity = (
        instance.definition.manifest_digest == initial_manifest_digest
        and reset_evidence.manifest_digest == initial_manifest_digest
        and reset_evidence.scene_identity == manifest.scene.identity
    )
    _require(
        same_scene_identity,
        "scene reset changed the bound scene identity",
    )
    _require(
        FAST_ARM_ROBOT_BUNDLE.profile.qpos_dimension
        == len(FAST_ARM_INITIAL_STATE_QPOS_RAD),
        "software fixture changed the Robot profile qpos dimension",
    )
    return _RunResult(
        log=log,
        log_bytes=log_bytes,
        payload=payload,
        payload_bytes=payload_bytes,
        model_digest=model_digest,
        reset_evidence_status=reset_evidence.status.value,
        reset_manifest_digest=reset_evidence.manifest_digest,
        initial_manifest_digest=initial_manifest_digest,
        same_scene_identity=same_scene_identity,
        robot_profile_qpos_dimension=FAST_ARM_ROBOT_BUNDLE.profile.qpos_dimension,
        software_revision=software_revision,
        manifest_software_revision_identity=manifest.software_revision_identity,
    )


def _extractor_for_instance(
    instance: ContactSceneInstance,
    manifest: ContactTaskManifest,
    *,
    model: object,
    data: object,
    object_body_name: str,
    sample_time_s: float = 0.0,
    frame_index: int = 0,
) -> ContactEvidenceExtractor:
    return ContactEvidenceExtractor(
        model=model,
        data=data,
        scene_identity=manifest.scene.identity,
        object_identity=manifest.scene.object.identity,
        manifest_digest=contact_manifest_digest(manifest),
        object_body_name=object_body_name,
        object_geom_name=instance.definition.object_geom_name,
        robot_geom_names=(PROXY_GEOM_NAME,),
        sample_time_s=sample_time_s,
        frame_index=frame_index,
        manifest=manifest,
    )


def _negative_control_for_evidence(
    *,
    name: str,
    evidence: ContactEvidence,
    instance: ContactSceneInstance,
    manifest: ContactTaskManifest,
    context: ContactTaskContext,
    force_manifest: VirtualReactionForceManifest,
    site_id: int,
    object_body_id: int,
    finalize_no_contact: bool = False,
) -> dict[str, object]:
    observation = _pose_observation(
        instance,
        evidence,
        elapsed_time_s=0.0,
        site_id=site_id,
        object_body_id=object_body_id,
    )
    binding = ContactTaskBinding(context)
    transition = binding.advance(binding.initial_state(), observation)
    task_state = transition.state
    if finalize_no_contact:
        _require(
            evidence.status is ContactEvidenceStatus.NO_CONTACT,
            "no-contact control must use a measured no-contact snapshot",
        )
        task_state = binding.finalize(
            task_state,
            reason="finite software control ended without target contact",
        ).state
    signal = VirtualReactionForceProcessor(force_manifest).process(
        evidence,
        trial=TRIAL,
    )
    return {
        "derived_status": signal.status.value,
        "evidence_status": evidence.status.value,
        "name": name,
        "passed": False,
        "task_classification": task_state.classification.value,
    }


def _run_negative_controls(
    manifest: ContactTaskManifest,
    request: ContactSceneBuildRequest,
) -> list[dict[str, object]]:
    context = _task_context(manifest)
    force_manifest = _force_manifest(manifest)
    instance = ContactSceneComposer(request).build()
    site_id = int(mujoco.mj_name2id(instance.model, mujoco.mjtObj.mjOBJ_SITE, "tip"))
    object_body_id = int(
        mujoco.mj_name2id(instance.model, mujoco.mjtObj.mjOBJ_BODY, OBJECT_BODY_NAME)
    )
    qpos_addresses, dof_addresses = _robot_joint_addresses(instance.model)
    home_qpos = np.asarray(instance.data.qpos, dtype=np.float64).copy()
    home_tip = np.asarray(instance.data.site_xpos[site_id], dtype=np.float64).copy()
    controls: list[dict[str, object]] = []

    instance.reset()
    no_contact = instance.measure_contact_evidence(
        robot_geom_names=(PROXY_GEOM_NAME,),
        sample_time_s=0.0,
        frame_index=0,
    )
    controls.append(
        _negative_control_for_evidence(
            name="no_contact",
            evidence=no_contact,
            instance=instance,
            manifest=manifest,
            context=context,
            force_manifest=force_manifest,
            site_id=site_id,
            object_body_id=object_body_id,
            finalize_no_contact=True,
        )
    )

    instance.reset()
    unavailable = _extractor_for_instance(
        instance,
        manifest,
        model=None,
        data=None,
        object_body_name=instance.definition.object_body_name,
    ).extract()
    controls.append(
        _negative_control_for_evidence(
            name="measurement_unavailable",
            evidence=unavailable,
            instance=instance,
            manifest=manifest,
            context=context,
            force_manifest=force_manifest,
            site_id=site_id,
            object_body_id=object_body_id,
        )
    )

    instance.reset()
    _set_tool_target(
        instance,
        initial_qpos=home_qpos,
        qpos_addresses=qpos_addresses,
        dof_addresses=dof_addresses,
        site_id=site_id,
        target_position=home_tip - np.asarray((0.016, 0.0, 0.0)),
        time_s=0.0,
    )
    invalid_contact = _extractor_for_instance(
        instance,
        manifest,
        model=instance.model,
        data=instance.data,
        object_body_name="contact_e2e_missing_object_body",
    ).extract()
    controls.append(
        _negative_control_for_evidence(
            name="invalid_contact",
            evidence=invalid_contact,
            instance=instance,
            manifest=manifest,
            context=context,
            force_manifest=force_manifest,
            site_id=site_id,
            object_body_id=object_body_id,
        )
    )

    invalid_position = tuple(
        float(home_tip[index] - (0.03 if index == 0 else 0.0))
        for index in range(3)
    )
    invalid_object = replace(manifest.scene.object, position_m=invalid_position)
    invalid_reset = replace(
        manifest.scene.reset,
        object_position_m=invalid_position,
        object_orientation_wxyz=invalid_object.orientation_wxyz,
    )
    invalid_scene = replace(
        manifest.scene,
        object=invalid_object,
        reset=invalid_reset,
    )
    invalid_manifest = replace(manifest, scene=invalid_scene)
    invalid_request = replace(request, manifest=invalid_manifest)
    scene_rejected = False
    try:
        ContactSceneComposer(invalid_request).build()
    except ContactSceneError:
        scene_rejected = True
    _require(scene_rejected, "overlapping initial scene was not rejected")
    controls.append(
        {
            "derived_status": "not_created",
            "evidence_status": "scene_rejected",
            "name": "invalid_scene",
            "passed": False,
            "task_classification": "not_started",
        }
    )

    instance.reset()
    _set_tool_target(
        instance,
        initial_qpos=home_qpos,
        qpos_addresses=qpos_addresses,
        dof_addresses=dof_addresses,
        site_id=site_id,
        target_position=home_tip - np.asarray((0.016, 0.0, 0.0)),
        time_s=0.0,
    )
    with patch.object(
        mujoco,
        "mj_contactForce",
        side_effect=RuntimeError("injected deterministic solver failure"),
    ):
        solver_invalid = instance.measure_contact_evidence(
            robot_geom_names=(PROXY_GEOM_NAME,),
            sample_time_s=0.0,
            frame_index=0,
        )
    controls.append(
        _negative_control_for_evidence(
            name="solver_invalid",
            evidence=solver_invalid,
            instance=instance,
            manifest=manifest,
            context=context,
            force_manifest=force_manifest,
            site_id=site_id,
            object_body_id=object_body_id,
        )
    )

    expected = {
        name: (evidence_status, signal_status, task_status)
        for name, evidence_status, signal_status, task_status in EXPECTED_NEGATIVE_CONTROLS
    }
    for control in controls:
        control["passed"] = (
            expected[control["name"]]
            == (
                control["evidence_status"],
                control["derived_status"],
                control["task_classification"],
            )
        )
    _require(all(control["passed"] is True for control in controls), "a negative control did not fail closed")
    return controls


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload_qpos_digest(payload: Mapping[str, object]) -> str:
    qpos = payload.get("qpos")
    _require(isinstance(qpos, list), "viewer payload does not contain qpos")
    return _sha256(_canonical_json(qpos))


def _build_summary(
    first: _RunResult,
    second: _RunResult,
    negative_controls: list[dict[str, object]],
    *,
    log_name: str,
) -> dict[str, object]:
    _require(first.log_bytes == second.log_bytes, "repeated contact logs are not byte-identical")
    _require(
        first.payload_bytes == second.payload_bytes,
        "repeated viewer payloads are not byte-identical",
    )
    log = first.log
    active_samples = tuple(
        sample for sample in log.samples if sample.observation.contact_evidence.target_contacts
    )
    raw_forces = tuple(
        float(sample.observation.contact_evidence.aggregate.normal_force_n)  # type: ignore[union-attr]
        for sample in active_samples
    )
    derived_magnitudes = tuple(
        math.sqrt(math.fsum(value * value for value in sample.force_signal.force_n or ()))
        for sample in active_samples
    )
    _require(bool(raw_forces), "valid run produced no target force evidence")
    _require(
        all(TASK_FORCE_BAND_N[0] <= force <= TASK_FORCE_BAND_N[1] for force in raw_forces),
        "raw Task force evidence left its declared band",
    )
    _require(
        all(
            sample.force_signal.status is VirtualReactionForceStatus.ACTIVE
            and sample.force_signal.clamped
            for sample in active_samples
        ),
        "measured force signal was not explicitly clamped",
    )
    _require(
        all(math.isclose(value, DERIVED_FORCE_CLAMP_N, rel_tol=0.0, abs_tol=1e-9) for value in derived_magnitudes),
        "derived force magnitude differs from the configured clamp",
    )
    final_sample = log.samples[-1]
    final_evidence = final_sample.observation.contact_evidence
    payload = first.payload
    payload_time_s = float(payload["time_s"])
    payload_frame_index = int(payload["frame_index"])
    _require(
        final_evidence.simulation_time_s == payload_time_s
        and final_evidence.frame_index == payload_frame_index,
        "summary cannot bind different log and viewer payload states",
    )
    return {
        "contract_version": SUMMARY_CONTRACT_VERSION,
        "determinism": {
            "log_bytes_equal": True,
            "payload_bytes_equal": True,
        },
        "execution": {
            "hardware_accessed": False,
            "integration_step_executed": False,
            "method": EXECUTION_METHOD,
            "network_accessed": False,
            "software_only": True,
        },
        "fixture": {
            "fixture_version": FIXTURE_SCHEMA_VERSION,
            "manifest_software_revision_identity": (
                first.manifest_software_revision_identity
            ),
            "model_input_sha256": first.model_digest,
            "object_geom_name": OBJECT_GEOM_NAME,
            "proxy_geom_name": PROXY_GEOM_NAME,
            "proxy_radius_m": PROXY_RADIUS_M,
            "robot_profile_qpos_dimension": first.robot_profile_qpos_dimension,
            "software_revision": first.software_revision,
        },
        "negative_controls": negative_controls,
        "reset": {
            "post_reset_evidence_status": first.reset_evidence_status,
            "same_manifest_digest": (
                first.reset_manifest_digest == first.initial_manifest_digest
            ),
            "same_scene_identity": first.same_scene_identity,
        },
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "valid_run": {
            "classification": log.summary.outcome.classification.value,
            "completion_time_s": log.summary.outcome.completion_time_s,
            "contact_manifest_digest": log.header.context.manifest_digest,
            "derived_force_below_raw_task_minimum": (
                max(derived_magnitudes) < TASK_FORCE_BAND_N[0]
            ),
            "derived_force_clamp_n": DERIVED_FORCE_CLAMP_N,
            "derived_force_peak_magnitude_n": max(derived_magnitudes),
            "log_artifact": log_name,
            "log_sha256": _sha256(first.log_bytes),
            "manifest_scene_identity": (
                f"{log.header.context.manifest.scene.identity.name}/"
                f"v{log.header.context.manifest.scene.identity.version}"
            ),
            "payload_artifact": "contact-e2e-payload-v0.json",
            "payload_frame_index": payload_frame_index,
            "payload_qpos_sha256": _payload_qpos_digest(payload),
            "payload_sha256": _sha256(first.payload_bytes),
            "payload_time_s": payload_time_s,
            "phase_sequence": [
                sample.task_state.phase.value for sample in log.samples
            ],
            "raw_force_band_n": list(TASK_FORCE_BAND_N),
            "raw_force_range_n": [min(raw_forces), max(raw_forces)],
            "sample_count": len(log.samples),
            "signal_manifest_digest": log.header.force_manifest.digest,
            "task_uses_raw_evidence_only": True,
            "trial": log.header.context.trial.to_document(),
        },
    }


def _write_atomic_exclusive(path: Path, data: bytes) -> Path:
    if path.exists():
        raise FileExistsError(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    created = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        created = True
        temporary.unlink()
        if path.read_bytes() != data:
            raise OSError(f"atomic artifact read-back mismatch: {path.name}")
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def run_contact_e2e(
    output_dir: Path,
    *,
    software_revision: str,
) -> tuple[Path, Path, Path]:
    """検証済みのsource revisionから有限なsoftware-only artifactを生成する。"""

    if not isinstance(output_dir, Path) or not output_dir.is_absolute():
        raise ValueError("output directory must be an explicit absolute path")
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output directory must already exist: {output_dir}")
    if any(output_dir.iterdir()):
        raise FileExistsError(f"output directory must be empty: {output_dir}")

    software_revision = _validate_capture_software_revision(software_revision)
    manifest, request, model_digest = _build_fixture(software_revision)
    first = _run_valid_capture(manifest, request, model_digest, software_revision)
    second = _run_valid_capture(manifest, request, model_digest, software_revision)
    controls = _run_negative_controls(manifest, request)
    log_name = contact_task_log_artifact_name(first.log.header.context.trial)
    summary = _build_summary(first, second, controls, log_name=log_name)
    summary_bytes = _canonical_json(summary) + b"\n"
    _require(
        decode_contact_e2e_summary(summary_bytes) == summary,
        "contact E2E summary failed strict encode/decode round-trip",
    )

    output_dir = output_dir.resolve()
    log_path = output_dir / log_name
    payload_path = output_dir / "contact-e2e-payload-v0.json"
    summary_path = output_dir / "contact-e2e-summary-v1.json"
    written: list[Path] = []
    try:
        write_contact_task_log(log_path, first.log)
        written.append(log_path)
        stored_log = log_path.read_bytes()
        _require(stored_log == first.log_bytes, "contact log writer changed canonical bytes")
        _require(
            decode_contact_task_log(stored_log) == first.log,
            "stored contact log failed strict read-back",
        )

        _write_atomic_exclusive(payload_path, first.payload_bytes)
        written.append(payload_path)
        _write_atomic_exclusive(summary_path, summary_bytes)
        written.append(summary_path)
        _require(
            decode_contact_e2e_summary(summary_path.read_bytes()) == summary,
            "stored summary failed strict read-back",
        )
        payload_document = json.loads(
            payload_path.read_bytes()[:-1].decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
        _require(
            _canonical_json(payload_document) + b"\n" == payload_path.read_bytes(),
            "stored viewer payload failed canonical JSON read-back",
        )
    except Exception:
        for path in reversed(written):
            path.unlink(missing_ok=True)
        raise
    return log_path, payload_path, summary_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="既存の空の絶対directory。生成物はここだけへ保存する。",
    )
    parser.add_argument(
        "--software-revision",
        required=True,
        help="現在のclean source checkoutのfull commit SHA、または明示したtest-only identity。",
    )
    arguments = parser.parse_args(argv)
    log_path, payload_path, summary_path = run_contact_e2e(
        arguments.output_dir,
        software_revision=arguments.software_revision,
    )
    print(f"contact log: {log_path}")
    print(f"viewer payload: {payload_path}")
    print(f"contact E2E summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
