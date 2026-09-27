from __future__ import annotations

from dataclasses import replace

import pytest

from xpotato_sim.plugins.environments.contact_cube_environment import (
    CONTACT_CUBE_ENVIRONMENT_PLUGIN,
)
from xpotato_sim.runtime.contact.manifest import (
    ContactCubeObject,
    ContactManifestError,
    ContactResetState,
    ContactSceneContract,
    ContactTarget,
    ContactTaskManifest,
    ContactMaterial,
    MuJoCoSettingsIdentity,
    ScenePresentationIdentity,
)
from xpotato_sim.runtime.contact.scene import (
    ContactSceneBuildRequest,
    ContactSceneComposer,
    ContactSceneError,
    validate_contact_scene_compatibility,
)
from xpotato_sim.runtime.experiment.contracts import (
    PluginSelection,
    SemanticRole,
    SemanticRoleRequirement,
    VersionedIdentity,
)


MODEL_XML = b"""
<mujoco model="contact_fixture">
  <option gravity="0 0 -9.81"/>
  <worldbody>
    <geom name="floor" type="plane" pos="0 0 0" size="1 1 0.1"/>
    <body name="tool" pos="0 0 0.3">
      <joint name="tool_slide" type="slide" axis="1 0 0"/>
      <geom name="tool_geom" type="sphere" size="0.02"/>
    </body>
  </worldbody>
  <actuator>
    <general name="tool_motor" joint="tool_slide" gear="1" dyntype="integrator"/>
  </actuator>
</mujoco>
"""


def _manifest(
    *, object_position_m: tuple[float, float, float] = (0.3, 0.0, 0.2)
) -> ContactTaskManifest:
    object_value = ContactCubeObject(
        identity=VersionedIdentity("contact_cube", 1),
        position_m=object_position_m,
        size_m=(0.03, 0.03, 0.03),
        mass_kg=0.2,
        material=ContactMaterial("contact-red", (0.8, 0.1, 0.1, 1.0)),
        friction=(0.7, 0.01, 0.001),
    )
    reset = ContactResetState(
        qpos_rad=(0.0,),
        qvel_rad_s=(0.0,),
        ctrl=(0.0,),
        act=(0.25,),
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
    return ContactTaskManifest(
        robot_bundle=PluginSelection("fast_arm", 1),
        environment=PluginSelection("contact_cube_environment", 1),
        task=PluginSelection("contact_press_hold_task", 1),
        evaluators=(PluginSelection("contact_outcome", 1),),
        scene=scene,
        software_revision_identity="test-revision:contact-scene",
    )


def _request(manifest: ContactTaskManifest | None = None) -> ContactSceneBuildRequest:
    return ContactSceneBuildRequest(
        manifest=_manifest() if manifest is None else manifest,
        model_xml=MODEL_XML,
        assets={},
        logical_model_path="fixtures/contact_scene.xml",
        robot_bundle_identity=VersionedIdentity("fast_arm", 1),
        environment_identity=VersionedIdentity("contact_cube_environment", 1),
        viewer_scene_identity="contact-cube/v1",
    )


def test_composer_adds_a_real_mujoco_object_and_is_deterministic() -> None:
    first = ContactSceneComposer(_request()).compose()
    second = ContactSceneComposer(_request()).compose()

    assert first.model_xml == second.model_xml
    assert first.manifest_digest == second.manifest_digest
    simulator = first.build_simulator()
    import mujoco

    body_id = mujoco.mj_name2id(simulator.model, mujoco.mjtObj.mjOBJ_BODY, "contact_cube")
    geom_id = mujoco.mj_name2id(simulator.model, mujoco.mjtObj.mjOBJ_GEOM, "contact_cube_geom")
    assert body_id >= 0
    assert geom_id >= 0
    assert int(simulator.model.geom_bodyid[geom_id]) == body_id
    assert tuple(simulator.model.geom_size[geom_id]) == pytest.approx((0.03, 0.03, 0.03))
    assert float(simulator.model.body_mass[body_id]) == pytest.approx(0.2)
    assert tuple(simulator.model.geom_friction[geom_id]) == pytest.approx((0.7, 0.01, 0.001))
    assert int(simulator.model.geom_contype[geom_id]) == 1
    assert int(simulator.model.geom_conaffinity[geom_id]) == 1
    assert int(simulator.model.geom_condim[geom_id]) == 3
    assert simulator.model.opt.integrator == mujoco.mjtIntegrator.mjINT_EULER
    assert simulator.model.opt.solver == mujoco.mjtSolver.mjSOL_NEWTON
    assert simulator.data.ncon == 0


def test_reset_reapplies_all_declared_state_without_trial_leakage() -> None:
    scene = ContactSceneComposer(_request()).compose()
    simulator = scene.build_simulator()

    simulator.data.qpos[0] = 0.4
    simulator.data.qvel[0] = 2.0
    simulator.data.ctrl[0] = 0.8
    simulator.data.act[0] = 4.0
    simulator.data.qacc_warmstart[:] = 4.0
    simulator.data.time = 3.0
    simulator.step(0.01)
    assert simulator.data.time > 3.0

    scene.reset(simulator)

    assert tuple(simulator.data.qpos[:1]) == pytest.approx((0.0,))
    assert tuple(simulator.data.qvel[:1]) == pytest.approx((0.0,))
    assert tuple(simulator.data.ctrl) == pytest.approx((0.0,))
    assert tuple(simulator.data.act) == pytest.approx((0.25,))
    assert tuple(simulator.data.qacc_warmstart) == pytest.approx((0.0,) * simulator.model.nv)
    assert simulator.data.time == 0.0
    assert simulator.data.ncon == 0
    import mujoco

    object_joint = mujoco.mj_name2id(
        simulator.model,
        mujoco.mjtObj.mjOBJ_JOINT,
        "contact_cube_freejoint",
    )
    object_qpos = int(simulator.model.jnt_qposadr[object_joint])
    assert tuple(simulator.data.qpos[object_qpos : object_qpos + 7]) == pytest.approx(
        (0.3, 0.0, 0.2, 1.0, 0.0, 0.0, 0.0)
    )


def test_initial_object_contact_or_penetration_is_fail_closed() -> None:
    manifest = _manifest(object_position_m=(0.3, 0.0, 0.02))
    with pytest.raises(ContactSceneError, match="initial object"):
        ContactSceneComposer(_request(manifest)).build()


def test_manifest_contact_parameters_are_bound_to_the_mujoco_geom() -> None:
    manifest = _manifest()
    object_value = replace(
        manifest.object,
        contype=2,
        conaffinity=4,
        condim=6,
    )
    manifest = replace(manifest, scene=replace(manifest.scene, object=object_value))
    scene = ContactSceneComposer(_request(manifest)).compose()
    simulator = scene.build_simulator()
    import mujoco

    geom_id = mujoco.mj_name2id(
        simulator.model,
        mujoco.mjtObj.mjOBJ_GEOM,
        object_value.geom_name,
    )
    assert int(simulator.model.geom_contype[geom_id]) == 2
    assert int(simulator.model.geom_conaffinity[geom_id]) == 4
    assert int(simulator.model.geom_condim[geom_id]) == 6


def test_initial_penetration_tolerance_must_match_manifest_identity() -> None:
    manifest = _manifest()
    assert (
        ContactSceneBuildRequest(
            manifest=manifest,
            model_xml=MODEL_XML,
            assets={},
            logical_model_path="fixtures/contact_scene.xml",
            robot_bundle_identity=VersionedIdentity("fast_arm", 1),
            environment_identity=VersionedIdentity("contact_cube_environment", 1),
            viewer_scene_identity="contact-cube/v1",
        ).initial_penetration_tolerance_m
        == manifest.scene.initial_penetration_tolerance_m
    )
    with pytest.raises(ContactSceneError, match="match manifest"):
        ContactSceneBuildRequest(
            manifest=manifest,
            model_xml=MODEL_XML,
            assets={},
            logical_model_path="fixtures/contact_scene.xml",
            robot_bundle_identity=VersionedIdentity("fast_arm", 1),
            environment_identity=VersionedIdentity("contact_cube_environment", 1),
            viewer_scene_identity="contact-cube/v1",
            initial_penetration_tolerance_m=1e-6,
        )


def test_unknown_object_or_scene_identity_is_rejected() -> None:
    manifest = _manifest()
    unknown_object = replace(
        manifest.object,
        identity=VersionedIdentity("unknown_object", 1),
    )
    unknown_object_manifest = replace(
        manifest,
        scene=replace(manifest.scene, object=unknown_object),
    )
    with pytest.raises(ContactSceneError, match="object identity"):
        ContactSceneComposer(_request(unknown_object_manifest)).compose()

    unknown_scene_manifest = replace(
        manifest,
        scene=replace(
            manifest.scene,
            identity=VersionedIdentity("unknown_scene", 1),
        ),
    )
    with pytest.raises(ContactSceneError, match="scene identity"):
        ContactSceneComposer(_request(unknown_scene_manifest)).compose()


def test_disabled_scene_does_not_add_a_cube_or_contact_state() -> None:
    manifest = _manifest()
    disabled_object = replace(manifest.object, enabled=False)
    disabled_scene = replace(
        manifest.scene,
        object=disabled_object,
        enabled=False,
    )
    disabled_manifest = replace(manifest, scene=disabled_scene)
    scene = ContactSceneComposer(_request(disabled_manifest)).compose()
    simulator = scene.build_simulator()
    import mujoco

    assert mujoco.mj_name2id(
        simulator.model,
        mujoco.mjtObj.mjOBJ_GEOM,
        "contact_cube_geom",
    ) < 0
    assert simulator.data.ncon == 0


def test_identity_and_reset_dimension_mismatch_are_rejected() -> None:
    with pytest.raises(ContactSceneError, match="environment identity mismatch"):
        ContactSceneBuildRequest(
            manifest=_manifest(),
            model_xml=MODEL_XML,
            assets={},
            logical_model_path="fixtures/contact_scene.xml",
            robot_bundle_identity=VersionedIdentity("fast_arm", 1),
            environment_identity=VersionedIdentity("wrong_environment", 1),
            viewer_scene_identity="contact-cube/v1",
        )
    bad_reset = replace(_manifest().scene.reset, qpos_rad=(0.0, 0.0), qvel_rad_s=(0.0, 0.0))
    bad_manifest = replace(_manifest(), scene=replace(_manifest().scene, reset=bad_reset))
    with pytest.raises(ContactSceneError, match="qpos_rad dimension"):
        ContactSceneComposer(_request(bad_manifest)).build()


def test_provider_owns_load_and_reset_lifecycle() -> None:
    provider = CONTACT_CUBE_ENVIRONMENT_PLUGIN.scene_provider
    instance = provider.compose_scene({"request": _request()})
    assert instance.definition.enabled
    provider.reset_scene(instance)
    with pytest.raises(TypeError, match="ContactSceneInstance"):
        provider.reset_scene(object())


def test_robot_bundle_resource_is_a_supported_scene_composition_source() -> None:
    from xpotato_sim.plugins.robots.fast_arm.adapter.bundle import FAST_ARM_ROBOT_BUNDLE

    manifest = _manifest()
    manifest = replace(
        manifest,
        scene=replace(
            manifest.scene,
            reset=replace(
                manifest.reset,
                qpos_rad=(0.0, -0.5235987755982989, 0.0, -1.0471975511965976),
                qvel_rad_s=(0.0, 0.0, 0.0, 0.0),
                ctrl=(0.0, 0.0, 0.0, 0.0),
                act=(),
            ),
        ),
    )
    instance = ContactSceneComposer.from_robot_bundle(
        manifest,
        FAST_ARM_ROBOT_BUNDLE,
    ).build()

    assert instance.definition.logical_model_path == "assets/mujoco/fast_arm/scene.xml"
    assert instance.data.time == 0.0
    assert instance.data.ncon == 0


def test_compatibility_can_require_roles_and_capabilities() -> None:
    manifest = _manifest()
    manifest = replace(
        manifest,
        scene=replace(
            manifest.scene,
            required_capabilities=frozenset({VersionedIdentity("contact_evidence", 1)}),
        ),
    )
    with pytest.raises(ContactSceneError, match="capability"):
        validate_contact_scene_compatibility(
            manifest,
            robot_capabilities=(VersionedIdentity("other", 1),),
        )
