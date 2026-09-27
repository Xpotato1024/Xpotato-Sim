from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE

from pathlib import Path

import pytest

from xpotato_sim.mujoco_backend import load_mujoco_model


def test_fast_arm_profile_model_resource_points_to_scene_xml() -> None:
    resource = FAST_ARM_ROBOT_PROFILE.mujoco_model_asset

    assert resource.logical_identifier == "assets/mujoco/fast_arm/scene.xml"
    model_xml, assets = resource.model_xml_and_assets()
    assert model_xml
    assert set(assets) >= {"arm.xml", "meshes/BaseLink.stl"}


def test_load_mujoco_model_loads_default_scene() -> None:
    bundle = load_mujoco_model(
        FAST_ARM_ROBOT_PROFILE.mujoco_model_asset,
        initial_keyframe_name=FAST_ARM_ROBOT_PROFILE.initial_keyframe_name,
    )

    assert bundle.model_path.as_posix() == "assets/mujoco/fast_arm/scene.xml"
    assert bundle.model is not None
    assert bundle.data is not None
    assert tuple(bundle.data.qpos) == pytest.approx(
        tuple(bundle.model.key(FAST_ARM_ROBOT_PROFILE.initial_keyframe_name).qpos)
    )


def test_generic_model_loader_does_not_infer_keyframe_from_model_path() -> None:
    bundle = load_mujoco_model(FAST_ARM_ROBOT_PROFILE.mujoco_model_asset)

    assert tuple(bundle.data.qpos) != pytest.approx(
        tuple(bundle.model.key(FAST_ARM_ROBOT_PROFILE.initial_keyframe_name).qpos)
    )


def test_load_mujoco_model_raises_for_missing_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing_scene.xml"

    with pytest.raises(FileNotFoundError):
        load_mujoco_model(missing_path)
