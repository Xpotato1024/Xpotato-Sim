"""``viewer_keyboard_gamepad_mapping/v1`` のfixed Mapping declaration entry point。

importはkeyboard/gamepad interpretation ownerへroutingするだけで、browser acquisitionや
Robot command executionを開始しない。
"""

from .implementation import VIEWER_CONTROL_MAPPING_PLUGIN


CONTROL_MAPPING_PLUGIN = VIEWER_CONTROL_MAPPING_PLUGIN

__all__ = ["CONTROL_MAPPING_PLUGIN"]
