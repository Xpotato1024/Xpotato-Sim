"""平面/triggerの公開binding入口で構造検証と固有の制約を維持する。"""
import pytest

from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.gamepad_planes import StickBinding
from xpotato_sim.plugins.mappings.viewer_keyboard_gamepad_mapping.gamepad_triggers import TriggerBinding


@pytest.fixture(params=[StickBinding, TriggerBinding])
def binding(request):
    def build(axes=(0, 1), signs=(1, -1)):
        if request.param is StickBinding:
            return StickBinding(axes, signs, mode_button=4)
        return TriggerBinding(axes, signs, trigger_button=6, sign_button=4)
    return build


@pytest.mark.parametrize("axes,signs", [([0, 1], [1, -1]), ((2, 3), (-1, 1))])
def test_binding_normalizes_pairs_without_changing_order_or_sign(binding, axes, signs):
    result = binding(axes, signs)
    assert result.axes == tuple(axes)
    assert result.signs == tuple(signs)
    assert type(result.axes) is tuple
    assert type(result.signs) is tuple


@pytest.mark.parametrize("field", ["axes", "signs"])
@pytest.mark.parametrize("value", [None, 1, "01", b"01", [], [0], [0, 1, 2], [True, 1], [0, False], [0., 1], {0: 0, 1: 1}])
def test_both_binding_entrances_reject_malformed_pairs_with_existing_error(binding, field, value):
    with pytest.raises(ValueError, match=f"^{field} requires two integers$"):
        binding(**{field: value})


@pytest.mark.parametrize("axes,signs", [((-1, 1), (1, -1)), ((0, 0), (1, -1)), ((0, 1), (0, 1)), ((0, 1), (1, 2))])
def test_binding_keeps_semantic_axis_and_sign_validation(binding, axes, signs):
    with pytest.raises(ValueError, match="^invalid stick axis indices or signs$"):
        binding(axes, signs)


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_each_binding_keeps_its_button_constraint(value):
    with pytest.raises(ValueError, match="mode_button requires a non-negative integer"):
        StickBinding((0, 1), (1, -1), value)
    with pytest.raises(ValueError, match="trigger_button requires a non-negative integer"):
        TriggerBinding((0, 1), (1, -1), value, 4)
    with pytest.raises(ValueError, match="sign_button requires a non-negative integer"):
        TriggerBinding((0, 1), (1, -1), 6, value)
