"""固定bindingの準備と、live観測・consumer隔離を実MuJoCoで検証する。"""
from dataclasses import replace
import json

import mujoco
import pytest

from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.execution.model_execution import ModelExecution
from xpotato_sim.schemas.coordinated import EndpointVelocity


def test_viewer_declaration_is_prepared_and_replacement_gets_new_identity(monkeypatch):
    from xpotato_sim.plugins.robots.fast_arm.adapter import assembly_viewer
    from xpotato_sim.runtime.composition.viewer_robot_declaration import viewer_robot_declaration_digest
    bundle = load_launch_profile("dynamic-cube-drop").build_model().viewer
    expected = viewer_robot_declaration_digest(bundle.declaration)
    calls = []
    original = assembly_viewer.viewer_robot_declaration_digest
    def counted(declaration):
        calls.append(declaration)
        return original(declaration)
    monkeypatch.setattr(assembly_viewer, "viewer_robot_declaration_digest", counted)
    for _ in range(3):
        assert bundle.metadata["viewer_robot_declaration_digest"] == expected
        assert bundle.declaration_digest == expected
    assert not calls
    changed = replace(bundle, declaration=replace(bundle.declaration, profile_id="changed"))
    assert changed.declaration_digest == viewer_robot_declaration_digest(changed.declaration)
    assert changed.declaration_digest != expected
    metadata = bundle.metadata
    metadata["robot_joint_names"][0] = "consumer"
    metadata["scene_state_layout_v1"]["joints"][0]["name"] = "consumer"
    assert bundle.metadata["robot_joint_names"][0] != "consumer"
    assert bundle.metadata["scene_state_layout_v1"]["joints"][0]["name"] != "consumer"


def test_sampling_uses_prepared_names_and_reads_current_native_values(monkeypatch):
    p = load_launch_profile("dynamic-cube-drop")
    provider = p.build_model().provider
    calls = []
    original = mujoco.mj_id2name
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(mujoco, "mj_id2name", counted)
    initial = provider.sample(frame_index=0, metadata={})
    commands = tuple(EndpointVelocity(a, (0., 0., 0.), "world") for a in provider.endpoint_ids)
    provider.commit(provider.prepare(commands, p.dt_s))
    current = provider.sample(frame_index=1, metadata={})
    assert not calls
    assert current.state.qpos != initial.state.qpos
    assert current.state.qvel == tuple(provider._data.qvel)
    assert current.state.time_s == provider._data.time
    assert current.robot.joint_positions_rad == tuple(current.state.qpos[i] for i in current.robot_qpos_addresses)
    assert current.dynamics["simulation_time_s"] == current.geometry.simulation_time_s == current.state.time_s
    provider.model.opt.gravity[:] = (0., 0., -1.)
    assert provider.sample(frame_index=2, metadata={}).dynamics["gravity_m_s2"] == [0., 0., -1.]
    provider.reset()
    reset = provider.sample(frame_index=0, metadata={})
    assert reset.robot.generation > current.robot.generation
    assert reset.state.qpos == initial.state.qpos and reset.state.time_s == 0


def test_prepared_layout_rejects_replacement_model():
    from xpotato_sim.mujoco_backend.snapshot import _read_synchronized_mujoco_state
    provider = load_launch_profile("dynamic-cube-drop").build_model().provider
    model = mujoco.MjModel.from_xml_string(provider.built.xml.decode(), dict(provider.built.assets))
    with pytest.raises(ValueError, match="layout/model mismatch"):
        _read_synchronized_mujoco_state(model, mujoco.MjData(model), frame_index=0,
            layout=provider._snapshot_layout)


def test_geometry_projection_does_not_rewalk_immutable_dataclasses(monkeypatch):
    from xpotato_sim.runtime.scene import observation
    sample = load_launch_profile("dynamic-cube-drop").build_model().provider.sample(frame_index=0, metadata={})
    expected = sample.geometry.to_document()
    def forbidden(*args):
        pytest.fail("immutable geometry must have a direct document projection")
    monkeypatch.setattr(observation, "asdict", forbidden, raising=False)
    assert sample.geometry.to_document() == expected


def test_sample_metadata_is_detached_from_input_task_and_other_samples():
    execution = ModelExecution(load_launch_profile("dynamic-cube-drop"), clock=lambda: 10.)
    from xpotato_sim.schemas import RawInputFrame
    input_metadata = {"nested": {"values": [1, 2]}}
    execution.runtime.last_frame = RawInputFrame("test", 0., metadata=input_metadata)
    first = execution.sample(0, advance_task=True)
    expected = json.dumps(first.metadata, sort_keys=True)
    first.metadata["nested"]["values"].append(3)
    first.metadata["scene_contact_binding_v1"]["object_ids"].clear()
    first.metadata["scene_dynamics_v1"]["objects"][0]["position_m"][0] = 999.
    assert input_metadata == {"nested": {"values": [1, 2]}}
    assert execution.scene_binding["object_ids"]
    second = execution.sample(0)
    assert json.dumps(second.metadata, sort_keys=True) == expected


def test_provider_metadata_copy_detaches_nested_consumer_values():
    from types import MappingProxyType
    provider = load_launch_profile("dynamic-cube-drop").build_model().provider
    metadata = {"nested": MappingProxyType({"values": [1]})}
    first = provider.sample(frame_index=0, metadata=metadata)
    first.state.metadata["nested"]["values"].append(2)
    assert metadata == {"nested": {"values": [1]}}
    assert provider.sample(frame_index=1, metadata=metadata).state.metadata == metadata


def test_trigger_presentation_copy_preserves_wire_shape_without_json_roundtrip(monkeypatch):
    from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping import implementation
    strategy = implementation.ViewerKeyboardGamepadMappingStrategy(session=True)
    parameters = load_launch_profile("dynamic-cube-drop").mapping_parameters
    strategy.reset_coordinated_presentation(parameters, reason="awaiting_gamepad_input")
    expected = json.loads(json.dumps(strategy.latest_trigger_presentation, allow_nan=False))
    def forbidden(*args, **kwargs):
        pytest.fail("prepared trigger presentation must not encode/decode itself")
    monkeypatch.setattr(implementation.json, "dumps", forbidden)
    monkeypatch.setattr(implementation.json, "loads", forbidden)
    actual = strategy.latest_trigger_presentation
    assert actual == expected
    actual.clear()
    assert strategy.latest_trigger_presentation == expected


def test_dynamics_plan_follows_replaced_settings_and_isolates_roles():
    provider = load_launch_profile("dynamic-cube-drop").build_model().provider
    old = provider._dynamics_observation(0)
    provider.settings = replace(provider.settings, max_joint_speed_rad_s=1.)
    new = provider._dynamics_observation(1)
    assert new["settings_digest"] == provider.settings.digest != old["settings_digest"]
    assert new["frame_index"] == 1
    p = load_launch_profile("dynamic-cube-drop")
    commands = tuple(EndpointVelocity(a, (0., 0., 0.), "world") for a in provider.endpoint_ids)
    for _ in range(60):
        provider.commit(provider.prepare(commands, p.dt_s))
    first = provider._dynamics_observation(2)
    assert first["contacts"]
    first["contacts"][0]["role1"]["kind"] = "consumer"
    assert provider._dynamics_observation(3)["contacts"][0]["role1"]["kind"] != "consumer"


@pytest.mark.parametrize("field", ["qpos", "qvel", "ctrl", "actuator_force", "xpos", "xquat", "cvel"])
def test_dynamic_observation_keeps_all_emitted_numeric_values_finite(field):
    provider = load_launch_profile("dynamic-cube-drop").build_model().provider
    values = getattr(provider._data, field)
    if field in {"xpos", "xquat", "cvel"}:
        values[provider._observation_plan.objects[0][2], 0] = float("nan")
    else:
        values[0] = float("nan")
    with pytest.raises(ValueError, match="nonfinite"):
        provider._dynamics_observation(0)
