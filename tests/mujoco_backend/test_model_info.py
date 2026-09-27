from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE

from xpotato_sim.mujoco_backend import (
    inspect_mujoco_model,
    load_mujoco_model,
)


def test_inspect_mujoco_model_returns_joint_body_and_site_names() -> None:
    bundle = load_mujoco_model(FAST_ARM_ROBOT_PROFILE.mujoco_model_asset)
    info = inspect_mujoco_model(bundle.model)

    assert info.joint_names
    assert info.body_names
    assert info.site_names
    assert "tip" in info.site_names
    assert "sholder_joint_1" in info.joint_names
    assert "base_link" in info.body_names
