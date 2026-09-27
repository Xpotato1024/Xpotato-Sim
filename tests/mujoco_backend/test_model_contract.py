from __future__ import annotations

import pytest

from xpotato_sim.mujoco_backend.model_info import MuJoCoModelInfo
from xpotato_sim.plugins.robots.fast_arm.adapter import model_contract as model_contract_module
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator
from xpotato_sim.plugins.robots.fast_arm.adapter.model_contract import (
    fast_arm_model_name_contract,
    resolve_fast_arm_end_effector_reference,
    resolve_fast_arm_tip_reference,
    resolve_fast_arm_wrist_reference,
    validate_fast_arm_model_name_contract,
)


def test_fast_arm_model_name_contract_matches_default_scene() -> None:
    simulator = build_fast_arm_simulator()
    contract = validate_fast_arm_model_name_contract(simulator.model)

    assert contract == fast_arm_model_name_contract()
    assert contract.canonical_model_name == "fast_arm"
    assert contract.end_effector_site_name == "tip"
    assert contract.end_effector_body_name == "fore_arm_link"
    assert contract.wrist_site_name is None
    assert contract.wrist_body_name == "fore_arm_link"
    assert contract.tip_site_name == "tip"
    assert contract.tip_body_name == "fore_arm_link"
    assert contract.position_unit == "meter"
    assert contract.coordinate_frame == "MuJoCo world / scene frame"
    assert contract.arm_body_names == (
        "base_link",
        "sholder_link_1",
        "sholder_link_2",
        "upper_arm_link",
        "fore_arm_link",
    )

    assert resolve_fast_arm_end_effector_reference(simulator.model) == model_contract_module.ResolvedModelReference(
        role="end_effector",
        kind="site",
        name="tip",
    )
    assert resolve_fast_arm_tip_reference(simulator.model) == model_contract_module.ResolvedModelReference(
        role="tip",
        kind="site",
        name="tip",
    )
    assert resolve_fast_arm_wrist_reference(simulator.model) == model_contract_module.ResolvedModelReference(
        role="wrist",
        kind="body",
        name="fore_arm_link",
    )


def test_fast_arm_model_name_contract_rejects_missing_tip_site_and_allows_explicit_body_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = MuJoCoModelInfo(
        joint_names=("sholder_joint_1", "sholder_joint_2", "sholder_joint_3", "elbow_joint"),
        body_names=(
            "world",
            "origin",
            "base",
            "base_link",
            "sholder_link_1",
            "sholder_link_2",
            "upper_arm_link",
            "fore_arm_link",
        ),
        site_names=(),
    )
    monkeypatch.setattr(model_contract_module, "inspect_mujoco_model", lambda model: info)

    with pytest.raises(ValueError, match="missing site name 'tip'.*end_effector / tip"):
        validate_fast_arm_model_name_contract(object())

    resolved = resolve_fast_arm_end_effector_reference(object(), allow_body_fallback=True)
    assert resolved == model_contract_module.ResolvedModelReference(
        role="end_effector",
        kind="body",
        name="fore_arm_link",
    )


def test_fast_arm_model_name_contract_rejects_missing_wrist_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = MuJoCoModelInfo(
        joint_names=("sholder_joint_1", "sholder_joint_2", "sholder_joint_3", "elbow_joint"),
        body_names=(
            "world",
            "origin",
            "base",
            "base_link",
            "sholder_link_1",
            "sholder_link_2",
            "upper_arm_link",
        ),
        site_names=("tip",),
    )
    monkeypatch.setattr(model_contract_module, "inspect_mujoco_model", lambda model: info)

    with pytest.raises(ValueError, match="missing body name 'fore_arm_link'.*arm / wrist / tip"):
        validate_fast_arm_model_name_contract(object())


def test_fast_arm_tip_reference_rejects_missing_body_when_explicit_fallback_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = MuJoCoModelInfo(
        joint_names=("sholder_joint_1", "sholder_joint_2", "sholder_joint_3", "elbow_joint"),
        body_names=(
            "world",
            "origin",
            "base",
            "base_link",
            "sholder_link_1",
            "sholder_link_2",
            "upper_arm_link",
        ),
        site_names=(),
    )
    monkeypatch.setattr(model_contract_module, "inspect_mujoco_model", lambda model: info)

    with pytest.raises(ValueError, match="missing body name 'fore_arm_link'.*tip fallback"):
        resolve_fast_arm_tip_reference(object(), allow_body_fallback=True)
