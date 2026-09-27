"""左右独立のmode、入力欠落、セッション隔離を実デバイスなしで検証する。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import pytest

from xpotato_sim.plugins.input_sources.viewer import ViewerInputSource
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping import VIEWER_CONTROL_MAPPING_PLUGIN as PLUGIN
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.gamepad_planes import coerce_plane_control
from xpotato_sim.schemas import ViewerControlMessage, ViewerControlGamepadMessage, ViewerControlGamepadButtonMessage


CONFIG = {"schema": "gamepad-plane-control/v1", "output_side": "left", "neutral_threshold": 0.1,
          "left": {"axes": [0, 1], "signs": [1, -1], "mode_button": 4},
          "right": {"axes": [2, 3], "signs": [1, -1], "mode_button": 5}}


def parameters(side="left"):
    return {"gamepad_plane_control": {**deepcopy(CONFIG), "output_side": side}}


def message(raw=(0., 0., 0., 0.), held=(), sequence=1, *, connected=True, stale=False, identity="test-pad"):
    return ViewerControlMessage(type="viewer_control_message", timestamp_s=sequence / 60,
        sequence=sequence, source_kind="gamepad", metadata={"viewer_provider_session_id":"test-stream"}, gamepad=ViewerControlGamepadMessage(
            connected=connected, id=identity, index=0, raw_axes=raw, axes=raw,
            buttons=tuple(ViewerControlGamepadButtonMessage(pressed=i in held, value=float(i in held)) for i in range(6)),
            stale=stale, zero_state=not any(abs(v) > 0.1 for v in raw) and not held,
        ))


def frame(*args, **kwargs):
    return ViewerInputSource(clock=lambda: 0.0).ingest_control_message(message(*args, **kwargs))


def map_frame(strategy, *args, side="left", **kwargs):
    return strategy.map_input(frame(*args, **kwargs), parameters(side))


def modes(intent):
    return intent.metadata["gamepad_plane_control_v1"]["sides"]


@pytest.mark.parametrize("side", ("left", "right"))
@pytest.mark.parametrize("axis,sign", [(0,1), (0,-1), (1,1), (1,-1), (2,1), (2,-1)])
def test_each_stick_reaches_six_directions_without_opposite_stick(side, axis, sign):
    strategy = PLUGIN.create_session_strategy()
    offset, button = (0,4) if side == "left" else (2,5)
    held = (button,) if axis == 2 else ()
    map_frame(strategy, held=held, sequence=0, side=side)
    raw = [0.] * 4
    raw[offset + int(axis != 0)] = float(sign if axis == 0 else -sign)
    result = map_frame(strategy, tuple(raw), held, 1, side=side)
    expected = [0.] * 3; expected[axis] = sign * .1
    assert result.metadata["local_endpoint_velocity_m_s"] == pytest.approx(expected)
    assert modes(result)[side]["status"] == "armed"
    other = "right" if side == "left" else "left"
    assert modes(result)[other]["velocity_m_s"] == (0., 0., 0.)


def test_start_press_and_release_require_neutral_and_do_not_change_other_side():
    s = PLUGIN.create_session_strategy()
    first = map_frame(s, (1.,-1.,0.,0.), sequence=0)
    assert first.values == (0.,0.,0.)
    map_frame(s, sequence=1)
    a = map_frame(s, (0.,-1.,0.,0.), sequence=2)
    assert a.metadata["local_endpoint_velocity_m_s"] == (0.,.1,0.)
    b = map_frame(s, (0.,-1.,0.,-1.), held=(5,), sequence=3)
    assert modes(b)["right"]["status"] == "waiting_neutral"
    assert modes(b)["right"]["plane"] == "xy"
    assert modes(b)["right"]["requested_plane"] == "xz"
    assert modes(b)["left"]["velocity_m_s"] == (0.,.1,0.)
    map_frame(s, (0.,-1.,0.,0.), held=(5,), sequence=4)
    c = map_frame(s, (0.,-1.,0.,-.55), held=(5,), sequence=5)
    assert modes(c)["right"]["velocity_m_s"] == pytest.approx((0.,0.,.05))
    d = map_frame(s, (0.,-1.,0.,-.55), held=(), sequence=6)
    assert modes(d)["right"]["velocity_m_s"] == (0.,0.,0.)
    assert modes(d)["right"]["status"] == "waiting_neutral"
    assert modes(d)["left"]["velocity_m_s"] == (0.,.1,0.)
    map_frame(s, (0.,-1.,0.,0.), sequence=7)
    e = map_frame(s, (0.,-1.,0.,-.55), sequence=8)
    assert modes(e)["right"]["velocity_m_s"] == pytest.approx((0.,.05,0.))
    assert modes(e)["right"]["switch_count"] == 2


def test_per_side_norm_and_face_buttons_do_not_inject_z():
    s = PLUGIN.create_session_strategy()
    map_frame(s, held=(5,), sequence=0)
    result = map_frame(s, (1.,-1.,1.,-1.), held=(0,1,5), sequence=1)
    v = .1 / 2**.5
    assert modes(result)["left"]["velocity_m_s"] == pytest.approx((v,v,0.))
    assert modes(result)["right"]["velocity_m_s"] == pytest.approx((v,0.,v))
    assert result.metadata["gamepad_plane_control_v1"]["output_scope"] == "single_endpoint"


@pytest.mark.parametrize("kw", [{"connected":False}, {"stale":True}])
def test_unavailable_input_resets_arming_before_recovery(kw):
    s = PLUGIN.create_session_strategy(); map_frame(s, sequence=0)
    assert map_frame(s, (0.,-1.,0.,0.), sequence=1).values[1] == 1.
    stop = map_frame(s, sequence=2, **kw)
    assert stop.values == (0.,0.,0.)
    assert modes(stop)["left"]["status"] == "waiting_neutral"
    assert map_frame(s, (0.,-1.,0.,0.), sequence=3).values == (0.,0.,0.)
    map_frame(s, sequence=4)
    assert map_frame(s, (0.,-1.,0.,0.), sequence=5).values[1] == 1.


def test_timeout_and_device_change_reset_before_nonzero_recovery():
    s = PLUGIN.create_session_strategy(); map_frame(s, sequence=0)
    source = ViewerInputSource(clock=lambda: 0.0)
    source.ingest_control_message(message((0.,-1.,0.,0.), sequence=1))
    source._clock = lambda: 1.0
    timed_out = s.map_input(source.read_frame(), parameters())
    assert timed_out.values == (0.,0.,0.)
    assert map_frame(s, (0.,-1.,0.,0.), sequence=2).values == (0.,0.,0.)
    map_frame(s, sequence=3)
    changed = map_frame(s, (0.,-1.,0.,0.), sequence=4, identity="another-pad")
    assert changed.values == (0.,0.,0.)


def test_sessions_are_isolated_and_singleton_rejects_stateful_use():
    s1,s2 = PLUGIN.create_session_strategy(),PLUGIN.create_session_strategy()
    assert s1 is not s2 and s1 is not PLUGIN.strategy
    map_frame(s1, sequence=0)
    assert map_frame(s1,(1.,0.,0.,0.),sequence=1).values[0] == 1.
    assert map_frame(s2,(1.,0.,0.,0.),sequence=1).values == (0.,0.,0.)
    with pytest.raises(ValueError, match="runtime mapping session"):
        map_frame(PLUGIN.strategy, sequence=0)


def test_duplicate_is_idempotent_and_old_or_reused_sequence_rejects():
    s=PLUGIN.create_session_strategy(); map_frame(s,sequence=0)
    f=frame((0.,-1.,0.,0.),held=(4,),sequence=1)
    a=s.map_input(f,parameters()); b=s.map_input(f,parameters())
    assert modes(a)==modes(b)
    with pytest.raises(ValueError,match="reused"):
        map_frame(s,sequence=1)
    s=PLUGIN.create_session_strategy(); map_frame(s,sequence=3)
    with pytest.raises(ValueError,match="out-of-order"):
        map_frame(s,sequence=2)


@pytest.mark.parametrize("kind", ["raw", "axis", "button", "sequence"])
def test_missing_control_fields_reject_instead_of_zero_padding(kind):
    s=PLUGIN.create_session_strategy(); m=message(sequence=0)
    if kind=="raw": m=replace(m,gamepad=replace(m.gamepad,raw_axes=None))
    if kind=="axis": m=replace(m,gamepad=replace(m.gamepad,raw_axes=(0.,)*3))
    if kind=="button": m=replace(m,gamepad=replace(m.gamepad,buttons=()))
    if kind=="sequence": m=replace(m,sequence=None)
    with pytest.raises(ValueError):
        s.map_input(ViewerInputSource(clock=lambda:0.).ingest_control_message(m),parameters())


@pytest.mark.parametrize("change", [
    {"output_side":"both"},{"neutral_threshold":True},{"neutral_threshold":.2},
    {"schema":"v2"},{"unknown":0},{"left":{"axes":[0,1],"signs":[1,0],"mode_button":4}},
    {"right":{"axes":[1,2],"signs":[1,-1],"mode_button":5}},
    {"right":{"axes":[2,3],"signs":[1,-1],"mode_button":4}},
])
def test_invalid_parameters_reject_before_execution(change):
    with pytest.raises((TypeError,ValueError)):
        PLUGIN.normalize_parameters({"gamepad_plane_control":{**deepcopy(CONFIG),**change}})


def test_mutual_exclusion_frozen_configuration_and_mid_run_change():
    raw=deepcopy(CONFIG)
    normalized=PLUGIN.normalize_parameters({"gamepad_plane_control":raw})
    raw["left"]["signs"][0]=-1
    assert normalized["gamepad_plane_control"]["left"]["signs"]==(1,-1)
    assert PLUGIN.normalize_parameters(normalized)==normalized
    with pytest.raises(TypeError): normalized["gamepad_plane_control"]["left"]["axes"]=(1,0)
    with pytest.raises(ValueError,match="mutually exclusive"):
        PLUGIN.normalize_parameters({**parameters(),"gamepad_axis_map":{"axis_indices":[0,1,3],"axis_signs":[1,-1,-1]}})
    s=PLUGIN.create_session_strategy(); map_frame(s,sequence=0)
    with pytest.raises(ValueError,match="configuration changed"):
        map_frame(s,sequence=1,side="right")


def test_bad_factory_and_identity_fail_closed():
    with pytest.raises(TypeError): replace(PLUGIN,session_strategy_factory=5)
    with pytest.raises(TypeError): replace(PLUGIN,session_strategy_factory=lambda:PLUGIN.strategy).create_session_strategy()
    with pytest.raises(TypeError): replace(PLUGIN,session_strategy_factory=lambda:object()).create_session_strategy()


def test_provider_restart_requires_neutral_and_retired_stream_is_rejected():
    s=PLUGIN.create_session_strategy(); map_frame(s,sequence=0)
    map_frame(s,(1.,0.,0.,0.),sequence=1)
    restarted=replace(message((1.,0.,0.,0.),sequence=0),metadata={"viewer_provider_session_id":"new-stream"})
    src=ViewerInputSource(clock=lambda:0.)
    result=s.map_input(src.ingest_control_message(restarted),parameters())
    assert result.values==(0.,0.,0.)
    neutral=replace(message(sequence=1),metadata={"viewer_provider_session_id":"new-stream"})
    s.map_input(src.ingest_control_message(neutral),parameters())
    with pytest.raises(ValueError,match="retired"):
        map_frame(s,sequence=2)


@pytest.mark.parametrize("identity", [None, "", "has space", "x"*129, 1])
def test_invalid_provider_session_is_rejected_by_source(identity):
    m=replace(message(),metadata={"viewer_provider_session_id":identity})
    with pytest.raises(ValueError,match="provider_session_id"):
        ViewerInputSource(clock=lambda:0.).ingest_control_message(m)


def test_empty_bootstrap_does_not_freeze_a_fabricated_world_frame():
    from xpotato_sim.schemas import RawInputFrame
    s=PLUGIN.create_session_strategy()
    empty=RawInputFrame(source="viewer",timestamp_s=0.,values=(),buttons=(),metadata={})
    assert s.map_input(empty,parameters()).values==(0.,0.,0.)
    src=ViewerInputSource(clock=lambda:0.)
    neutral=replace(message(sequence=0),metadata={"viewer_provider_session_id":"tool-stream","control_frame":"tool"})
    s.map_input(src.ingest_control_message(neutral),parameters())
    move=replace(message((.55,0.,0.,0.),sequence=1),metadata={"viewer_provider_session_id":"tool-stream","control_frame":"tool"})
    result=s.map_input(src.ingest_control_message(move),parameters())
    assert result.metadata["control_frame"]=="tool"
    assert result.metadata["local_endpoint_velocity_m_s"]==pytest.approx((.05,0.,0.))
