"""stick XY + analog trigger Zとbumper符号ラッチを実デバイスなしで検証する。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import pytest

from xpotato_sim.plugins.input_sources.viewer import ViewerInputSource
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping import (
    VIEWER_CONTROL_MAPPING_PLUGIN as PLUGIN,
)
from xpotato_sim.schemas import (
    ViewerControlGamepadButtonMessage,
    ViewerControlGamepadMessage,
    ViewerControlMessage,
)


CONFIG = {
    "schema": "gamepad-trigger-control/v1",
    "output_side": "left",
    "neutral_threshold": 0.1,
    "left": {"axes": [0, 1], "signs": [1, -1], "trigger_button": 6, "sign_button": 4},
    "right": {"axes": [2, 3], "signs": [1, -1], "trigger_button": 7, "sign_button": 5},
}
def parameters(side="left"):
    return {"gamepad_trigger_control": {**deepcopy(CONFIG), "output_side": side}}


def message(raw=(0., 0., 0., 0.), *, triggers=(0., 0.), held=(), sequence=1,
            connected=True, stale=False, identity="test-pad", trigger_values=True):
    values = [0.] * 8
    values[6], values[7] = triggers
    buttons = tuple(
        ViewerControlGamepadButtonMessage(
            pressed=(i in held) or values[i] > 0.5,
            value=(values[i] if trigger_values or i not in (6, 7) else None),
        )
        for i in range(8)
    )
    zero = not any(abs(v) > 0.1 for v in raw) and not held and not any(v > 0.1 for v in triggers)
    return ViewerControlMessage(
        type="viewer_control_message", timestamp_s=sequence / 60, sequence=sequence,
        source_kind="gamepad", metadata={"viewer_provider_session_id": "trigger-test"},
        gamepad=ViewerControlGamepadMessage(
            connected=connected, id=identity, index=0, raw_axes=raw, axes=raw,
            buttons=buttons, stale=stale, zero_state=zero,
        ),
    )


def frame(*args, **kwargs):
    return ViewerInputSource(clock=lambda: 0.).ingest_control_message(message(*args, **kwargs))


def map_frame(strategy, *args, side="left", **kwargs):
    return strategy.map_input(frame(*args, **kwargs), parameters(side))


def presentation(intent):
    return intent.metadata["gamepad_trigger_control_v1"]
@pytest.mark.parametrize("side,offset,trigger", [
    ("left", 0, 0),
    ("right", 2, 1),
])
def test_stick_xy_and_trigger_z_are_simultaneous(side, offset, trigger):
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, sequence=0, side=side)
    raw = [0.] * 4
    raw[offset], raw[offset + 1] = .55, -.55
    triggers = [0., 0.]
    triggers[trigger] = .55
    result = map_frame(strategy, tuple(raw), triggers=tuple(triggers), sequence=1, side=side)
    velocity = result.metadata["local_endpoint_velocity_m_s"]
    assert velocity == pytest.approx((.05, .05, .05))
    assert presentation(result)["sides"][side]["trigger_value"] == .55
    assert presentation(result)["sides"][side]["z_sign"] == 1


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("axis,sign", [(0,1), (0,-1), (1,1), (1,-1), (2,1), (2,-1)])
def test_each_stick_reaches_six_directions_without_opposite_stick(side, axis, sign):
    strategy = PLUGIN.create_session_strategy()
    offset, bumper, trigger_index = (0, 4, 0) if side == "left" else (2, 5, 1)
    held = (bumper,) if axis == 2 and sign < 0 else ()
    map_frame(strategy, held=held, sequence=0, side=side)
    raw, triggers = [0.]*4, [0., 0.]
    if axis == 2: triggers[trigger_index] = 1.
    else: raw[offset + axis] = float(sign if axis == 0 else -sign)
    result = map_frame(strategy, tuple(raw), triggers=tuple(triggers), held=held, sequence=1, side=side)
    expected = [0.]*3; expected[axis] = sign * .1
    assert result.metadata["local_endpoint_velocity_m_s"] == pytest.approx(expected)
    other = "right" if side == "left" else "left"
    assert presentation(result)["sides"][other]["velocity_m_s"] == (0., 0., 0.)


@pytest.mark.parametrize("side,sign_button,trigger_index", [
    ("left", 4, 0),
    ("right", 5, 1),
])
def test_bumper_selects_negative_z_only_while_trigger_is_neutral(side, sign_button, trigger_index):
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, held=(sign_button,), sequence=0, side=side)
    triggers = [0., 0.]
    triggers[trigger_index] = .55
    negative = map_frame(
        strategy, triggers=tuple(triggers), held=(sign_button,), sequence=1, side=side,
    )
    assert negative.metadata["local_endpoint_velocity_m_s"][2] == pytest.approx(-.05)
    assert presentation(negative)["sides"][side]["z_sign"] == -1

    positive_request_while_moving = map_frame(
        strategy, triggers=tuple(triggers), held=(), sequence=2, side=side,
    )
    assert positive_request_while_moving.metadata["local_endpoint_velocity_m_s"][2] == pytest.approx(-.05)
    assert presentation(positive_request_while_moving)["sides"][side]["z_sign"] == -1
    map_frame(strategy, triggers=(0., 0.), held=(), sequence=3, side=side)
    positive = map_frame(strategy, triggers=tuple(triggers), held=(), sequence=4, side=side)
    assert positive.metadata["local_endpoint_velocity_m_s"][2] == pytest.approx(.05)
    assert presentation(positive)["sides"][side]["z_sign"] == 1


def test_both_sides_keep_independent_z_signs():
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, held=(4,), sequence=0)
    result = map_frame(strategy, triggers=(.55, .55), held=(4,), sequence=1)
    sides = presentation(result)["sides"]
    assert sides["left"]["velocity_m_s"][2] == pytest.approx(-.05)
    assert sides["right"]["velocity_m_s"][2] == pytest.approx(.05)


def test_non_neutral_first_trigger_waits_for_neutral_before_z_motion():
    strategy = PLUGIN.create_session_strategy()
    first = map_frame(strategy, triggers=(.55, 0.), sequence=0)
    assert first.metadata["local_endpoint_velocity_m_s"][2] == 0.
    assert presentation(first)["sides"]["left"]["status"] == "waiting_trigger_neutral"
    map_frame(strategy, triggers=(0., 0.), sequence=1)
    moved = map_frame(strategy, triggers=(.55, 0.), sequence=2)
    assert moved.metadata["local_endpoint_velocity_m_s"][2] == pytest.approx(.05)


def test_missing_analog_trigger_value_rejects_instead_of_becoming_digital():
    strategy = PLUGIN.create_session_strategy()
    with pytest.raises(ValueError, match="analog trigger"):
        map_frame(strategy, sequence=0, trigger_values=False)


@pytest.mark.parametrize("change", [
    {"output_side": "both"},
    {"neutral_threshold": .2},
    {"neutral_threshold": True},
    {"schema": "v2"},
    {"unknown": 0},
    {"left": {"axes": [0, 1], "signs": [1, 0], "trigger_button": 6, "sign_button": 4}},
    {"left": {"axes": [0, 1], "signs": [1, -1], "trigger_button": 4, "sign_button": 4}},
    {"right": {"axes": [1, 2], "signs": [1, -1], "trigger_button": 7, "sign_button": 5}},
])
def test_invalid_trigger_parameters_fail_closed(change):
    with pytest.raises((TypeError, ValueError)):
        PLUGIN.normalize_parameters({"gamepad_trigger_control": {**deepcopy(CONFIG), **change}})
def test_trigger_axis_map_exclusion_and_retired_plane_rejection():
    with pytest.raises(ValueError, match="mutually exclusive"):
        PLUGIN.normalize_parameters({
            **parameters(),
            "gamepad_axis_map": {"axis_indices": [0, 1, 2], "axis_signs": [1, 1, 1]},
        })
    with pytest.raises(ValueError, match="gamepad_plane_control"):
        PLUGIN.normalize_parameters({**parameters(), "gamepad_plane_control": {}})


@pytest.mark.parametrize("kw", [{"connected": False}, {"stale": True}])
def test_unavailable_input_resets_trigger_arming(kw):
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, sequence=0)
    assert map_frame(strategy, triggers=(.55, 0.), sequence=1).values[2] > 0
    stop = map_frame(strategy, sequence=2, **kw)
    assert stop.values == (0., 0., 0.)
    assert presentation(stop)["sides"]["left"]["status"] == "waiting_trigger_neutral"
    recovered = map_frame(strategy, triggers=(.55, 0.), sequence=3)
    assert recovered.values[2] == 0.


def test_duplicate_is_idempotent_and_old_or_reused_sequence_rejects():
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, sequence=0)
    sample = frame(triggers=(.55, 0.), sequence=1)
    first = strategy.map_input(sample, parameters())
    assert presentation(first) == presentation(strategy.map_input(sample, parameters()))
    with pytest.raises(ValueError, match="reused"):
        map_frame(strategy, sequence=1)
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, sequence=3)
    with pytest.raises(ValueError, match="out-of-order"):
        map_frame(strategy, sequence=2)


@pytest.mark.parametrize("kind", ["raw", "axis", "button", "sequence"])
def test_missing_control_fields_reject_instead_of_zero_padding(kind):
    sample = message(sequence=0)
    if kind == "raw": sample = replace(sample, gamepad=replace(sample.gamepad, raw_axes=None))
    if kind == "axis": sample = replace(sample, gamepad=replace(sample.gamepad, raw_axes=(0.,)*3))
    if kind == "button": sample = replace(sample, gamepad=replace(sample.gamepad, buttons=()))
    if kind == "sequence": sample = replace(sample, sequence=None)
    with pytest.raises(ValueError):
        PLUGIN.create_session_strategy().map_input(
            ViewerInputSource(clock=lambda: 0.).ingest_control_message(sample), parameters())


def test_configuration_is_frozen_and_mid_run_change_rejects():
    raw = deepcopy(CONFIG)
    normalized = PLUGIN.normalize_parameters({"gamepad_trigger_control": raw})
    raw["left"]["signs"][0] = -1
    assert normalized["gamepad_trigger_control"]["left"]["signs"] == (1, -1)
    assert PLUGIN.normalize_parameters(normalized) == normalized
    with pytest.raises(TypeError):
        normalized["gamepad_trigger_control"]["left"]["axes"] = (1, 0)
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, sequence=0)
    with pytest.raises(ValueError, match="configuration changed"):
        map_frame(strategy, sequence=1, side="right")


def test_sessions_are_isolated_and_singleton_rejects_stateful_use():
    first, second = PLUGIN.create_session_strategy(), PLUGIN.create_session_strategy()
    assert first is not second and first is not PLUGIN.strategy
    map_frame(first, sequence=0)
    assert map_frame(first, triggers=(.55, 0.), sequence=1).values[2] > 0.
    assert map_frame(second, triggers=(.55, 0.), sequence=1).values[2] == 0.
    with pytest.raises(ValueError, match="runtime mapping session"):
        map_frame(PLUGIN.strategy, sequence=0)


def test_timeout_and_device_change_reset_trigger_before_nonzero_recovery():
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, sequence=0)
    source = ViewerInputSource(clock=lambda: 0.)
    source.ingest_control_message(message(triggers=(.55, 0.), sequence=1))
    source._clock = lambda: 1.
    assert strategy.map_input(source.read_frame(), parameters()).values == (0., 0., 0.)
    assert map_frame(strategy, triggers=(.55, 0.), sequence=2).values[2] == 0.
    map_frame(strategy, sequence=3)
    assert map_frame(strategy, triggers=(.55, 0.), sequence=4, identity="another-pad").values[2] == 0.


def test_provider_restart_requires_neutral_and_retired_stream_is_rejected():
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, sequence=0)
    map_frame(strategy, triggers=(.55, 0.), sequence=1)
    source = ViewerInputSource(clock=lambda: 0.)
    restarted = replace(message(triggers=(.55, 0.), sequence=0),
                        metadata={"viewer_provider_session_id": "new-stream"})
    assert strategy.map_input(source.ingest_control_message(restarted), parameters()).values[2] == 0.
    neutral = replace(message(sequence=1), metadata={"viewer_provider_session_id": "new-stream"})
    strategy.map_input(source.ingest_control_message(neutral), parameters())
    with pytest.raises(ValueError, match="retired"):
        map_frame(strategy, sequence=2)


@pytest.mark.parametrize("identity", [None, "", "has space", "x"*129, 1])
def test_invalid_provider_session_is_rejected_by_source(identity):
    sample = replace(message(), metadata={"viewer_provider_session_id": identity})
    with pytest.raises(ValueError, match="provider_session_id"):
        ViewerInputSource(clock=lambda: 0.).ingest_control_message(sample)


def test_per_side_norm_and_face_buttons_do_not_inject_z():
    strategy = PLUGIN.create_session_strategy()
    map_frame(strategy, sequence=0)
    result = map_frame(strategy, (1., -1., 1., -1.), held=(0, 1), sequence=1)
    for side in ("left", "right"):
        assert presentation(result)["sides"][side]["velocity_m_s"] == pytest.approx((.1/2**.5, .1/2**.5, 0.))


def test_empty_bootstrap_does_not_freeze_a_fabricated_world_frame():
    from xpotato_sim.schemas import RawInputFrame
    strategy = PLUGIN.create_session_strategy()
    assert strategy.map_input(RawInputFrame(source="viewer", timestamp_s=0., values=(), buttons=(), metadata={}), parameters()).values == (0., 0., 0.)
    source = ViewerInputSource(clock=lambda: 0.)
    for sequence, axes in [(0, (0.,)*4), (1, (.55, 0., 0., 0.))]:
        sample = replace(message(axes, sequence=sequence), metadata={"viewer_provider_session_id": "tool-stream", "control_frame": "tool"})
        result = strategy.map_input(source.ingest_control_message(sample), parameters())
    assert result.metadata["control_frame"] == "tool"
    assert result.metadata["local_endpoint_velocity_m_s"] == pytest.approx((.05, 0., 0.))


def test_bad_factory_and_identity_fail_closed():
    with pytest.raises(TypeError): replace(PLUGIN, session_strategy_factory=5)
    with pytest.raises(TypeError): replace(PLUGIN, session_strategy_factory=lambda: PLUGIN.strategy).create_session_strategy()
    with pytest.raises(TypeError): replace(PLUGIN, session_strategy_factory=lambda: object()).create_session_strategy()
