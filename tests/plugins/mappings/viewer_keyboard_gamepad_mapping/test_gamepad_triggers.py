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
    {"schema": "v2"},
    {"left": {"axes": [0, 1], "signs": [1, -1], "trigger_button": 4, "sign_button": 4}},
    {"right": {"axes": [1, 2], "signs": [1, -1], "trigger_button": 7, "sign_button": 5}},
])
def test_invalid_trigger_parameters_fail_closed(change):
    with pytest.raises((TypeError, ValueError)):
        PLUGIN.normalize_parameters({"gamepad_trigger_control": {**deepcopy(CONFIG), **change}})
def test_trigger_plane_and_axis_map_are_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        PLUGIN.normalize_parameters({
            **parameters(),
            "gamepad_axis_map": {"axis_indices": [0, 1, 2], "axis_signs": [1, 1, 1]},
        })
    from tests.plugins.mappings.viewer_keyboard_gamepad_mapping.test_gamepad_planes import parameters as plane_parameters
    with pytest.raises(ValueError, match="mutually exclusive"):
        PLUGIN.normalize_parameters({**parameters(), **plane_parameters()})


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
