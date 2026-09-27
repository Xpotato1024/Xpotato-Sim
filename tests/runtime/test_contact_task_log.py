from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from pathlib import Path

import pytest

import xpotato_sim.runtime.contact.log as contact_task_log_module
from xpotato_sim.plugins.tasks.contact_press_hold_task.implementation import (
    ContactTaskBinding,
    derive_contact_outcome,
)
from xpotato_sim.runtime.contact import (
    ContactCubeObject,
    ContactEvidence,
    ContactEvidenceStatus,
    ContactForceAggregate,
    ContactMaterial,
    ContactOperatorStatus,
    ContactPairClassification,
    ContactRecord,
    ContactResetState,
    ContactSceneContract,
    ContactTarget,
    ContactTaskContext,
    ContactTaskLog,
    ContactTaskLogError,
    ContactTaskLogHeader,
    ContactTaskLogRecorder,
    ContactTaskLogSourceKind,
    ContactTaskLogTaskState,
    ContactTaskManifest,
    ContactTaskObservation,
    ContactTaskPhase,
    ContactTrialIdentity,
    MuJoCoSettingsIdentity,
    ScenePresentationIdentity,
    VirtualReactionForceConfig,
    VirtualReactionForceFrame,
    VirtualReactionForceManifest,
    VirtualReactionForceProcessor,
    VirtualReactionForceStatus,
    build_contact_task_presentation_v1,
    contact_manifest_digest,
    contact_task_log_artifact_name,
    decode_contact_task_log,
    read_contact_task_log,
    write_contact_task_log,
)
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    ROLE_ATTRIBUTE_WILDCARD,
    SemanticRole,
    SemanticRoleRequirement,
    TaskTerminalClassification,
    VersionedIdentity,
)


def _manifest() -> ContactTaskManifest:
    object_value = ContactCubeObject(
        identity=VersionedIdentity("contact_cube", 1),
        position_m=(0.15, 0.0, 0.07),
        size_m=(0.04, 0.04, 0.04),
        mass_kg=0.2,
        material=ContactMaterial("red-cube/v1", (0.8, 0.1, 0.1, 1.0)),
        friction=(0.7, 0.01, 0.001),
    )
    scene = ContactSceneContract(
        identity=VersionedIdentity("contact_cube_scene", 1),
        object=object_value,
        reset=ContactResetState(
            qpos_rad=(0.0, 0.0, 0.0, 0.0),
            qvel_rad_s=(0.0, 0.0, 0.0, 0.0),
            object_position_m=object_value.position_m,
            object_orientation_wxyz=object_value.orientation_wxyz,
        ),
        target=ContactTarget(
            face="+x",
            normal_object=(1.0, 0.0, 0.0),
            approach_direction_world=(-1.0, 0.0, 0.0),
            penetration_band_m=(0.0, 0.002),
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
                    frame=ROLE_ATTRIBUTE_WILDCARD,
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
        software_revision_identity="fixture-revision:contact-task-log",
    )


def _evidence(
    manifest: ContactTaskManifest,
    status: ContactEvidenceStatus,
    *,
    time_s: float,
    frame_index: int,
) -> ContactEvidence:
    common = {
        "scene_identity": manifest.scene.identity,
        "object_identity": manifest.scene.object.identity,
        "manifest_digest": contact_manifest_digest(manifest),
        "sample_time_s": time_s,
        "simulation_time_s": time_s,
        "frame_index": frame_index,
    }
    if status is ContactEvidenceStatus.NO_CONTACT:
        return ContactEvidence(
            status=status,
            contacts=(),
            aggregate=ContactForceAggregate.no_contact(),
            **common,
        )
    if status is not ContactEvidenceStatus.MEASURED:
        return ContactEvidence(
            status=status,
            contacts=(),
            aggregate=None,
            reason="synthetic fixture evidence unavailable",
            **common,
        )
    force = (2.0, 0.0, 0.0)
    record = ContactRecord(
        contact_identity=f"target-contact-{frame_index}",
        classification=ContactPairClassification.TARGET_OBJECT,
        geom1_id=1,
        geom2_id=2,
        geom1_name="tool_geom",
        geom2_name=manifest.scene.object.geom_name,
        body1_id=1,
        body2_id=2,
        body1_name="tool",
        body2_name=manifest.scene.object.body_name,
        point_world_m=(0.19, 0.0, 0.07),
        normal_world=(1.0, 0.0, 0.0),
        distance_m=-0.001,
        penetration_m=0.001,
        contact_frame_world=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        force_contact_frame_n=force,
        force_world_n=force,
        torque_contact_frame_nm=(0.0, 0.0, 0.0),
        torque_world_nm=(0.0, 0.0, 0.0),
        object_on_tool_force_world_n=force,
        tool_on_object_force_world_n=(-2.0, 0.0, 0.0),
        normal_force_n=2.0,
        tangential_force_world_n=(0.0, 0.0, 0.0),
        resultant_force_n=2.0,
        force_status=ContactEvidenceStatus.MEASURED,
    )
    return ContactEvidence(
        status=status,
        contacts=(record,),
        aggregate=ContactForceAggregate(
            contact_count=1,
            normal_force_n=2.0,
            tangential_force_world_n=(0.0, 0.0, 0.0),
            resultant_force_world_n=force,
            resultant_force_n=math.sqrt(4.0),
            object_on_tool_force_world_n=force,
            tool_on_object_force_world_n=(-2.0, 0.0, 0.0),
            object_on_tool_wrench_world_nm=(2.0, 0.0, 0.0, 0.0, 0.14, 0.0),
        ),
        **common,
    )


def _build_log(
    status: ContactEvidenceStatus = ContactEvidenceStatus.MEASURED,
) -> ContactTaskLog:
    manifest = _manifest()
    trial = ContactTrialIdentity("offline-contact-cube-demo")
    context = ContactTaskContext(
        manifest=manifest,
        dwell_interval_s=0.2,
        timeout_s=1.0,
        target_normal_force_band_n=(1.0, 3.0),
        trial=trial,
    )
    force_manifest = VirtualReactionForceManifest(
        contact_manifest=manifest,
        config=VirtualReactionForceConfig(
            output_frame=VirtualReactionForceFrame.MUJOCO_WORLD,
            deadband_n=0.0,
            low_pass_time_constant_s=0.0,
            smoothing_window_samples=1,
            rate_limit_n_per_s=None,
            magnitude_clamp_n=None,
            max_inter_sample_gap_s=0.5,
        ),
    )
    recorder = ContactTaskLogRecorder(
        ContactTaskLogHeader(
            context,
            force_manifest,
            source_kind=ContactTaskLogSourceKind.SYNTHETIC_FIXTURE,
        )
    )
    processor = VirtualReactionForceProcessor(force_manifest)
    binding = ContactTaskBinding(context)
    task_state = binding.initial_state()
    observations = []
    times = (0.0, 0.2) if status is ContactEvidenceStatus.MEASURED else (0.0,)
    for index, time_s in enumerate(times):
        evidence = _evidence(manifest, status, time_s=time_s, frame_index=index + 1)
        observation = ContactTaskObservation(
            elapsed_time_s=time_s,
            contact_evidence=evidence,
            tip_position_world_m=(0.1, 0.0, 0.07),
            object_position_world_m=manifest.scene.object.position_m,
            object_orientation_wxyz=manifest.scene.object.orientation_wxyz,
            contact_location_world_m=(
                evidence.target_contacts[0].point_world_m
                if evidence.target_contacts
                else None
            ),
            operator_status=ContactOperatorStatus.NOMINAL,
        )
        transition = binding.advance(task_state, observation)
        task_state = transition.state
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
    return recorder.finalize(outcome)


def _canonical_lines(data: bytes) -> list[dict[str, object]]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def _encode_lines(lines: list[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(
            line,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for line in lines
    )


def test_contact_log_round_trip_keeps_raw_force_and_outcome_separate() -> None:
    log = _build_log()
    encoded = log.to_jsonl()
    decoded = decode_contact_task_log(encoded)

    assert decoded == log
    assert decoded.header.binding_document["manifest_digest"] == log.header.context.manifest_digest
    assert decoded.header.binding_document["signal_manifest_digest"] == log.header.force_manifest.digest
    assert decoded.samples[0].observation.contact_evidence.status is ContactEvidenceStatus.MEASURED
    assert decoded.samples[0].force_signal.status is VirtualReactionForceStatus.ACTIVE
    assert decoded.samples[0].force_signal.force_n == (2.0, 0.0, 0.0)
    assert decoded.summary.outcome.classification is TaskTerminalClassification.SUCCESS
    assert contact_task_log_artifact_name(log.header.context.trial).endswith(".jsonl")
    assert encoded == decoded.to_jsonl()


@pytest.mark.parametrize(
    ("field", "typed_mismatch", "json_mismatch"),
    [
        ("dwell_interval_s", 0.25, 0.25),
        ("timeout_s", 1.5, 1.5),
        ("target_normal_force_band_n", (1.5, 3.5), [1.5, 3.5]),
        ("approach_alignment_min_cosine", 0.5, 0.5),
        ("normal_alignment_min_cosine", 0.5, 0.5),
        ("max_contact_location_drift_m", 0.01, 0.01),
        ("require_pose_measurement", True, True),
    ],
)
def test_contact_log_rejects_outcome_task_condition_mismatch(
    field: str,
    typed_mismatch: object,
    json_mismatch: object,
) -> None:
    log = _build_log()
    mismatched_outcome = replace(log.summary.outcome, **{field: typed_mismatch})
    with pytest.raises(ContactTaskLogError, match="task outcome conditions"):
        ContactTaskLog(
            header=log.header,
            samples=log.samples,
            summary=replace(log.summary, outcome=mismatched_outcome),
        )

    lines = _canonical_lines(log.to_jsonl())
    outcome = lines[-1]["outcome"]
    assert isinstance(outcome, dict)
    outcome[field] = json_mismatch
    with pytest.raises(ContactTaskLogError, match="task outcome conditions"):
        decode_contact_task_log(_encode_lines(lines))


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "binding", "sequence"])
def test_contact_log_decoder_rejects_untrusted_jsonl_changes(mutation: str) -> None:
    data = _build_log().to_jsonl()
    if mutation == "duplicate":
        data = data.replace(
            b'"record_kind":"header"',
            b'"record_kind":"header","record_kind":"header"',
            1,
        )
    else:
        lines = _canonical_lines(data)
        if mutation == "unknown":
            lines[1]["unexpected"] = True
        elif mutation == "binding":
            binding = lines[1]["binding"]
            assert isinstance(binding, dict)
            trial = binding["trial"]
            assert isinstance(trial, dict)
            trial["trial_id"] = "another-trial"
        else:
            lines[1]["sequence_index"] = 8
        data = _encode_lines(lines)
    with pytest.raises(ContactTaskLogError):
        decode_contact_task_log(data)


def test_contact_log_writer_is_atomic_strict_and_refuses_accidental_overwrite(
    tmp_path: Path,
) -> None:
    log = _build_log()
    destination = tmp_path / contact_task_log_artifact_name(log.header.context.trial)

    assert write_contact_task_log(destination, log) == destination
    assert read_contact_task_log(destination) == log
    assert destination.read_bytes() == log.to_jsonl()
    assert list(tmp_path.iterdir()) == [destination]
    with pytest.raises(FileExistsError):
        write_contact_task_log(destination, log)
    assert list(tmp_path.iterdir()) == [destination]


def test_contact_log_writer_preserves_concurrent_no_overwrite_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = _build_log()
    destination = tmp_path / contact_task_log_artifact_name(log.header.context.trial)
    winner_bytes = b"published by the concurrent writer\n"
    real_link = contact_task_log_module.os.link

    def publish_winner_before_link(source: Path, target: Path) -> None:
        assert target == destination
        destination.write_bytes(winner_bytes)
        real_link(source, target)

    monkeypatch.setattr(contact_task_log_module.os, "link", publish_winner_before_link)
    with pytest.raises(FileExistsError):
        write_contact_task_log(destination, log)

    assert destination.read_bytes() == winner_bytes
    assert list(tmp_path.iterdir()) == [destination]


def test_contact_log_writer_explicit_overwrite_replaces_existing(tmp_path: Path) -> None:
    log = _build_log()
    destination = tmp_path / contact_task_log_artifact_name(log.header.context.trial)
    destination.write_bytes(b"previous artifact\n")

    assert write_contact_task_log(destination, log, overwrite=True) == destination
    assert read_contact_task_log(destination) == log
    assert destination.read_bytes() == log.to_jsonl()
    assert list(tmp_path.iterdir()) == [destination]


def test_presentation_binds_scene_and_marks_late_samples_stale() -> None:
    log = _build_log()
    current = build_contact_task_presentation_v1(
        log,
        payload_time_s=0.2,
        payload_frame_index=2,
    )
    assert current["status"] == "available"
    cube = current["cube"]
    assert isinstance(cube, dict)
    assert cube["presentation_identity"] == "contact-cube/v1"
    contacts = current["contacts"]
    assert isinstance(contacts, list) and contacts
    force = current["derived_force"]
    assert isinstance(force, dict)
    assert force["force_n"] == [2.0, 0.0, 0.0]

    stale = build_contact_task_presentation_v1(
        log,
        payload_time_s=2.0,
        payload_frame_index=10,
    )
    assert stale["status"] == "stale"
    assert stale["reason"]


def test_no_contact_zero_is_valid_but_invalid_evidence_is_unavailable() -> None:
    no_contact = _build_log(ContactEvidenceStatus.NO_CONTACT)
    presentation = build_contact_task_presentation_v1(
        no_contact,
        payload_time_s=0.0,
        payload_frame_index=1,
    )
    assert presentation["status"] == "available"
    raw = presentation["raw_evidence"]
    derived = presentation["derived_force"]
    assert isinstance(raw, dict) and raw["status"] == "no_contact"
    assert isinstance(derived, dict)
    assert derived["status"] == "no_contact"
    assert derived["force_n"] == [0.0, 0.0, 0.0]

    invalid = _build_log(ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE)
    unavailable = build_contact_task_presentation_v1(
        invalid,
        payload_time_s=0.0,
        payload_frame_index=1,
    )
    assert unavailable["status"] == "unavailable"
    raw_invalid = unavailable["raw_evidence"]
    derived_invalid = unavailable["derived_force"]
    assert isinstance(raw_invalid, dict) and raw_invalid["force_world_n"] is None
    assert isinstance(derived_invalid, dict) and derived_invalid["force_n"] is None


def test_checked_in_viewer_demo_fixture_is_deterministic_and_uses_sample_pose() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    fixture = repository_root / "apps" / "mujoco-viewer" / "tests" / "fixtures" / "contact-cube-v1-demo.jsonl"
    expected = _build_log().to_jsonl()

    assert fixture.read_bytes() == expected
    decoded = decode_contact_task_log(expected)
    sample = decoded.samples[-1].observation
    assert sample.object_position_world_m == decoded.header.context.manifest.scene.object.position_m
    assert sample.contact_location_world_m == (0.19, 0.0, 0.07)
    assert decoded.header.source_kind is ContactTaskLogSourceKind.SYNTHETIC_FIXTURE


def test_contact_log_requires_exact_raw_force_copy() -> None:
    log = _build_log()
    lines = _canonical_lines(log.to_jsonl())
    signal = lines[1]["derived_reaction_force"]
    assert isinstance(signal, dict)
    raw_force = signal["raw_force_world_n"]
    assert isinstance(raw_force, list)
    raw_force[0] = 2.0000005

    with pytest.raises(ContactTaskLogError, match="exactly preserve"):
        decode_contact_task_log(_encode_lines(lines))


def test_contact_log_requires_success_to_match_final_state_and_contact() -> None:
    no_contact_log = _build_log(ContactEvidenceStatus.NO_CONTACT)
    final_state = no_contact_log.samples[-1].task_state
    assert final_state.classification is TaskTerminalClassification.RUNNING
    assert no_contact_log.summary.outcome.classification is TaskTerminalClassification.FAILURE
    assert decode_contact_task_log(no_contact_log.to_jsonl()) == no_contact_log

    measured_log = _build_log()
    success_state = ContactTaskLogTaskState(
        phase=ContactTaskPhase.SUCCESS,
        classification=TaskTerminalClassification.SUCCESS,
    )
    forged_sample = replace(no_contact_log.samples[-1], task_state=success_state)
    forged_outcome = replace(
        measured_log.summary.outcome,
        observations_count=len(no_contact_log.samples),
    )
    forged_summary = replace(no_contact_log.summary, outcome=forged_outcome)

    with pytest.raises(ContactTaskLogError, match="measured final target contact"):
        replace(
            no_contact_log,
            samples=(forged_sample,),
            summary=forged_summary,
        )


def test_contact_log_rejects_final_task_state_outcome_mismatch() -> None:
    log = _build_log()
    failure_state = ContactTaskLogTaskState(
        phase=ContactTaskPhase.FAILURE,
        classification=TaskTerminalClassification.FAILURE,
        reason="synthetic final-state mismatch",
    )

    with pytest.raises(ContactTaskLogError, match="successful outcome"):
        replace(
            log,
            samples=(
                *log.samples[:-1],
                replace(log.samples[-1], task_state=failure_state),
            ),
        )
