from __future__ import annotations

import json
from dataclasses import replace

import pytest

from xpotato_sim.runtime.contact.manifest import (
    CONTACT_ENVIRONMENT_ROLE,
    CONTACT_MANIFEST_SCHEMA_VERSION,
    CONTACT_TASK_IDENTITY,
    ContactCubeObject,
    ContactManifestDecodeError,
    ContactManifestError,
    ContactMaterial,
    ContactResetState,
    ContactSceneContract,
    ContactTarget,
    ContactTaskManifest,
    MuJoCoSettingsIdentity,
    ScenePresentationIdentity,
    canonical_decode,
    canonical_encode,
    contact_manifest_digest,
    decode_contact_manifest,
    encode_contact_manifest,
)
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    ROLE_ATTRIBUTE_WILDCARD,
    SemanticRole,
    SemanticRoleRequirement,
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
    reset = ContactResetState(
        qpos_rad=(0.0, 0.0, 0.0, 0.0),
        qvel_rad_s=(0.0, 0.0, 0.0, 0.0),
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
            timestep_s=1.0 / 500.0,
            integrator="implicitfast",
            solver="Newton",
            iterations=50,
        ),
        required_capabilities=frozenset(
            {VersionedIdentity("contact_evidence", 1)}
        ),
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
        software_revision_identity="test-revision:contact-manifest",
    )


def test_valid_manifest_has_expected_identity_and_round_trips() -> None:
    manifest = _manifest()

    encoded = encode_contact_manifest(manifest)
    decoded = decode_contact_manifest(encoded)

    assert manifest.schema_version == CONTACT_MANIFEST_SCHEMA_VERSION
    assert manifest.task_identity == CONTACT_TASK_IDENTITY
    assert decoded == manifest
    assert canonical_encode(manifest) == encoded
    assert canonical_decode(encoded) == manifest
    assert contact_manifest_digest(manifest).startswith("sha256:")


def test_canonical_bytes_are_independent_of_mapping_insertion_order() -> None:
    manifest = _manifest()
    document = manifest.to_document()
    reordered = {key: document[key] for key in reversed(tuple(document))}

    assert canonical_encode(manifest) == canonical_encode(canonical_decode(reordered))


def test_manifest_rejects_invalid_object_values_without_defaulting() -> None:
    with pytest.raises(ContactManifestError, match="positive"):
        replace(_manifest().object, mass_kg=0.0)
    with pytest.raises(ContactManifestError, match="unit quaternion"):
        replace(_manifest().object, orientation_wxyz=(1.0, 1.0, 0.0, 0.0))
    with pytest.raises(ContactManifestError, match="friction"):
        replace(_manifest().object, friction=(-0.1, 0.0, 0.0))
    with pytest.raises(ContactManifestError, match="penetration band"):
        replace(
            _manifest().scene.target,
            penetration_band_m=(0.2, 0.1),
        )
    with pytest.raises(ContactManifestError, match="contype"):
        replace(_manifest().object, contype=-1)
    with pytest.raises(ContactManifestError, match="conaffinity"):
        replace(_manifest().object, conaffinity=True)
    with pytest.raises(ContactManifestError, match="condim"):
        replace(_manifest().object, condim=True)
    with pytest.raises(ContactManifestError, match="initial_penetration"):
        replace(_manifest().scene, initial_penetration_tolerance_m=-1.0)


def test_manifest_rejects_scene_enabled_mismatch_and_nonzero_reset_time() -> None:
    manifest = _manifest()
    with pytest.raises(ContactManifestError, match="enabled"):
        replace(manifest.scene, enabled=False)
    with pytest.raises(ContactManifestError, match="position"):
        replace(
            manifest.scene,
            reset=replace(manifest.reset, object_position_m=(0.0, 0.0, 0.0)),
        )
    with pytest.raises(ContactManifestError, match="exactly zero"):
        replace(manifest.reset, simulation_time_s=0.1)


def test_decoder_rejects_unknown_missing_duplicate_and_nonfinite_fields() -> None:
    document = _manifest().to_document()
    unknown = dict(document)
    unknown["unexpected"] = True
    with pytest.raises(ContactManifestDecodeError, match="unknown fields"):
        decode_contact_manifest(unknown)

    missing = dict(document)
    del missing["scene"]
    with pytest.raises(ContactManifestDecodeError, match="missing fields"):
        decode_contact_manifest(missing)

    raw = canonical_encode(_manifest()).decode("utf-8")
    duplicate = raw[:-1] + ',"task":' + json.dumps(document["task"]) + "}"
    with pytest.raises(ContactManifestDecodeError, match="duplicate field"):
        decode_contact_manifest(duplicate)

    nonfinite = json.loads(raw)
    nonfinite["scene"]["object"]["mass_kg"] = float("nan")
    with pytest.raises(ContactManifestDecodeError, match="non-finite|finite"):
        decode_contact_manifest(json.dumps(nonfinite))


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("scene", "identity", "name"), 123),
        (("scene", "object", "identity", "name"), 123),
        (("robot_bundle", "plugin_id"), 123),
        (("scene", "required_robot_roles", 0, "role"), 123),
        (("software_revision_identity",), 123),
    ),
)
def test_decoder_rejects_non_string_identity_fields_without_coercion(
    path: tuple[object, ...], value: object
) -> None:
    document = _manifest().to_document()
    current: object = document
    for key in path[:-1]:
        current = current[key]  # type: ignore[index]
    current[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ContactManifestDecodeError, match="string|invalid"):
        decode_contact_manifest(document)


def test_contact_parameters_and_initial_tolerance_are_canonical_identity() -> None:
    manifest = _manifest()
    document = manifest.to_document()
    assert document["scene"]["object"]["contype"] == 1  # type: ignore[index]
    assert document["scene"]["object"]["conaffinity"] == 1  # type: ignore[index]
    assert document["scene"]["object"]["condim"] == 3  # type: ignore[index]
    assert document["scene"]["initial_penetration_tolerance_m"] == pytest.approx(1e-12)  # type: ignore[index]

    changed_object = replace(
        manifest.object,
        contype=2,
        conaffinity=4,
        condim=6,
    )
    changed_scene = replace(
        manifest.scene,
        object=changed_object,
        initial_penetration_tolerance_m=1e-6,
    )
    changed = replace(manifest, scene=changed_scene)

    assert contact_manifest_digest(changed) != contact_manifest_digest(manifest)
    assert decode_contact_manifest(encode_contact_manifest(changed)) == changed


def test_contact_role_requirement_is_typed_and_does_not_accept_wrong_value() -> None:
    requirement = SemanticRoleRequirement(
        role=CONTACT_ENVIRONMENT_ROLE,
        object_kind=ROLE_ATTRIBUTE_WILDCARD,
        frame="mujoco_world",
        unit="meter",
    )
    assert requirement.role == CONTACT_ENVIRONMENT_ROLE
