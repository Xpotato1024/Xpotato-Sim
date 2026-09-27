from __future__ import annotations

import math
from dataclasses import replace

import pytest

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
from xpotato_sim.runtime.contact.task_contract import ContactTrialIdentity
from xpotato_sim.runtime.contact import (
    VirtualReactionForceConfig,
    VirtualReactionForceError,
    VirtualReactionForceFrame,
    VirtualReactionForceManifest,
    VirtualReactionForceProcessor,
    VirtualReactionForceStatus,
    decode_virtual_reaction_force_manifest,
    encode_virtual_reaction_force_manifest,
)
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    ROLE_ATTRIBUTE_WILDCARD,
    SemanticRole,
    SemanticRoleRequirement,
    VersionedIdentity,
)


def _contact_manifest() -> ContactTaskManifest:
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
        software_revision_identity="test-revision:virtual-reaction-force",
    )


def _evidence(
    manifest: ContactTaskManifest,
    *,
    sample_time_s: float,
    simulation_time_s: float,
    force: tuple[float, float, float] | None = None,
    status: ContactEvidenceStatus = ContactEvidenceStatus.MEASURED,
    frame_index: int | None = None,
) -> ContactEvidence:
    common = {
        "scene_identity": manifest.scene.identity,
        "object_identity": manifest.object.identity,
        "manifest_digest": contact_manifest_digest(manifest),
        "sample_time_s": sample_time_s,
        "simulation_time_s": simulation_time_s,
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
            reason="fixture measurement unavailable",
            **common,
        )
    if force is None:
        raise ValueError("measured fixture requires force")
    x, y, z = force
    magnitude = math.sqrt(x * x + y * y + z * z)
    tangent = (0.0, y, z)
    record = ContactRecord(
        contact_identity="target-contact-0",
        classification=ContactPairClassification.TARGET_OBJECT,
        geom1_id=1,
        geom2_id=2,
        geom1_name="tool_geom",
        geom2_name="object_geom",
        body1_id=1,
        body2_id=2,
        body1_name="tool",
        body2_name="object",
        point_world_m=(0.0, 0.0, 0.0),
        normal_world=(1.0, 0.0, 0.0),
        distance_m=-0.001,
        penetration_m=0.001,
        contact_frame_world=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        force_contact_frame_n=force,
        force_world_n=force,
        torque_contact_frame_nm=(0.0, 0.0, 0.0),
        torque_world_nm=(0.0, 0.0, 0.0),
        object_on_tool_force_world_n=force,
        tool_on_object_force_world_n=(-x, -y, -z),
        normal_force_n=abs(x),
        tangential_force_world_n=tangent,
        resultant_force_n=magnitude,
        force_status=ContactEvidenceStatus.MEASURED,
    )
    aggregate = ContactForceAggregate(
        contact_count=1,
        normal_force_n=abs(x),
        tangential_force_world_n=tangent,
        resultant_force_world_n=force,
        resultant_force_n=magnitude,
        object_on_tool_force_world_n=force,
        tool_on_object_force_world_n=(-x, -y, -z),
        object_on_tool_wrench_world_nm=(x, y, z, 0.0, 0.0, 0.0),
    )
    return ContactEvidence(
        status=status,
        contacts=(record,),
        aggregate=aggregate,
        **common,
    )


def _config(
    *,
    frame: VirtualReactionForceFrame = VirtualReactionForceFrame.MUJOCO_WORLD,
    deadband: float = 0.0,
    tau: float = 0.0,
    window: int = 1,
    rate_limit: float | None = None,
    clamp: float | None = None,
    max_gap: float = 10.0,
) -> VirtualReactionForceConfig:
    return VirtualReactionForceConfig(
        output_frame=frame,
        deadband_n=deadband,
        low_pass_time_constant_s=tau,
        smoothing_window_samples=window,
        rate_limit_n_per_s=rate_limit,
        magnitude_clamp_n=clamp,
        max_inter_sample_gap_s=max_gap,
    )


def _signal_manifest(
    contact: ContactTaskManifest,
    config: VirtualReactionForceConfig | None = None,
) -> VirtualReactionForceManifest:
    return VirtualReactionForceManifest(
        contact_manifest=contact,
        config=config or _config(),
    )


def test_manifest_digest_binds_config_and_decode_is_strict() -> None:
    contact = _contact_manifest()
    manifest = _signal_manifest(contact, _config(deadband=0.5, clamp=8.0))
    encoded = encode_virtual_reaction_force_manifest(manifest)

    decoded = decode_virtual_reaction_force_manifest(
        encoded,
        contact_manifest=contact,
    )
    assert decoded == manifest
    assert decoded.digest == manifest.digest
    assert _signal_manifest(contact, _config(deadband=0.6, clamp=8.0)).digest != manifest.digest

    with pytest.raises(VirtualReactionForceError, match="unexpected fields"):
        decode_virtual_reaction_force_manifest(
            encoded[:-1] + b',"unexpected":true}',
            contact_manifest=contact,
        )
    with pytest.raises(VirtualReactionForceError, match="not canonical"):
        decode_virtual_reaction_force_manifest(
            encoded.replace(b'"deadband_n":0.5', b'"deadband_n": 0.5'),
            contact_manifest=contact,
        )
    with pytest.raises(VirtualReactionForceError):
        decode_virtual_reaction_force_manifest(
            encoded,
            contact_manifest=replace(
                contact,
                software_revision_identity="test-revision:changed",
            ),
        )


def test_frame_transform_and_replay_are_deterministic() -> None:
    contact = _contact_manifest()
    manifest = _signal_manifest(
        contact,
        _config(frame=VirtualReactionForceFrame.TOOL),
    )
    trial = ContactTrialIdentity("fixture-trial")
    stream = (
        _evidence(contact, sample_time_s=0.0, simulation_time_s=0.0, force=(2.0, 3.0, 0.0), frame_index=1),
        _evidence(contact, sample_time_s=0.1, simulation_time_s=0.1, force=(4.0, 0.0, 0.0), frame_index=2),
    )
    rotation = (0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)

    def replay() -> list[bytes]:
        processor = VirtualReactionForceProcessor(manifest)
        return [
            processor.process(
                sample,
                trial=trial,
                world_to_output_rotation=rotation,
            ).canonical_bytes()
            for sample in stream
        ]

    first = replay()
    assert replay() == first
    signal = VirtualReactionForceProcessor(manifest).process(
        stream[0],
        trial=trial,
        world_to_output_rotation=rotation,
    )
    assert signal.raw_force_world_n == (2.0, 3.0, 0.0)
    assert signal.raw_force_output_frame_n == (-3.0, 2.0, 0.0)
    assert signal.force_n == (-3.0, 2.0, 0.0)
    assert signal.output_frame is VirtualReactionForceFrame.TOOL


def test_reference_fixture_covers_deadband_smoothing_rate_and_clamp() -> None:
    contact = _contact_manifest()
    trial = ContactTrialIdentity("reference-pipeline")

    manifest = _signal_manifest(
        contact,
        _config(deadband=0.5, window=2, rate_limit=2.0, clamp=2.0),
    )
    processor = VirtualReactionForceProcessor(manifest)
    first = processor.process(
        _evidence(contact, sample_time_s=0.0, simulation_time_s=0.0, force=(4.0, 0.0, 0.0), frame_index=1),
        trial=trial,
    )
    second = processor.process(
        _evidence(contact, sample_time_s=0.1, simulation_time_s=0.1, force=(5.0, 0.0, 0.0), frame_index=2),
        trial=trial,
    )
    deadbanded = processor.process(
        _evidence(contact, sample_time_s=0.2, simulation_time_s=0.2, force=(0.25, 0.0, 0.0), frame_index=3),
        trial=trial,
    )

    assert first.force_n == (2.0, 0.0, 0.0)
    assert first.raw_force_world_n == (4.0, 0.0, 0.0)
    assert first.filtered and first.clamped and first.saturated
    assert second.rate_limited and second.clamped
    assert second.force_n == (2.0, 0.0, 0.0)
    assert second.raw_force_world_n == (5.0, 0.0, 0.0)
    assert deadbanded.deadbanded and deadbanded.filtered
    assert deadbanded.raw_force_world_n == (0.25, 0.0, 0.0)
    assert deadbanded.force_n == (2.0, 0.0, 0.0)

    low_pass_manifest = _signal_manifest(contact, _config(tau=1.0))
    low_pass_processor = VirtualReactionForceProcessor(low_pass_manifest)
    low_pass_processor.process(
        _evidence(contact, sample_time_s=0.0, simulation_time_s=0.0, force=(1.0, 0.0, 0.0), frame_index=1),
        trial=trial,
    )
    low_pass = low_pass_processor.process(
        _evidence(contact, sample_time_s=1.0, simulation_time_s=1.0, force=(3.0, 0.0, 0.0), frame_index=2),
        trial=trial,
    )
    assert low_pass.filtered
    assert low_pass.force_n == pytest.approx((1.0 + (1.0 - math.exp(-1.0)) * 2.0, 0.0, 0.0))


def test_lifecycle_no_contact_missing_unavailable_and_invalid() -> None:
    contact = _contact_manifest()
    processor = VirtualReactionForceProcessor(
        _signal_manifest(contact, _config(window=2))
    )
    trial = ContactTrialIdentity("lifecycle")

    processor.process(
        _evidence(contact, sample_time_s=0.0, simulation_time_s=0.0, force=(1.0, 0.0, 0.0), frame_index=1),
        trial=trial,
    )
    no_contact = processor.process(
        _evidence(
            contact,
            sample_time_s=0.1,
            simulation_time_s=0.1,
            status=ContactEvidenceStatus.NO_CONTACT,
            frame_index=2,
        ),
        trial=trial,
    )
    assert no_contact.status is VirtualReactionForceStatus.NO_CONTACT
    assert no_contact.force_n == (0.0, 0.0, 0.0)
    assert no_contact.raw_force_world_n == (0.0, 0.0, 0.0)

    after_no_contact = processor.process(
        _evidence(contact, sample_time_s=0.2, simulation_time_s=0.2, force=(3.0, 0.0, 0.0), frame_index=3),
        trial=trial,
    )
    assert after_no_contact.force_n == (3.0, 0.0, 0.0)

    missing = processor.process(None, trial=trial)
    assert missing.status is VirtualReactionForceStatus.MEASUREMENT_UNAVAILABLE
    assert missing.force_n is None
    assert missing.raw_force_world_n is None

    unavailable = processor.process(
        _evidence(
            contact,
            sample_time_s=0.3,
            simulation_time_s=0.3,
            status=ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE,
            frame_index=4,
        ),
        trial=trial,
    )
    invalid = processor.process(
        _evidence(
            contact,
            sample_time_s=0.4,
            simulation_time_s=0.4,
            status=ContactEvidenceStatus.SOLVER_INVALID,
            frame_index=5,
        ),
        trial=trial,
    )
    assert unavailable.status is VirtualReactionForceStatus.MEASUREMENT_UNAVAILABLE
    assert invalid.status is VirtualReactionForceStatus.INVALID
    assert unavailable.force_n is None
    assert invalid.force_n is None


def test_missing_frame_index_preserves_monotonic_index() -> None:
    contact = _contact_manifest()
    processor = VirtualReactionForceProcessor(
        _signal_manifest(contact, _config())
    )
    trial = ContactTrialIdentity("optional-frame-index")
    first = processor.process(
        _evidence(
            contact,
            sample_time_s=0.0,
            simulation_time_s=0.0,
            force=(1.0, 0.0, 0.0),
            frame_index=10,
        ),
        trial=trial,
    )
    missing_index = processor.process(
        _evidence(
            contact,
            sample_time_s=0.1,
            simulation_time_s=0.1,
            force=(2.0, 0.0, 0.0),
            frame_index=None,
        ),
        trial=trial,
    )
    regressed_index = processor.process(
        _evidence(
            contact,
            sample_time_s=0.2,
            simulation_time_s=0.2,
            force=(3.0, 0.0, 0.0),
            frame_index=9,
        ),
        trial=trial,
    )
    assert first.status is VirtualReactionForceStatus.ACTIVE
    assert missing_index.status is VirtualReactionForceStatus.ACTIVE
    assert regressed_index.status is VirtualReactionForceStatus.INVALID
    assert regressed_index.force_n is None


def test_stale_trial_change_and_invalid_transform_fail_closed() -> None:
    contact = _contact_manifest()
    manifest = _signal_manifest(
        contact,
        _config(window=2, max_gap=0.5),
    )
    processor = VirtualReactionForceProcessor(manifest)
    first_trial = ContactTrialIdentity("first-trial")
    second_trial = ContactTrialIdentity("second-trial")
    processor.process(
        _evidence(contact, sample_time_s=0.0, simulation_time_s=0.0, force=(1.0, 0.0, 0.0), frame_index=1),
        trial=first_trial,
    )
    stale = processor.process(
        _evidence(contact, sample_time_s=1.0, simulation_time_s=1.0, force=(2.0, 0.0, 0.0), frame_index=2),
        trial=first_trial,
    )
    assert stale.status is VirtualReactionForceStatus.STALE
    assert stale.force_n is None
    recovered = processor.process(
        _evidence(contact, sample_time_s=1.1, simulation_time_s=1.1, force=(3.0, 0.0, 0.0), frame_index=3),
        trial=first_trial,
    )
    assert recovered.status is VirtualReactionForceStatus.ACTIVE
    assert recovered.force_n == (3.0, 0.0, 0.0)

    reset_signal = processor.process(
        _evidence(contact, sample_time_s=0.0, simulation_time_s=0.0, force=(5.0, 0.0, 0.0), frame_index=1),
        trial=second_trial,
    )
    assert reset_signal.force_n == (5.0, 0.0, 0.0)

    tool_manifest = _signal_manifest(
        contact,
        _config(frame=VirtualReactionForceFrame.TOOL),
    )
    malformed_transform = VirtualReactionForceProcessor(tool_manifest).process(
        _evidence(contact, sample_time_s=0.0, simulation_time_s=0.0, force=(2.0, 0.0, 0.0), frame_index=1),
        trial=first_trial,
        world_to_output_rotation=(1.0, 0.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, 1.0),
    )
    assert malformed_transform.status is VirtualReactionForceStatus.INVALID
    assert malformed_transform.force_n is None
    assert malformed_transform.raw_force_world_n == (2.0, 0.0, 0.0)

    wrong_source = VirtualReactionForceProcessor(manifest).process(
        _evidence(
            replace(contact, software_revision_identity="test-revision:other-source"),
            sample_time_s=0.0,
            simulation_time_s=0.0,
            force=(8.0, 0.0, 0.0),
            frame_index=1,
        ),
        trial=first_trial,
    )
    assert wrong_source.status is VirtualReactionForceStatus.INVALID
    assert wrong_source.force_n is None
    with pytest.raises((TypeError, ValueError)):
        _config(deadband=math.inf)