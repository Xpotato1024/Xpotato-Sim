"""binding済みcontact-task log sampleのversionedなread-only projection。"""

from __future__ import annotations

import math
from typing import Final

from xpotato_sim.runtime.contact.evidence import ContactEvidenceStatus
from xpotato_sim.runtime.contact.log import (
    CONTACT_CUBE_OBJECT_IDENTITY,
    CONTACT_CUBE_PRESENTATION_IDENTITY,
    CONTACT_CUBE_SCENE_IDENTITY,
    CONTACT_TASK_LOG_PROVENANCE,
    CONTACT_TASK_PRESENTATION_SCHEMA_VERSION,
    ContactTaskLog,
    ContactTaskLogError,
)
from xpotato_sim.runtime.contact.scene import ContactSceneInstance
from xpotato_sim.runtime.contact.virtual_reaction_force import (
    VirtualReactionForceStatus,
)
from xpotato_sim.runtime.composition.robot_profile import (
    RobotProfile,
    robot_profile_runtime_metadata,
)

CONTACT_TASK_PRESENTATION_METADATA_KEY: Final[str] = "contact_task_v1"
CONTACT_SCENE_ROBOT_QPOS_METADATA_KEY: Final[str] = "contact_scene_robot_qpos_v1"
CONTACT_SCENE_ROBOT_QPOS_SCHEMA_VERSION: Final[str] = "contact-scene-robot-qpos/v1"


def build_contact_task_presentation_v1(
    log: ContactTaskLog,
    *,
    payload_time_s: float,
    payload_frame_index: int,
    sample_index: int = -1,
) -> dict[str, object]:
    """physicsを導出せず、task log sampleをpayload-v0 metadataへprojectionする。"""

    if not isinstance(log, ContactTaskLog):
        raise TypeError("contact presentation requires ContactTaskLog")
    if isinstance(sample_index, bool) or not isinstance(sample_index, int):
        raise TypeError("sample_index must be an integer")
    if not isinstance(payload_time_s, (int, float)) or isinstance(payload_time_s, bool):
        raise TypeError("payload_time_s must be a finite number")
    if not isinstance(payload_frame_index, int) or isinstance(payload_frame_index, bool):
        raise TypeError("payload_frame_index must be an integer")
    if not (-len(log.samples) <= sample_index < len(log.samples)):
        raise IndexError("contact presentation sample_index is outside the log")
    if payload_time_s < 0.0 or not float(payload_time_s) < float("inf"):
        raise ContactTaskLogError("payload_time_s must be finite and non-negative")
    if payload_frame_index < 0:
        raise ContactTaskLogError("payload_frame_index must be non-negative")

    resolved_index = sample_index if sample_index >= 0 else len(log.samples) + sample_index
    sample = log.samples[resolved_index]
    header = log.header
    manifest = header.context.manifest
    object_value = manifest.scene.object
    evidence = sample.observation.contact_evidence
    signal = sample.force_signal
    max_age_s = header.force_manifest.config.max_inter_sample_gap_s
    age_s = float(payload_time_s) - evidence.simulation_time_s

    status = "available"
    reason: str | None = None
    if (
        manifest.scene.identity != CONTACT_CUBE_SCENE_IDENTITY
        or object_value.identity != CONTACT_CUBE_OBJECT_IDENTITY
        or manifest.scene.presentation.visual_feedback_identity
        != CONTACT_CUBE_PRESENTATION_IDENTITY
    ):
        status = "unavailable"
        reason = "unsupported contact scene, object, or presentation identity"
    elif age_s < 0.0:
        status = "unavailable"
        reason = "contact sample is newer than the payload state"
    elif age_s > max_age_s or payload_frame_index < (
        evidence.frame_index if evidence.frame_index is not None else payload_frame_index
    ):
        status = "stale"
        reason = "contact sample exceeds the configured payload-age boundary"
    elif signal.status is VirtualReactionForceStatus.STALE:
        status = "stale"
        reason = signal.reason or "derived force signal is stale"
    elif not evidence.is_valid_measurement:
        status = "unavailable"
        reason = evidence.reason or f"raw contact evidence status is {evidence.status.value}"
    elif signal.status not in {
        VirtualReactionForceStatus.ACTIVE,
        VirtualReactionForceStatus.NO_CONTACT,
    }:
        status = "unavailable"
        reason = signal.reason or f"derived force status is {signal.status.value}"
    elif (
        sample.observation.object_position_world_m is None
        or sample.observation.object_orientation_wxyz is None
    ):
        status = "unavailable"
        reason = "backend object pose is unavailable"

    aggregate = evidence.aggregate
    raw_force_world_n = (
        None if aggregate is None else list(aggregate.object_on_tool_force_world_n)
    )
    contacts = [
        {
            "contact_identity": item.contact_identity,
            "normal_world": list(item.normal_world),
            "penetration_m": item.penetration_m,
            "point_world_m": list(item.point_world_m),
        }
        for item in evidence.target_contacts
    ]
    return {
        "schema_version": CONTACT_TASK_PRESENTATION_SCHEMA_VERSION,
        "provenance": CONTACT_TASK_LOG_PROVENANCE,
        "source_kind": header.source_kind.value,
        "evidence_notice": (
            "決定的に生成した合成fixtureです。MuJoCoから取得した接触証拠ではありません。"
            if header.source_kind.value == "synthetic_fixture"
            else None
        ),
        "status": status,
        "reason": reason,
        "binding": header.binding_document,
        "sequence_index": resolved_index,
        "payload_age_s": max(0.0, age_s),
        "max_age_s": max_age_s,
        "sample": {
            "elapsed_time_s": sample.observation.elapsed_time_s,
            "sample_time_s": evidence.sample_time_s,
            "simulation_time_s": evidence.simulation_time_s,
            "frame_index": evidence.frame_index,
        },
        "cube": {
            "identity": {
                "name": object_value.identity.name,
                "version": object_value.identity.version,
            },
            "presentation_identity": manifest.scene.presentation.visual_feedback_identity,
            "shape": object_value.shape,
            "half_size_m": list(object_value.size_m),
            "rgba": list(object_value.material.rgba),
            "position_world_m": (
                None
                if sample.observation.object_position_world_m is None
                else list(sample.observation.object_position_world_m)
            ),
            "orientation_wxyz": (
                None
                if sample.observation.object_orientation_wxyz is None
                else list(sample.observation.object_orientation_wxyz)
            ),
        },
        "contacts": contacts,
        "raw_evidence": {
            "status": evidence.status.value,
            "reason": evidence.reason,
            "contact_count": len(contacts),
            "force_world_n": raw_force_world_n,
        },
        "derived_force": {
            "status": signal.status.value,
            "source_status": (
                None if signal.source_status is None else signal.source_status.value
            ),
            "frame": signal.output_frame.value,
            "force_n": None if signal.force_n is None else list(signal.force_n),
            "raw_force_world_n": (
                None
                if signal.raw_force_world_n is None
                else list(signal.raw_force_world_n)
            ),
            "unit": signal.unit,
            "sign_convention": signal.sign_convention,
            "filtered": signal.filtered,
            "deadbanded": signal.deadbanded,
            "rate_limited": signal.rate_limited,
            "clamped": signal.clamped,
            "reason": signal.reason,
        },
        "task_state": sample.task_state.to_document(),
        "outcome": {
            "phase": log.summary.outcome.phase.value,
            "classification": log.summary.outcome.classification.value,
            "reason": log.summary.outcome.reason,
            "completion_time_s": log.summary.outcome.completion_time_s,
            "observations_count": log.summary.outcome.observations_count,
        },
    }


def contact_task_payload_metadata_v1(
    log: ContactTaskLog,
    *,
    payload_time_s: float,
    payload_frame_index: int,
    sample_index: int = -1,
) -> dict[str, object]:
    """payload-v0のcomposition前にmergeするoptionalなopen metadata extensionを返す。"""

    return {
        CONTACT_TASK_PRESENTATION_METADATA_KEY: build_contact_task_presentation_v1(
            log,
            payload_time_s=payload_time_s,
            payload_frame_index=payload_frame_index,
            sample_index=sample_index,
        )
    }


def contact_scene_robot_qpos_payload_metadata_v1(
    log: ContactTaskLog,
    *,
    instance: ContactSceneInstance,
    robot_profile: RobotProfile,
    payload_time_s: float,
    payload_frame_index: int,
    sample_index: int = -1,
) -> dict[str, object]:
    """scene instanceのdefinitionをlogへ照合してからqpos address metadataを返す。"""

    if not isinstance(log, ContactTaskLog):
        raise TypeError("contact qpos projection requires ContactTaskLog")
    if not isinstance(instance, ContactSceneInstance):
        raise TypeError("contact qpos projection requires ContactSceneInstance")
    if not isinstance(robot_profile, RobotProfile):
        raise TypeError("contact qpos projection requires resolved RobotProfile")
    if isinstance(sample_index, bool) or not isinstance(sample_index, int):
        raise TypeError("sample_index must be an integer")
    if isinstance(payload_time_s, bool) or not isinstance(payload_time_s, (int, float)):
        raise TypeError("payload_time_s must be a finite number")
    if not math.isfinite(float(payload_time_s)) or payload_time_s < 0.0:
        raise ContactTaskLogError("payload_time_s must be finite and non-negative")
    if isinstance(payload_frame_index, bool) or not isinstance(payload_frame_index, int):
        raise TypeError("payload_frame_index must be an integer")
    if payload_frame_index < 0:
        raise ContactTaskLogError("payload_frame_index must be non-negative")
    if not (-len(log.samples) <= sample_index < len(log.samples)):
        raise IndexError("contact qpos projection sample_index is outside the log")

    manifest = log.header.context.manifest
    scene_definition = instance.definition
    scene_manifest = scene_definition.manifest
    if (
        scene_definition.manifest_digest != log.header.context.manifest_digest
        or scene_manifest.scene.identity != manifest.scene.identity
    ):
        raise ContactTaskLogError(
            "ContactSceneInstance definition does not match the contact log"
        )
    selection = manifest.robot_bundle
    if (
        robot_profile.profile_id != selection.plugin_id
        or robot_profile.profile_contract_version != selection.contract_version
    ):
        raise ContactTaskLogError(
            "resolved Robot profile does not match the contact manifest bundle"
        )
    resolved_index = sample_index if sample_index >= 0 else len(log.samples) + sample_index
    evidence = log.samples[resolved_index].observation.contact_evidence
    if (
        evidence.frame_index != payload_frame_index
        or evidence.simulation_time_s != float(payload_time_s)
    ):
        raise ContactTaskLogError(
            "contact sample and MuJoCo payload must share exact frame/time identity"
        )

    profile_metadata = robot_profile_runtime_metadata(robot_profile)
    robot_profile_id = profile_metadata.get("robot_profile_id")
    model_contract_version = profile_metadata.get("model_contract_version")
    robot_joint_names = profile_metadata.get("robot_joint_names")
    robot_qpos_dimension = profile_metadata.get("robot_qpos_dimension")
    if (
        not isinstance(robot_profile_id, str)
        or not isinstance(model_contract_version, str)
        or not isinstance(robot_joint_names, tuple)
        or not all(isinstance(name, str) and name for name in robot_joint_names)
        or isinstance(robot_qpos_dimension, bool)
        or not isinstance(robot_qpos_dimension, int)
        or robot_qpos_dimension < 1
        or len(robot_joint_names) != robot_qpos_dimension
        or len(robot_joint_names) != len(set(robot_joint_names))
    ):
        raise ContactTaskLogError("resolved Robot profile metadata is invalid")

    model = instance.model
    source_qpos_dimension = getattr(model, "nq", None)
    if (
        isinstance(source_qpos_dimension, bool)
        or not isinstance(source_qpos_dimension, int)
        or source_qpos_dimension < robot_qpos_dimension
    ):
        raise ContactTaskLogError("MuJoCo model.nq cannot contain the Robot profile qpos")

    try:
        import mujoco
    except ImportError as exc:  # pragma: no cover - project dependency
        raise ContactTaskLogError("MuJoCo is required to resolve contact scene qpos") from exc

    qpos_addresses: list[int] = []
    one_dof_joint_types = {
        int(mujoco.mjtJoint.mjJNT_HINGE),
        int(mujoco.mjtJoint.mjJNT_SLIDE),
    }
    for joint_name in robot_joint_names:
        joint_id = int(
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        )
        if joint_id < 0:
            raise ContactTaskLogError(
                f"MuJoCo model is missing canonical Robot joint {joint_name!r}"
            )
        if int(model.jnt_type[joint_id]) not in one_dof_joint_types:
            raise ContactTaskLogError(
                f"canonical Robot joint {joint_name!r} is not a scalar qpos joint"
            )
        qpos_addresses.append(int(model.jnt_qposadr[joint_id]))
    if (
        len(set(qpos_addresses)) != robot_qpos_dimension
        or any(address < 0 or address >= source_qpos_dimension for address in qpos_addresses)
    ):
        raise ContactTaskLogError("resolved Robot qpos addresses are invalid")

    scene_identity = manifest.scene.identity
    return {
        CONTACT_SCENE_ROBOT_QPOS_METADATA_KEY: {
            "schema_version": CONTACT_SCENE_ROBOT_QPOS_SCHEMA_VERSION,
            "scene_identity": {
                "name": scene_identity.name,
                "version": scene_identity.version,
            },
            "manifest_digest": log.header.context.manifest_digest,
            "frame_index": payload_frame_index,
            "time_s": float(payload_time_s),
            "source_qpos_dimension": source_qpos_dimension,
            "robot_profile_id": robot_profile_id,
            "model_contract_version": model_contract_version,
            "robot_qpos_dimension": robot_qpos_dimension,
            "robot_joint_names": list(robot_joint_names),
            "qpos_addresses": qpos_addresses,
        }
    }
