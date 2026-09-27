from fast_arm_core.reference.initial_state import FAST_ARM_INITIAL_STATE
from xpotato_sim.plugins.robots.fast_arm.adapter.initial_state import (
    FAST_ARM_INITIAL_STATE_CONTRACT,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE


def test_initial_state_contract_is_an_exact_core_projection() -> None:
    contract = FAST_ARM_INITIAL_STATE_CONTRACT
    assert contract.source_id == FAST_ARM_INITIAL_STATE.source_id
    assert contract.qpos_rad == FAST_ARM_INITIAL_STATE.qpos_rad
    assert contract.tip_position_m == FAST_ARM_INITIAL_STATE.tip_position_m
    assert (
        contract.tool_orientation_wxyz
        == FAST_ARM_INITIAL_STATE.tool_orientation_wxyz
    )
    assert contract.frame == FAST_ARM_INITIAL_STATE.frame
    assert contract.position_unit == FAST_ARM_INITIAL_STATE.tip_position_unit
    assert contract.quaternion_order == FAST_ARM_INITIAL_STATE.quaternion_order
    assert FAST_ARM_ROBOT_PROFILE.initial_keyframe_name == FAST_ARM_INITIAL_STATE.source_id
