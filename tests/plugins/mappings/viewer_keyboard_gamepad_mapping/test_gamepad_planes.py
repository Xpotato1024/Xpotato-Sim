"""旧平面切替は読み替えず拒否する。"""
from pathlib import Path
import pytest
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping import VIEWER_CONTROL_MAPPING_PLUGIN as PLUGIN

@pytest.mark.parametrize("value", [None, {}, {"schema": "gamepad-plane-control/v1"}, {
    "schema": "gamepad-plane-control/v1", "output_side": "left", "neutral_threshold": .1,
    "left": {"axes": [0, 1], "signs": [1, -1], "mode_button": 4},
    "right": {"axes": [2, 3], "signs": [1, -1], "mode_button": 5},
}])
def test_retired_plane_configuration_is_rejected(value):
    with pytest.raises(ValueError, match="gamepad_plane_control"):
        PLUGIN.normalize_parameters({"gamepad_plane_control": value})

def test_retired_plane_implementation_is_absent():
    root = Path(__file__).resolve().parents[4]
    assert not (root / "src/xpotato_sim/plugins/mappings/viewer_keyboard_gamepad_mapping/gamepad_planes.py").exists()
