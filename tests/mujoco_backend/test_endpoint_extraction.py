from __future__ import annotations

from types import SimpleNamespace

import pytest

from xpotato_sim.mujoco_backend import endpoint_extraction as generic_endpoint_extraction
from xpotato_sim.mujoco_backend import (
    ResolvedModelReference,
    RuntimeMuJoCoEndpointEvaluation,
    RuntimeMuJoCoSiteEndpointEvaluation,
    load_mujoco_model,
    snapshot_mujoco_state,
)
from xpotato_sim.mujoco_backend.model_info import MuJoCoModelInfo
from xpotato_sim.plugins.robots.fast_arm.adapter import endpoint as endpoint_extraction_module
from xpotato_sim.plugins.robots.fast_arm.adapter import model_contract as model_contract_module
from xpotato_sim.plugins.robots.fast_arm.adapter.kinematics import FastArmMuJoCoModelForwardKinematicsSolver
from xpotato_sim.plugins.robots.fast_arm.adapter.profile import FAST_ARM_ROBOT_PROFILE


def _fake_mujoco(*, body_id: int = 0, site_id: int = 0) -> object:
    def mj_forward(model: object, data: object) -> None:
        return None

    def mj_name2id(model: object, obj_type: object, name: str) -> int:
        if obj_type == fake_mujoco_module.mjtObj.mjOBJ_SITE and name == "tip":
            return site_id
        if obj_type == fake_mujoco_module.mjtObj.mjOBJ_BODY and name == "fore_arm_link":
            return body_id
        return -1

    fake_mujoco_module = SimpleNamespace(
        mjtObj=SimpleNamespace(mjOBJ_SITE=1, mjOBJ_BODY=2),
        mj_forward=mj_forward,
        mj_name2id=mj_name2id,
    )
    return fake_mujoco_module


def test_runtime_mujoco_endpoint_evaluation_has_documented_site_name_alias() -> None:
    assert RuntimeMuJoCoSiteEndpointEvaluation is RuntimeMuJoCoEndpointEvaluation


def test_resolved_model_reference_rejects_invalid_kind() -> None:
    with pytest.raises(
        ValueError,
        match="unsupported MuJoCo model reference kind: 'geom'",
    ):
        ResolvedModelReference(role="endpoint", kind="geom", name="tool")


def test_extract_fast_arm_tip_site_endpoint_from_model_data_returns_tip_site_world_position() -> None:
    bundle = load_mujoco_model(FAST_ARM_ROBOT_PROFILE.mujoco_model_asset)

    evaluation = endpoint_extraction_module.extract_fast_arm_tip_site_endpoint(
        bundle.model,
        bundle.data,
    )

    assert evaluation.role == "tip"
    assert evaluation.kind == "site"
    assert evaluation.name == "tip"
    assert evaluation.position_m == pytest.approx(
        FastArmMuJoCoModelForwardKinematicsSolver().forward(tuple(bundle.data.qpos)),
        abs=1e-9,
    )
    assert evaluation.unit == "meter"
    assert evaluation.coordinate_frame == "MuJoCo world / scene frame"


def test_extract_fast_arm_tip_site_endpoint_from_model_data_requires_explicit_body_fallback(
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

    with pytest.raises(ValueError, match="missing site name 'tip'.*tip"):
        endpoint_extraction_module.extract_fast_arm_tip_site_endpoint(object(), object())


def test_extract_fast_arm_tip_site_endpoint_from_model_data_uses_explicit_body_fallback(
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
    monkeypatch.setattr(generic_endpoint_extraction, "_import_mujoco", lambda: _fake_mujoco(body_id=3))

    model = object()
    data = SimpleNamespace(
        site_xpos=(),
        xpos=((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.338, 0.0, 0.7)),
    )

    evaluation = endpoint_extraction_module.extract_fast_arm_tip_site_endpoint(
        model,
        data,
        allow_body_fallback=True,
    )

    assert evaluation.role == "tip"
    assert evaluation.kind == "body"
    assert evaluation.name == "fore_arm_link"
    assert evaluation.position_m == pytest.approx((0.338, 0.0, 0.7), abs=1e-9)
    assert evaluation.unit == "meter"
    assert evaluation.coordinate_frame == "MuJoCo world / scene frame"


def test_extract_fast_arm_tip_site_endpoint_from_model_data_raises_when_fallback_body_is_missing(
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
    monkeypatch.setattr(generic_endpoint_extraction, "_import_mujoco", lambda: _fake_mujoco(body_id=-1))

    model = object()
    data = SimpleNamespace(site_xpos=(), xpos=())

    with pytest.raises(ValueError, match="missing body name 'fore_arm_link'.*tip"):
        endpoint_extraction_module.extract_fast_arm_tip_site_endpoint(
            model,
            data,
            allow_body_fallback=True,
        )


def test_extract_fast_arm_tip_site_endpoint_from_state_reuses_snapshot_transforms() -> None:
    bundle = load_mujoco_model(FAST_ARM_ROBOT_PROFILE.mujoco_model_asset)
    state = snapshot_mujoco_state(
        bundle.model,
        bundle.data,
        frame_index=1,
    )

    evaluation = endpoint_extraction_module.extract_fast_arm_tip_site_endpoint_from_state(state)

    assert evaluation.role == "tip"
    assert evaluation.kind == "site"
    assert evaluation.name == "tip"
    assert evaluation.position_m == pytest.approx(
        FastArmMuJoCoModelForwardKinematicsSolver().forward(state.qpos),
        abs=1e-9,
    )
