"""軸設定の不正値を実行前に拒否し、旧設定の意味を維持する。"""
from dataclasses import FrozenInstanceError
import pytest
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.gamepad_axes import (
    GamepadAxisMap, apply_gamepad_axis_map, coerce_gamepad_axis_map,
)
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping import (
    normalize_viewer_control_mapping_parameters,
)


def test_explicit_axis_map_selects_and_signs_without_modifying_input():
    raw = [0.2, -0.3, 0.8, -0.5]
    config = GamepadAxisMap((0, 1, 3), (1, -1, -1))
    assert apply_gamepad_axis_map(raw, config) == pytest.approx((0.2, 0.3, 0.5))
    assert raw == [0.2, -0.3, 0.8, -0.5]
    with pytest.raises(ValueError, match="missing"):
        apply_gamepad_axis_map(raw[:3], config)


@pytest.mark.parametrize("value", [
    None, True, {}, {"axis_indices": [0, 1, 2]},
    {"axis_indices": [0, 1, 2], "axis_signs": [1, 1, 1], "typo": 0},
    {"axis_indices": [-1, 1, 2], "axis_signs": [1, 1, 1]},
    {"axis_indices": [0, 0, 2], "axis_signs": [1, 1, 1]},
    {"axis_indices": [False, 1, 2], "axis_signs": [1, 1, 1]},
    {"axis_indices": [0.0, 1, 2], "axis_signs": [1, 1, 1]},
    {"axis_indices": [0, 1], "axis_signs": [1, 1, 1]},
    {"axis_indices": "012", "axis_signs": [1, 1, 1]},
    {"axis_indices": [0, 1, 2], "axis_signs": [0, 1, 1]},
    {"axis_indices": [0, 1, 2], "axis_signs": [True, 1, 1]},
    {"axis_indices": [0, 1, 2], "axis_signs": [1, float("nan"), 1]},
])
def test_bad_axis_map_is_rejected_before_mapping(value):
    with pytest.raises((ValueError, TypeError)):
        normalize_viewer_control_mapping_parameters({"gamepad_axis_map": value})


def test_configuration_is_copied_and_deeply_frozen():
    raw = {"axis_indices": [0, 1, 3], "axis_signs": [1, -1, -1]}
    config = coerce_gamepad_axis_map(raw)
    normalized = normalize_viewer_control_mapping_parameters({"gamepad_axis_map": raw})
    raw["axis_signs"][0] = -1
    assert config.axis_signs == (1, -1, -1)
    assert normalized["gamepad_axis_map"]["axis_signs"] == (1, -1, -1)
    with pytest.raises(FrozenInstanceError):
        config.axis_signs = (1, 1, 1)
    with pytest.raises(TypeError):
        normalized["gamepad_axis_map"]["axis_signs"] = (1, 1, 1)
    assert normalize_viewer_control_mapping_parameters(normalized) == normalized


def test_omitted_axis_map_preserves_old_parameter_projection():
    assert set(normalize_viewer_control_mapping_parameters({})) == {
        "keyboard_config", "gamepad_speed_m_s", "gamepad_deadzone", "gamepad_max_delta_m",
    }
