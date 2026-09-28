"""保存済みassembly診断の互換入口。制御ループは共通compositionへ委譲する。"""
from fast_arm_core.assembly import FastArmAssembly
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.implementation import (
    ViewerKeyboardGamepadMappingStrategy, normalize_viewer_control_mapping_parameters,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.coordinated import FastArmAssemblyMotionProvider
from xpotato_sim.runtime.composition.coordinated_input import CoordinatedInputRuntime


class FastArmCoordinatedGamepadRuntime(CoordinatedInputRuntime):
    def __init__(self, *, assembly: FastArmAssembly, mapping_parameters,
                 side_to_arm, epoch, dt_s, max_input_age_s, clock):
        super().__init__(provider=FastArmAssemblyMotionProvider(assembly),
            mapping_parameters=normalize_viewer_control_mapping_parameters(mapping_parameters),
            mapping_factory=lambda: ViewerKeyboardGamepadMappingStrategy(session=True),
            side_to_arm=side_to_arm, epoch=epoch, dt_s=dt_s,
            max_input_age_s=max_input_age_s, clock=clock)
