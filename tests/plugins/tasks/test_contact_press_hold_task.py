from __future__ import annotations

from dataclasses import replace

import pytest

from xpotato_sim.plugins.evaluations.contact_outcome import CONTACT_OUTCOME_PLUGIN
from xpotato_sim.plugins.tasks.contact_press_hold_task import (
    CONTACT_PRESS_HOLD_TASK_PLUGIN,
    CONTACT_TASK_OUTCOME_EVIDENCE,
    CONTACT_TASK_TERMINAL_EVIDENCE,
    ContactOperatorStatus,
    ContactRetryPolicy,
    ContactTaskContext,
    ContactTaskObservation,
    ContactTaskPhase,
    ContactTaskRunner,
    ContactTrialIdentity,
    run_contact_task_fixture,
)
from xpotato_sim.runtime.contact.evidence import (
    ContactEvidence,
    ContactEvidenceStatus,
    ContactForceAggregate,
    ContactPairClassification,
    ContactRecord,
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
from xpotato_sim.runtime.experiment.contracts import (
    EvidenceStatus,
    PluginSelection,
    SemanticRole,
    SemanticRoleRequirement,
    TaskTerminalClassification,
    VersionedIdentity,
)


def _manifest() -> ContactTaskManifest:
    object_value = ContactCubeObject(
        identity=VersionedIdentity("contact_cube", 1),
        position_m=(0.0, 0.0, 0.2),
        size_m=(0.03, 0.03, 0.03),
        mass_kg=0.2,
        material=ContactMaterial("contact-red", (0.8, 0.1, 0.1, 1.0)),
        friction=(0.7, 0.01, 0.001),
    )
    scene = ContactSceneContract(
        identity=VersionedIdentity("contact_cube_scene", 1),
        object=object_value,
        reset=ContactResetState(
            qpos_rad=(0.0,),
            qvel_rad_s=(0.0,),
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
        software_revision_identity="test-revision:contact-task",
    )


def _context(**kwargs: object) -> ContactTaskContext:
    values = {
        "manifest": _manifest(),
        "dwell_interval_s": 0.2,
        "timeout_s": 1.0,
        "trial": ContactTrialIdentity("contact-test-trial"),
    }
    values.update(kwargs)
    return ContactTaskContext(**values)  # type: ignore[arg-type]


def _no_contact(manifest: ContactTaskManifest, time_s: float) -> ContactTaskObservation:
    return ContactTaskObservation(
        elapsed_time_s=time_s,
        contact_evidence=ContactEvidence(
            status=ContactEvidenceStatus.NO_CONTACT,
            scene_identity=manifest.scene.identity,
            object_identity=manifest.scene.object.identity,
            manifest_digest=contact_manifest_digest(manifest),
            sample_time_s=time_s,
            simulation_time_s=time_s,
            contacts=(),
            aggregate=ContactForceAggregate.no_contact(),
        ),
    )


def _contact(
    manifest: ContactTaskManifest,
    time_s: float,
    *,
    penetration_m: float = 0.001,
    normal_force_n: float = 2.0,
    location_x_m: float = 0.0,
    operator_status: ContactOperatorStatus = ContactOperatorStatus.NOMINAL,
    reason: str | None = None,
) -> ContactTaskObservation:
    record = ContactRecord(
        contact_identity="target_object:contact_cube_geom:tool_geom:0",
        classification=ContactPairClassification.TARGET_OBJECT,
        geom1_id=2,
        geom2_id=1,
        geom1_name=manifest.scene.object.geom_name,
        geom2_name="tool_geom",
        body1_id=2,
        body2_id=1,
        body1_name=manifest.scene.object.body_name,
        body2_name="tool",
        point_world_m=(location_x_m, 0.0, 0.2),
        normal_world=(1.0, 0.0, 0.0),
        distance_m=-penetration_m,
        penetration_m=penetration_m,
        contact_frame_world=(
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ),
        force_contact_frame_n=(normal_force_n, 0.0, 0.0),
        force_world_n=(normal_force_n, 0.0, 0.0),
        torque_contact_frame_nm=(0.0, 0.0, 0.0),
        torque_world_nm=(0.0, 0.0, 0.0),
        object_on_tool_force_world_n=(normal_force_n, 0.0, 0.0),
        tool_on_object_force_world_n=(-normal_force_n, 0.0, 0.0),
        normal_force_n=normal_force_n,
        tangential_force_world_n=(0.0, 0.0, 0.0),
        resultant_force_n=normal_force_n,
        force_status=ContactEvidenceStatus.MEASURED,
    )
    return ContactTaskObservation(
        elapsed_time_s=time_s,
        contact_evidence=ContactEvidence(
            status=ContactEvidenceStatus.MEASURED,
            scene_identity=manifest.scene.identity,
            object_identity=manifest.scene.object.identity,
            manifest_digest=contact_manifest_digest(manifest),
            sample_time_s=time_s,
            simulation_time_s=time_s,
            contacts=(record,),
            aggregate=ContactForceAggregate(
                contact_count=1,
                normal_force_n=normal_force_n,
                tangential_force_world_n=(0.0, 0.0, 0.0),
                resultant_force_world_n=(normal_force_n, 0.0, 0.0),
                resultant_force_n=normal_force_n,
                object_on_tool_force_world_n=(normal_force_n, 0.0, 0.0),
                tool_on_object_force_world_n=(-normal_force_n, 0.0, 0.0),
                object_on_tool_wrench_world_nm=(
                    normal_force_n,
                    0.0,
                    0.0,
                    0.0,
                    normal_force_n * 0.2,
                    0.0,
                ),
            ),
        ),
        tip_position_world_m=(0.0, 0.0, 0.2),
        object_position_world_m=manifest.scene.object.position_m,
        object_orientation_wxyz=manifest.scene.object.orientation_wxyz,
        operator_status=operator_status,
        reason=reason,
    )


def _invalid(manifest: ContactTaskManifest, time_s: float) -> ContactTaskObservation:
    return ContactTaskObservation(
        elapsed_time_s=time_s,
        contact_evidence=ContactEvidence(
            status=ContactEvidenceStatus.SOLVER_INVALID,
            scene_identity=manifest.scene.identity,
            object_identity=manifest.scene.object.identity,
            manifest_digest=contact_manifest_digest(manifest),
            sample_time_s=time_s,
            simulation_time_s=time_s,
            contacts=(),
            aggregate=None,
            reason="fixture solver state is invalid",
        ),
    )


def test_contact_task_plugin_declares_task_owned_evidence() -> None:
    assert CONTACT_PRESS_HOLD_TASK_PLUGIN.identity == VersionedIdentity(
        "contact_press_hold_task", 1
    )
    assert CONTACT_PRESS_HOLD_TASK_PLUGIN.produced_evidence == frozenset(
        {CONTACT_TASK_TERMINAL_EVIDENCE, CONTACT_TASK_OUTCOME_EVIDENCE}
    )
    assert CONTACT_PRESS_HOLD_TASK_PLUGIN.compatible_backend_kinds == frozenset(
        {"mujoco"}
    )


def test_measured_contact_band_and_dwell_produce_success_outcome() -> None:
    context = _context()
    manifest = context.manifest
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})
    first = binding.advance(binding.initial_state(), _no_contact(manifest, 0.0))
    contact = binding.advance(first.state, _contact(manifest, 0.2))
    terminal = binding.advance(contact.state, _contact(manifest, 0.4))

    assert contact.state.phase is ContactTaskPhase.FIRST_CONTACT
    assert terminal.classification is TaskTerminalClassification.SUCCESS
    outcome = terminal.evidence.require(CONTACT_TASK_OUTCOME_EVIDENCE)
    assert outcome.status is EvidenceStatus.MEASURED
    assert outcome.value["completion_time_s"] == pytest.approx(0.4)  # type: ignore[index]
    assert outcome.value["first_contact_time_s"] == pytest.approx(0.2)  # type: ignore[index]
    assert outcome.value["peak_normal_force_n"] == pytest.approx(2.0)  # type: ignore[index]
    assert outcome.value["dwell_interval_s"] == pytest.approx(context.dwell_interval_s)  # type: ignore[index]
    assert outcome.value["timeout_s"] == pytest.approx(context.timeout_s)  # type: ignore[index]
    assert outcome.value["target_penetration_band_m"] == [0.0, 0.002]  # type: ignore[index]
    assert outcome.value["require_pose_measurement"] is False  # type: ignore[index]
    assert terminal.evidence.require(CONTACT_TASK_TERMINAL_EVIDENCE).status is EvidenceStatus.MEASURED

    metric = CONTACT_OUTCOME_PLUGIN.derive_metric(terminal.evidence, {})
    assert metric.status is EvidenceStatus.MEASURED
    assert metric.value["classification"] == "success"  # type: ignore[index]


def test_contact_loss_resets_hold_and_recontact_is_counted() -> None:
    context = _context()
    manifest = context.manifest
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})
    state = binding.advance(binding.initial_state(), _no_contact(manifest, 0.0)).state
    state = binding.advance(state, _contact(manifest, 0.1)).state
    state = binding.advance(state, _no_contact(manifest, 0.2)).state
    state = binding.advance(state, _contact(manifest, 0.3)).state
    terminal = binding.advance(state, _contact(manifest, 0.5))

    assert terminal.classification is TaskTerminalClassification.SUCCESS
    assert terminal.state.contact_loss_count == 1
    assert terminal.state.recontact_count == 1
    assert terminal.state.dwell_started_at_s == pytest.approx(0.3)


def test_timeout_failure_has_no_completion_or_force_when_no_contact() -> None:
    context = _context(timeout_s=0.5)
    manifest = context.manifest
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})
    first = binding.advance(binding.initial_state(), _no_contact(manifest, 0.0))
    terminal = binding.advance(first.state, _no_contact(manifest, 0.5))

    assert terminal.classification is TaskTerminalClassification.FAILURE
    value = terminal.evidence.require(CONTACT_TASK_OUTCOME_EVIDENCE).value
    assert value["completion_time_s"] is None  # type: ignore[index]
    assert value["peak_normal_force_n"] is None  # type: ignore[index]
    assert value["max_penetration_m"] is None  # type: ignore[index]
    metric = CONTACT_OUTCOME_PLUGIN.derive_metric(terminal.evidence, {})
    assert metric.status is EvidenceStatus.MEASURED
    assert metric.value["classification"] == "failure"  # type: ignore[index]


def test_declared_approach_gate_rejects_missing_tip_measurement() -> None:
    context = _context(approach_alignment_min_cosine=0.0)
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})

    terminal = binding.advance(
        binding.initial_state(),
        _no_contact(context.manifest, 0.0),
    )

    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert terminal.state.terminal_reason == (
        "approach alignment gate requires measured tip positions"
    )


@pytest.mark.parametrize(
    (
        "first_sample_time_s",
        "first_simulation_time_s",
        "sample_time_s",
        "simulation_time_s",
        "reason_fragment",
    ),
    (
        (0.0, 0.0, 0.0, 0.2, "sample_time_s is stale"),
        (0.2, 0.2, 0.3, 0.1, "simulation_time_s moved backwards"),
    ),
)
def test_nonmonotonic_or_stale_measured_time_is_invalid_and_replayable(
    first_sample_time_s: float,
    first_simulation_time_s: float,
    sample_time_s: float,
    simulation_time_s: float,
    reason_fragment: str,
) -> None:
    context = _context()
    manifest = context.manifest
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})
    first = _no_contact(manifest, 0.0)
    first = replace(
        first,
        contact_evidence=replace(
            first.contact_evidence,
            sample_time_s=first_sample_time_s,
            simulation_time_s=first_simulation_time_s,
        ),
    )
    state = binding.advance(binding.initial_state(), first).state
    second = _no_contact(manifest, 0.2)
    second = replace(
        second,
        contact_evidence=replace(
            second.contact_evidence,
            sample_time_s=sample_time_s,
            simulation_time_s=simulation_time_s,
        ),
    )
    terminal = binding.advance(state, second)

    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert reason_fragment in (terminal.state.terminal_reason or "")
    assert terminal.state.observations == (first, second)
    outcome = terminal.evidence.require(CONTACT_TASK_OUTCOME_EVIDENCE)
    assert outcome.status is EvidenceStatus.INVALID


def test_measured_aggregate_mismatch_is_rejected_before_task_success() -> None:
    context = _context()
    manifest = context.manifest
    observation = _contact(manifest, 0.0)
    evidence = observation.contact_evidence
    assert evidence.aggregate is not None
    # ContactEvidence constructorのguardとは独立して、task boundaryを検証する。
    # hostile callerが構築後のobjectを変更してもsuccessへ進めないことを確認する。
    object.__setattr__(
        evidence,
        "aggregate",
        replace(evidence.aggregate, contact_count=2),
    )
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})
    terminal = binding.advance(binding.initial_state(), observation)

    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert terminal.state.observations == (observation,)
    assert "aggregate" in (terminal.state.terminal_reason or "")


def test_measured_aggregate_value_mismatch_is_rejected_before_task_success() -> None:
    context = _context()
    manifest = context.manifest
    observation = _contact(manifest, 0.0)
    evidence = observation.contact_evidence
    assert evidence.aggregate is not None
    object.__setattr__(
        evidence,
        "aggregate",
        replace(evidence.aggregate, normal_force_n=999.0),
    )
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})
    terminal = binding.advance(binding.initial_state(), observation)

    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert terminal.state.observations == (observation,)
    assert "failed validation" in (terminal.state.terminal_reason or "")


def test_measured_target_force_status_mismatch_is_rejected_before_task_success() -> None:
    context = _context()
    manifest = context.manifest
    observation = _contact(manifest, 0.0)
    record = observation.contact_evidence.contacts[0]
    object.__setattr__(record, "force_status", ContactEvidenceStatus.INVALID_CONTACT)
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})
    terminal = binding.advance(binding.initial_state(), observation)

    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert terminal.state.observations == (observation,)
    assert "failed validation" in (terminal.state.terminal_reason or "")


@pytest.mark.parametrize(
    "status",
    (
        ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE,
        ContactEvidenceStatus.INVALID_CONTACT,
        ContactEvidenceStatus.SOLVER_INVALID,
    ),
)
def test_invalid_or_unavailable_contact_is_technical_invalid(status: ContactEvidenceStatus) -> None:
    context = _context()
    manifest = context.manifest
    observation = _invalid(manifest, 0.0)
    observation = ContactTaskObservation(
        elapsed_time_s=0.0,
        contact_evidence=ContactEvidence(
            status=status,
            scene_identity=manifest.scene.identity,
            object_identity=manifest.scene.object.identity,
            manifest_digest=contact_manifest_digest(manifest),
            sample_time_s=0.0,
            simulation_time_s=0.0,
            contacts=(),
            aggregate=None,
            reason="contact measurement is unavailable",
        ),
    )
    binding = CONTACT_PRESS_HOLD_TASK_PLUGIN.bind_context(context, {})
    terminal = binding.advance(binding.initial_state(), observation)

    assert terminal.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert terminal.state.observations == (observation,)
    assert terminal.evidence.require(CONTACT_TASK_OUTCOME_EVIDENCE).status is EvidenceStatus.INVALID
    metric = CONTACT_OUTCOME_PLUGIN.derive_metric(terminal.evidence, {})
    assert metric.value is None
    assert metric.status is EvidenceStatus.INVALID


@pytest.mark.parametrize(
    "operator_status",
    (
        ContactOperatorStatus.HELD,
        ContactOperatorStatus.REJECTED,
        ContactOperatorStatus.STALE,
        ContactOperatorStatus.TIMEOUT,
    ),
)
def test_operator_failure_is_not_technical_retry(operator_status: ContactOperatorStatus) -> None:
    context = _context()
    manifest = context.manifest
    result = run_contact_task_fixture(
        context,
        (
            _no_contact(manifest, 0.0),
            _contact(
                manifest,
                0.1,
                operator_status=operator_status,
                reason=f"operator status={operator_status.value}",
            ),
        ),
        retry_policy=ContactRetryPolicy(max_attempts=2),
    )
    assert len(result.attempts) == 1
    assert result.classification is TaskTerminalClassification.FAILURE


def test_bounded_retry_preserves_original_technical_invalid_attempt() -> None:
    context = _context()
    manifest = context.manifest
    result = run_contact_task_fixture(
        context,
        (
            (_invalid(manifest, 0.0),),
            (
                _no_contact(manifest, 0.0),
                _contact(manifest, 0.2),
                _contact(manifest, 0.4),
            ),
        ),
        retry_policy=ContactRetryPolicy(max_attempts=2),
    )

    assert len(result.attempts) == 2
    assert result.attempts[0].outcome.classification is TaskTerminalClassification.TECHNICAL_INVALID
    assert result.attempts[0].outcome.completion_time_s is None
    assert result.attempts[1].outcome.classification is TaskTerminalClassification.SUCCESS
    assert result.attempts[1].trial.retry_of_trial_id == result.attempts[0].trial.trial_id


def test_outcome_summary_regeneration_is_deterministic() -> None:
    context = _context()
    manifest = context.manifest
    observations = (
        _no_contact(manifest, 0.0),
        _contact(manifest, 0.2, location_x_m=0.01),
        _contact(manifest, 0.4, location_x_m=0.011),
    )
    first = ContactTaskRunner(context).run(observations)
    second = ContactTaskRunner(context).run(observations)

    assert first.outcome.canonical_bytes() == second.outcome.canonical_bytes()
    assert first.canonical_bytes() == second.canonical_bytes()
