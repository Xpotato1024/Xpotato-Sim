from __future__ import annotations

from dataclasses import replace

import mujoco
import pytest

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
from xpotato_sim.runtime.contact.evidence import (
    CONTACT_EVIDENCE_PROVENANCE,
    ContactEvidence,
    ContactEvidenceError,
    ContactEvidenceExtractor,
    ContactEvidenceStatus,
    ContactPairClassification,
    extract_contact_evidence_from_scene_instance,
)
from xpotato_sim.runtime.contact.scene import (
    ContactSceneBuildRequest,
    ContactSceneComposer,
)
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    SemanticRole,
    SemanticRoleRequirement,
    VersionedIdentity,
)


def _contact_fixture_xml() -> str:
    return """
    <mujoco model="contact_evidence_fixture">
      <option gravity="0 0 -9.81"/>
      <worldbody>
        <geom name="floor" type="plane" size="2 2 0.1"/>
        <body name="tool" pos="0 0 0.1">
          <geom name="tool_geom" type="sphere" size="0.04"/>
        </body>
        <body name="object" pos="0 0 0.2">
          <freejoint/>
          <geom name="object_geom" type="box" size="0.03 0.03 0.03" mass="1"/>
        </body>
      </worldbody>
    </mujoco>
    """


def _scene_request() -> ContactSceneBuildRequest:
    object_value = ContactCubeObject(
        identity=VersionedIdentity("contact_cube", 1),
        position_m=(0.3, 0.0, 0.2),
        size_m=(0.03, 0.03, 0.03),
        mass_kg=0.2,
        material=ContactMaterial("contact-red", (0.8, 0.1, 0.1, 1.0)),
        friction=(0.7, 0.01, 0.001),
    )
    reset = ContactResetState(
        qpos_rad=(0.0,),
        qvel_rad_s=(0.0,),
        object_position_m=object_value.position_m,
        object_orientation_wxyz=object_value.orientation_wxyz,
    )
    scene = ContactSceneContract(
        identity=VersionedIdentity("contact_cube_scene", 1),
        object=object_value,
        reset=reset,
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
    return ContactSceneBuildRequest(
        manifest=ContactTaskManifest(
            robot_bundle=PluginSelection("fast_arm", 1),
            environment=PluginSelection("contact_cube_environment", 1),
            task=PluginSelection("contact_press_hold_task", 1),
            evaluators=(PluginSelection("contact_outcome", 1),),
            scene=scene,
            software_revision_identity="test-revision:contact-evidence",
        ),
        model_xml=(
            b"""
            <mujoco model="contact_fixture">
              <option gravity="0 0 -9.81"/>
              <worldbody>
                <geom name="floor" type="plane" size="1 1 0.1"/>
                <body name="tool" pos="0 0 0.3">
                  <joint name="tool_slide" type="slide" axis="1 0 0"/>
                  <geom name="tool_geom" type="sphere" size="0.02"/>
                </body>
              </worldbody>
            </mujoco>
            """
        ),
        assets={},
        logical_model_path="fixtures/contact_evidence_scene.xml",
        robot_bundle_identity=VersionedIdentity("fast_arm", 1),
        environment_identity=VersionedIdentity("contact_cube_environment", 1),
        viewer_scene_identity="contact-cube/v1",
    )


def _direct_extractor(
    *,
    xml: str | None = None,
    robot_geom_names: tuple[str, ...] = ("tool_geom",),
    object_body_name: str = "object",
    object_geom_name: str = "object_geom",
) -> tuple[ContactEvidenceExtractor, object, object]:
    manifest = _scene_request().manifest
    model = mujoco.MjModel.from_xml_string(xml or _contact_fixture_xml())
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return (
        ContactEvidenceExtractor(
            model=model,
            data=data,
            scene_identity=VersionedIdentity("contact_cube_scene", 1),
            object_identity=VersionedIdentity("contact_cube", 1),
            manifest_digest=contact_manifest_digest(manifest),
            object_body_name=object_body_name,
            object_geom_name=object_geom_name,
            robot_geom_names=robot_geom_names,
            frame_index=3,
            manifest=manifest,
        ),
        model,
        data,
    )


def _target_evidence() -> tuple[ContactEvidenceExtractor, ContactEvidence]:
    extractor, model, data = _direct_extractor()
    object_joint = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "object_freejoint")
    )
    object_qpos = int(model.jnt_qposadr[object_joint])
    data.qpos[object_qpos : object_qpos + 7] = (
        0.0,
        0.0,
        0.05,
        1.0,
        0.0,
        0.0,
        0.0,
    )
    mujoco.mj_forward(model, data)
    evidence = extractor.extract()
    assert evidence.status is ContactEvidenceStatus.MEASURED
    return extractor, evidence


def test_no_contact_is_measured_zero_target_force_without_zeroing_failures() -> None:
    extractor, _, _ = _direct_extractor()
    evidence = extractor.extract()

    assert evidence.status is ContactEvidenceStatus.NO_CONTACT
    assert evidence.aggregate is not None
    assert evidence.aggregate.contact_count == 0
    assert evidence.aggregate.resultant_force_n == 0.0
    assert evidence.as_canonical_evidence().provenance == CONTACT_EVIDENCE_PROVENANCE


def test_target_contact_uses_official_force_api_and_explicit_frames_and_sign() -> None:
    extractor, model, data = _direct_extractor()
    tool_geom = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "tool_geom")
    )
    object_joint = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "object_freejoint")
    )
    data.qpos[0] = 0.0
    object_qpos = int(model.jnt_qposadr[object_joint])
    data.qpos[object_qpos : object_qpos + 7] = (0.0, 0.0, 0.05, 1.0, 0.0, 0.0, 0.0)
    mujoco.mj_forward(model, data)

    evidence = extractor.extract()

    assert evidence.status is ContactEvidenceStatus.MEASURED
    assert evidence.simulation_time_s == 0.0
    assert evidence.frame_index == 3
    target = evidence.target_contacts
    assert target
    record = target[0]
    assert record.classification is ContactPairClassification.TARGET_OBJECT
    assert tool_geom in {record.geom1_id, record.geom2_id}
    assert record.point_world_m == pytest.approx((0.0, 0.0, 0.08), abs=0.03)
    assert record.distance_m < 0.0
    assert record.penetration_m == pytest.approx(-record.distance_m)
    assert record.force_status is ContactEvidenceStatus.MEASURED
    assert record.force_contact_frame_n is not None
    assert record.force_world_n is not None
    assert record.object_on_tool_force_world_n is not None
    assert record.tool_on_object_force_world_n is not None
    assert record.object_on_tool_force_world_n == pytest.approx(
        tuple(-value for value in record.tool_on_object_force_world_n)
    )
    assert record.normal_force_n is not None and record.normal_force_n > 0.0
    assert evidence.aggregate is not None
    assert evidence.aggregate.contact_count == len(target)
    assert evidence.aggregate.normal_force_n > 0.0
    assert evidence.aggregate.resultant_force_n > 0.0


def test_contact_record_rejects_malformed_frame_and_normal_relationship() -> None:
    _, evidence = _target_evidence()
    record = evidence.target_contacts[0]
    with pytest.raises(ValueError, match="orthogonal|orthonormal"):
        replace(
            record,
            contact_frame_world=(
                1.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
            ),
        )
    with pytest.raises(ValueError, match="normal row"):
        replace(record, normal_world=tuple(-value for value in record.normal_world))


def test_contact_record_rejects_force_transform_or_object_tool_sign_mismatch() -> None:
    _, evidence = _target_evidence()
    record = evidence.target_contacts[0]
    assert record.force_world_n is not None
    with pytest.raises(ValueError, match="force transform"):
        replace(
            record,
            force_world_n=(record.force_world_n[0] + 1.0, *record.force_world_n[1:]),
        )
    assert record.object_on_tool_force_world_n is not None
    with pytest.raises(ValueError, match="oppose"):
        replace(
            record,
            tool_on_object_force_world_n=record.object_on_tool_force_world_n,
        )


def test_contact_evidence_rejects_aggregate_count_or_value_mismatch() -> None:
    _, evidence = _target_evidence()
    assert evidence.aggregate is not None
    with pytest.raises(ValueError, match="aggregate"):
        ContactEvidence(
            status=evidence.status,
            scene_identity=evidence.scene_identity,
            object_identity=evidence.object_identity,
            manifest_digest=evidence.manifest_digest,
            sample_time_s=evidence.sample_time_s,
            simulation_time_s=evidence.simulation_time_s,
            contacts=evidence.contacts,
            aggregate=replace(
                evidence.aggregate,
                contact_count=evidence.aggregate.contact_count + 1,
            ),
            frame_index=evidence.frame_index,
        )
    with pytest.raises(ValueError, match="aggregate"):
        ContactEvidence(
            status=evidence.status,
            scene_identity=evidence.scene_identity,
            object_identity=evidence.object_identity,
            manifest_digest=evidence.manifest_digest,
            sample_time_s=evidence.sample_time_s,
            simulation_time_s=evidence.simulation_time_s,
            contacts=evidence.contacts,
            aggregate=replace(
                evidence.aggregate,
                resultant_force_n=evidence.aggregate.resultant_force_n + 1.0,
            ),
            frame_index=evidence.frame_index,
        )


def test_extractor_requires_manifest_canonical_digest_boundary() -> None:
    extractor, model, data = _direct_extractor()
    with pytest.raises(ContactEvidenceError, match="canonical manifest"):
        ContactEvidenceExtractor(
            model=model,
            data=data,
            scene_identity=extractor.scene_identity,
            object_identity=extractor.object_identity,
            manifest_digest="sha256:" + "1" * 64,
            object_body_name=extractor.object_body_name,
            object_geom_name=extractor.object_geom_name,
            robot_geom_names=("tool_geom",),
            manifest=extractor.manifest,
        )
    no_manifest = ContactEvidenceExtractor(
        model=model,
        data=data,
        scene_identity=extractor.scene_identity,
        object_identity=extractor.object_identity,
        manifest_digest=extractor.manifest_digest,
        object_body_name=extractor.object_body_name,
        object_geom_name=extractor.object_geom_name,
        robot_geom_names=("tool_geom",),
    ).extract()
    assert no_manifest.status is ContactEvidenceStatus.INVALID_CONTACT
    assert no_manifest.reason is not None and "manifest" in no_manifest.reason


def test_contact_order_and_canonical_artifact_are_deterministic() -> None:
    first_extractor, model, data = _direct_extractor()
    object_joint = int(
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "object_freejoint")
    )
    object_qpos = int(model.jnt_qposadr[object_joint])
    data.qpos[object_qpos : object_qpos + 7] = (0.0, 0.0, 0.05, 1.0, 0.0, 0.0, 0.0)
    mujoco.mj_forward(model, data)
    first = first_extractor.extract()
    second = first_extractor.extract()

    assert first.canonical_bytes() == second.canonical_bytes()
    assert tuple(item.contact_identity for item in first.contacts) == tuple(
        item.contact_identity for item in second.contacts
    )
    assert first.contacts == tuple(
        sorted(
            first.contacts,
            key=lambda item: (
                item.classification.value,
                item.geom1_name,
                item.geom2_name,
                item.distance_m,
                item.point_world_m,
                item.normal_world,
                item.contact_identity,
            ),
        )
    )


def test_object_environment_and_self_contact_boundaries_are_not_target_contact() -> None:
    environment_extractor, _, _ = _direct_extractor(
        xml="""
        <mujoco>
          <worldbody>
            <geom name="floor" type="plane" size="2 2 0.1"/>
            <body name="object" pos="0 0 0.02">
              <freejoint/>
              <geom name="object_geom" type="box" size="0.03 0.03 0.03" mass="1"/>
            </body>
          </worldbody>
        </mujoco>
        """,
        robot_geom_names=(),
    )
    environment = environment_extractor.extract()
    assert environment.status is ContactEvidenceStatus.NO_CONTACT
    assert any(
        item.classification is ContactPairClassification.ENVIRONMENT_CONTACT
        for item in environment.contacts
    )

    self_extractor, _, _ = _direct_extractor(
        xml="""
            <mujoco>
              <worldbody>
                <body name="robot_a" pos="0 0 0.05">
                  <freejoint/>
                  <geom name="robot_a_geom" type="sphere" size="0.04"/>
                </body>
            <body name="robot_b" pos="0 0 0.05">
              <geom name="robot_b_geom" type="sphere" size="0.04"/>
            </body>
            <body name="object" pos="1 0 0.05">
              <freejoint/>
              <geom name="object_geom" type="box" size="0.03 0.03 0.03" mass="1"/>
            </body>
          </worldbody>
        </mujoco>
        """,
        robot_geom_names=("robot_a_geom", "robot_b_geom"),
    )
    self_contact = self_extractor.extract()
    assert self_contact.status is ContactEvidenceStatus.NO_CONTACT
    assert any(
        item.classification is ContactPairClassification.SELF_CONTACT
        for item in self_contact.contacts
    )


def test_missing_model_or_model_identity_is_explicit_failure() -> None:
    extractor, model, data = _direct_extractor()
    unavailable = ContactEvidenceExtractor(
        model=None,
        data=None,
        scene_identity=extractor.scene_identity,
        object_identity=extractor.object_identity,
        manifest_digest=extractor.manifest_digest,
        object_body_name=extractor.object_body_name,
        object_geom_name=extractor.object_geom_name,
        manifest=extractor.manifest,
    ).extract()
    assert unavailable.status is ContactEvidenceStatus.MEASUREMENT_UNAVAILABLE
    assert unavailable.aggregate is None
    assert unavailable.as_canonical_evidence().status.value == "unavailable"

    mismatch = ContactEvidenceExtractor(
        model=model,
        data=data,
        scene_identity=extractor.scene_identity,
        object_identity=extractor.object_identity,
        manifest_digest=extractor.manifest_digest,
        object_body_name="wrong_object",
        object_geom_name=extractor.object_geom_name,
        manifest=extractor.manifest,
    ).extract()
    assert mismatch.status is ContactEvidenceStatus.INVALID_CONTACT
    assert mismatch.aggregate is None
    assert mismatch.as_canonical_evidence().status.value == "invalid"


def test_model_and_data_mismatch_is_invalid_not_a_zero_measurement() -> None:
    extractor, model, _ = _direct_extractor()
    other_model = mujoco.MjModel.from_xml_string(_contact_fixture_xml())
    other_data = mujoco.MjData(other_model)
    mismatch = ContactEvidenceExtractor(
        model=model,
        data=other_data,
        scene_identity=extractor.scene_identity,
        object_identity=extractor.object_identity,
        manifest_digest=extractor.manifest_digest,
        object_body_name=extractor.object_body_name,
        object_geom_name=extractor.object_geom_name,
        robot_geom_names=("tool_geom",),
        manifest=extractor.manifest,
    ).extract()

    assert mismatch.status is ContactEvidenceStatus.INVALID_CONTACT
    assert mismatch.aggregate is None
    assert mismatch.reason is not None and "model/data" in mismatch.reason


def test_solver_failure_is_preserved_even_without_target_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extractor, _, _ = _direct_extractor(
        xml="""
        <mujoco>
          <worldbody>
            <geom name="floor" type="plane" size="2 2 0.1"/>
            <body name="object" pos="0 0 0.02">
              <freejoint/>
              <geom name="object_geom" type="box" size="0.03 0.03 0.03" mass="1"/>
            </body>
          </worldbody>
        </mujoco>
        """,
        robot_geom_names=(),
    )

    def invalid_force(*args: object) -> object:
        raise RuntimeError("fixture solver failure")

    monkeypatch.setattr(mujoco, "mj_contactForce", invalid_force)
    evidence = extractor.extract()

    assert evidence.status is ContactEvidenceStatus.SOLVER_INVALID
    assert evidence.aggregate is None


def test_scene_instance_exposes_backend_contact_measurement_without_viewer() -> None:
    instance = ContactSceneComposer(_scene_request()).build()
    evidence = extract_contact_evidence_from_scene_instance(
        instance,
        robot_geom_names=("tool_geom",),
    )
    assert evidence.status is ContactEvidenceStatus.NO_CONTACT
    assert evidence.simulation_time_s == 0.0

    # The scene has a deterministic manifest digest and the runtime method is
    # a thin backend facade, not a second contact implementation.
    assert evidence.manifest_digest == instance.definition.manifest_digest


def test_invalid_explicit_robot_geom_identity_is_fail_closed() -> None:
    extractor, _, _ = _direct_extractor(robot_geom_names=("missing_robot_geom",))
    evidence = extractor.extract()
    assert evidence.status is ContactEvidenceStatus.INVALID_CONTACT
    assert evidence.aggregate is None


def test_missing_robot_geom_identity_is_not_inferred_from_model_geometry() -> None:
    extractor, _, _ = _direct_extractor(robot_geom_names=None)
    evidence = extractor.extract()

    assert evidence.status is ContactEvidenceStatus.INVALID_CONTACT
    assert evidence.aggregate is None
    assert evidence.reason is not None and "identity" in evidence.reason
