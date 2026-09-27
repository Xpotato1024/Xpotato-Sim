from __future__ import annotations

import pytest

from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.keyboard import (
    KeyboardBinding,
    KeyboardInputConfig,
    build_default_keyboard_input_config,
    build_keyboard_motion_command,
)
from xpotato_sim.runtime.control.desired_endpoint_resolver import resolve_desired_endpoint_from_motion_command
from xpotato_sim.schemas import MotionCommand


def test_default_keyboard_keybind_contract_matches_package_resource() -> None:
    config = build_default_keyboard_input_config()

    assert config.speed_m_s == 0.1
    assert config.deadzone == 0.0
    assert config.max_delta_m == 0.03
    assert config.bindings["KeyW"] == KeyboardBinding(axis="y", direction=1)
    assert config.bindings["KeyS"] == KeyboardBinding(axis="y", direction=-1)
    assert config.bindings["KeyA"] == KeyboardBinding(axis="x", direction=-1)
    assert config.bindings["KeyD"] == KeyboardBinding(axis="x", direction=1)
    assert config.bindings["Space"] == KeyboardBinding(axis="z", direction=1)
    assert config.bindings["ShiftLeft"] == KeyboardBinding(axis="z", direction=-1)
    assert config.bindings["ShiftRight"] == KeyboardBinding(axis="z", direction=-1)


@pytest.mark.parametrize(
    ("pressed_keys", "expected_axis_values"),
    [
        (("KeyW",), (0.0, 1.0, 0.0)),
        (("KeyS",), (0.0, -1.0, 0.0)),
        (("KeyA",), (-1.0, 0.0, 0.0)),
        (("KeyD",), (1.0, 0.0, 0.0)),
        (("Space",), (0.0, 0.0, 1.0)),
        (("ShiftLeft",), (0.0, 0.0, -1.0)),
        (("ShiftRight",), (0.0, 0.0, -1.0)),
    ],
)
def test_keyboard_pressed_keys_map_to_expected_axis_delta(
    pressed_keys: tuple[str, ...],
    expected_axis_values: tuple[float, float, float],
) -> None:
    command = build_keyboard_motion_command(
        pressed_keys,
        current_tip_position_m=(1.0, 2.0, 3.0),
        timestamp_s=12.5,
    )

    assert command.target is None
    assert command.metadata["source_kind"] == "keyboard"
    assert command.metadata["intent_kind"] == "local_endpoint_velocity"
    assert command.metadata["input_continuity"] == "continuous"
    assert command.metadata["control_frame"] == "world"
    assert command.metadata["pressed_keys"] == tuple(sorted(pressed_keys))
    assert command.metadata["axis_values"] == expected_axis_values
    assert command.metadata["current_tip_position_m"] == (1.0, 2.0, 3.0)
    expected_velocity_m_s = tuple(component * 0.1 for component in expected_axis_values)
    assert command.metadata["local_endpoint_velocity_m_s"] == expected_velocity_m_s
    assert command.metadata["resolved_world_endpoint_velocity_m_s"] == expected_velocity_m_s
    assert command.metadata["endpoint_velocity_m_s"] == expected_velocity_m_s
    assert command.metadata["endpoint_velocity_frame"] == "mujoco_world"


def test_keyboard_opposite_keys_cancel_on_same_axis() -> None:
    command = build_keyboard_motion_command(
        ("KeyW", "KeyS", "KeyA", "KeyD"),
        current_tip_position_m=(0.5, 0.5, 0.5),
        timestamp_s=1.0,
    )

    assert command.metadata["axis_values"] == (0.0, 0.0, 0.0)
    assert command.metadata["endpoint_velocity_m_s"] == (0.0, 0.0, 0.0)
    assert command.metadata["resolved_world_endpoint_velocity_m_s"] == (0.0, 0.0, 0.0)


def test_keyboard_max_delta_m_clamps_total_delta() -> None:
    config = KeyboardInputConfig(
        bindings={
            "KeyW": KeyboardBinding(axis="y", direction=1),
            "KeyD": KeyboardBinding(axis="x", direction=1),
            "Space": KeyboardBinding(axis="z", direction=1),
        },
        speed_m_s=0.1,
        deadzone=0.0,
        max_delta_m=0.03,
    )

    command = build_keyboard_motion_command(
        ("KeyW", "KeyD", "Space"),
        current_tip_position_m=(0.0, 0.0, 0.0),
        timestamp_s=2.0,
        config=config,
    )

    endpoint_velocity_m_s = command.metadata["endpoint_velocity_m_s"]
    assert sum(component * component for component in endpoint_velocity_m_s) <= 0.1 * 0.1 + 1e-12


def test_keyboard_empty_key_state_is_no_op() -> None:
    command = build_keyboard_motion_command(
        (),
        current_tip_position_m=(0.1, 0.2, 0.3),
        timestamp_s=0.0,
    )

    assert command.metadata["axis_values"] == (0.0, 0.0, 0.0)
    assert command.metadata["endpoint_velocity_m_s"] == (0.0, 0.0, 0.0)
    assert command.metadata["resolved_world_endpoint_velocity_m_s"] == (0.0, 0.0, 0.0)


def test_keyboard_motion_command_resolves_desired_endpoint() -> None:
    command = build_keyboard_motion_command(
        ("KeyW", "KeyA"),
        current_tip_position_m=(0.1, 0.2, 0.3),
        timestamp_s=0.5,
    )

    with pytest.raises(ValueError, match='MotionCommand.metadata\\["desired_endpoint_m"\\] is required'):
        resolve_desired_endpoint_from_motion_command(command)


@pytest.mark.parametrize(
    ("config_kwargs", "match"),
    [
        ({"bindings": {"KeyW": type("InvalidBinding", (), {"axis": "q", "direction": 1})()}, "speed_m_s": 0.01, "deadzone": 0.0, "max_delta_m": 0.03}, "unknown axis"),
        ({"bindings": {"KeyW": KeyboardBinding(axis="x", direction=1)}, "speed_m_s": -0.01, "deadzone": 0.0, "max_delta_m": 0.03}, "speed_m_s must be non-negative"),
        ({"bindings": {"KeyW": KeyboardBinding(axis="x", direction=1)}, "speed_m_s": 0.01, "deadzone": 0.0, "max_delta_m": -0.03}, "max_delta_m must be non-negative"),
    ],
)
def test_keyboard_config_validation_rejects_invalid_values(config_kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        KeyboardInputConfig(**config_kwargs)


def test_keyboard_current_tip_position_validation_rejects_bad_values() -> None:
    with pytest.raises(ValueError, match="must contain exactly three values"):
        build_keyboard_motion_command(("KeyW",), current_tip_position_m=(0.0, 0.0), timestamp_s=0.0)

    with pytest.raises(ValueError, match="must contain only finite values"):
        build_keyboard_motion_command(("KeyW",), current_tip_position_m=(0.0, float("nan"), 0.0), timestamp_s=0.0)
