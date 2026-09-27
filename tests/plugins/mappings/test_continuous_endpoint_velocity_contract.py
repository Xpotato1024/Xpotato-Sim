from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from math import sqrt

import pytest

from xpotato_sim.plugins.mappings._continuous_endpoint_velocity import (
    build_continuous_endpoint_velocity_intent,
    build_normalized_analog_fixture_intent,
)
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.keyboard import (
    build_keyboard_continuous_velocity_intent,
    build_keyboard_motion_command,
)
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.keyboard import (
    KeyboardBinding,
    KeyboardInputConfig,
)


def _build(**overrides: object):
    kwargs = {
        "source_kind": "fixture_analog",
        "source_timestamp_s": 1.25,
        "speed_m_s": 0.2,
        "deadzone": 0.1,
        "max_delta_m": 0.03,
        "control_frame": "world",
    }
    kwargs.update(overrides)
    return build_continuous_endpoint_velocity_intent((1.0, 0.0, 0.0), **kwargs)


def test_valid_world_and_tool_requested_frames() -> None:
    world = _build()
    tool = _build(control_frame="tool")
    assert world.local_endpoint_velocity_m_s == (0.2, 0.0, 0.0)
    assert world.control_frame == "world"
    assert tool.control_frame == "tool"
    assert "resolved_world_endpoint_velocity_m_s" not in tool.to_metadata()


def test_deadzone_speed_and_diagonal_norm_clamp_provenance() -> None:
    deadzone = build_continuous_endpoint_velocity_intent(
        (0.1, -0.1, 0.0), source_kind="fixture", source_timestamp_s=0.0,
        speed_m_s=2.0, deadzone=0.1, max_delta_m=0.0,
    )
    diagonal = build_continuous_endpoint_velocity_intent(
        (1.0, 1.0, 0.0), source_kind="fixture", source_timestamp_s=0.0,
        speed_m_s=0.2, deadzone=0.0, max_delta_m=0.03,
    )
    assert deadzone.zero_input is True
    assert diagonal.axis_values == pytest.approx((1.0 / sqrt(2.0), 1.0 / sqrt(2.0), 0.0))
    assert diagonal.local_endpoint_velocity_m_s == pytest.approx((0.2 / sqrt(2.0), 0.2 / sqrt(2.0), 0.0))
    assert diagonal.norm_clamped is True
    assert diagonal.local_endpoint_max_delta_m == 0.03


@pytest.mark.parametrize(
    ("supplement", "expected_axis", "expected_zero"),
    [
        ((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), False),
        ((0.0, 0.0, -1.0), (0.0, 0.0, -1.0), False),
        ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), True),
    ],
)
def test_zero_input_uses_final_axis_after_supplement(
    supplement: tuple[float, float, float],
    expected_axis: tuple[float, float, float],
    expected_zero: bool,
) -> None:
    intent = build_continuous_endpoint_velocity_intent(
        (0.0, 0.0, 0.0),
        source_kind="viewer_gamepad",
        source_timestamp_s=0.0,
        speed_m_s=0.1,
        deadzone=0.1,
        max_delta_m=0.03,
        supplemental_axis_values=supplement,
    )
    assert intent.axis_values == expected_axis
    assert intent.zero_input is expected_zero


def test_zero_speed_preserves_nonzero_input_semantics() -> None:
    intent = build_continuous_endpoint_velocity_intent(
        (1.0, 0.0, 0.0),
        source_kind="fixture_analog",
        source_timestamp_s=0.0,
        speed_m_s=0.0,
        deadzone=0.0,
        max_delta_m=0.03,
    )
    assert intent.axis_values == (1.0, 0.0, 0.0)
    assert intent.local_endpoint_velocity_m_s == (0.0, 0.0, 0.0)
    assert intent.zero_input is False


def test_keyboard_adapter_and_public_helper_preserve_legacy_clamp_before_deadzone() -> None:
    config = KeyboardInputConfig(
        bindings={
            "KeyD": KeyboardBinding("x", 1),
            "KeyW": KeyboardBinding("y", 1),
        },
        speed_m_s=0.1,
        deadzone=0.8,
        max_delta_m=0.03,
    )
    intent = build_keyboard_continuous_velocity_intent(
        ("KeyD", "KeyW"), timestamp_s=1.0, config=config
    )
    command = build_keyboard_motion_command(
        ("KeyD", "KeyW"),
        current_tip_position_m=(0.0, 0.0, 0.0),
        timestamp_s=1.0,
        config=config,
    )

    assert intent.deadzone_applied_axis_values == (0.0, 0.0, 0.0)
    assert intent.axis_values == (0.0, 0.0, 0.0)
    assert intent.local_endpoint_velocity_m_s == (0.0, 0.0, 0.0)
    assert intent.zero_input is True
    assert intent.norm_clamped is True
    assert command.metadata["axis_values"] == (0.0, 0.0, 0.0)
    assert command.metadata["local_endpoint_velocity_m_s"] == (0.0, 0.0, 0.0)
    assert command.metadata["zero_input"] is True
    assert command.metadata["norm_clamped"] is True


def test_active_zero_is_distinct_from_inactive_and_stale() -> None:
    active_zero = build_continuous_endpoint_velocity_intent(
        (0.0, 0.0, 0.0), source_kind="keyboard", source_timestamp_s=0.0,
        speed_m_s=0.1, deadzone=0.0, max_delta_m=0.03,
    )
    inactive = _build(source_active=False)
    stale = _build(source_active=False, stale_reason="command_timeout")
    assert active_zero.source_active is True and active_zero.zero_input is True and not active_zero.stale
    assert inactive.source_active is False and not inactive.stale
    assert stale.source_active is False and stale.stale
    with pytest.raises(ValueError, match="active source"):
        _build(source_active=True, stale_reason="command_timeout")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"control_frame": "camera"}, "control_frame"),
        ({"source_kind": ""}, "source_kind"),
        ({"deadzone": -0.1}, "deadzone"),
        ({"speed_m_s": -0.1}, "speed_m_s"),
        ({"max_delta_m": -0.1}, "max_delta_m"),
    ],
)
def test_invalid_scalar_contract_values_are_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _build(**overrides)


@pytest.mark.parametrize("axes", [(1.0, 2.0), (1.0, 2.0, 3.0, 4.0), (float("nan"), 0.0, 0.0)])
def test_invalid_vectors_are_rejected(axes: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        build_continuous_endpoint_velocity_intent(
            axes, source_kind="fixture", source_timestamp_s=0.0,
            speed_m_s=0.1, deadzone=0.0, max_delta_m=0.03,
        )


def test_contract_is_deterministic_and_deeply_immutable_without_mutating_inputs() -> None:
    axes = [0.5, 0.0, 0.0]
    diagnostics = {"raw": [1, 2]}
    first = build_continuous_endpoint_velocity_intent(
        axes, source_kind="fixture", source_timestamp_s=0.0,
        speed_m_s=0.1, deadzone=0.0, max_delta_m=0.03, source_diagnostics=diagnostics,
    )
    second = build_continuous_endpoint_velocity_intent(
        axes, source_kind="fixture", source_timestamp_s=0.0,
        speed_m_s=0.1, deadzone=0.0, max_delta_m=0.03, source_diagnostics=diagnostics,
    )
    assert first == second
    assert axes == [0.5, 0.0, 0.0] and diagnostics == {"raw": [1, 2]}
    assert first.source_diagnostics["raw"] == (1, 2)
    with pytest.raises(TypeError):
        first.source_diagnostics["raw"] = ()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        first.source_active = False  # type: ignore[misc]
    serialized = first.to_metadata()
    assert json.loads(json.dumps(dict(serialized)))["source_diagnostics"] == {"raw": [1, 2]}
    serialized["source_diagnostics"]["raw"] = [9]  # type: ignore[index]
    assert first.source_diagnostics["raw"] == (1, 2)


def test_keyboard_gamepad_equivalent_builder_and_analog_fixture_have_common_field_parity() -> None:
    config = KeyboardInputConfig(
        bindings={"KeyD": KeyboardBinding("x", 1)}, speed_m_s=0.1, deadzone=0.0, max_delta_m=0.03,
    )
    keyboard = build_keyboard_continuous_velocity_intent(("KeyD",), timestamp_s=5.0, config=config)
    gamepad = build_continuous_endpoint_velocity_intent(
        (1.0, 0.0, 0.0), source_kind="viewer_gamepad", source_timestamp_s=5.0,
        speed_m_s=0.1, deadzone=0.0, max_delta_m=0.03,
        source_diagnostics={"raw_axes": (1.0, 0.0, 0.0)},
    )
    analog = build_normalized_analog_fixture_intent(
        (1.0, 0.0, 0.0), source_kind="fixture_analog", source_timestamp_s=5.0,
        speed_m_s=0.1, deadzone=0.0, max_delta_m=0.03,
    )
    common = lambda intent: (
        intent.intent_kind, intent.input_continuity, intent.axis_values,
        intent.local_endpoint_velocity_m_s, intent.control_frame,
        intent.source_active, intent.zero_input, intent.norm_clamped,
    )
    assert common(keyboard) == common(gamepad) == common(analog)
    assert {keyboard.source_kind, gamepad.source_kind, analog.source_kind} == {
        "keyboard", "viewer_gamepad", "fixture_analog"
    }


@pytest.mark.parametrize("control_frame", ["world", "tool"])
@pytest.mark.parametrize("axes", [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)])
def test_cross_source_zero_diagonal_and_frame_parity(control_frame: str, axes: tuple[float, float, float]) -> None:
    intents = [
        build_continuous_endpoint_velocity_intent(
            axes, source_kind=source, source_timestamp_s=2.0, speed_m_s=0.1,
            deadzone=0.0, max_delta_m=0.03, control_frame=control_frame,
        )
        for source in ("keyboard", "viewer_gamepad", "fixture_analog")
    ]
    assert len({(intent.axis_values, intent.local_endpoint_velocity_m_s, intent.control_frame, intent.zero_input) for intent in intents}) == 1
