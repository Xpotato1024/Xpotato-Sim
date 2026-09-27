from __future__ import annotations

from pathlib import Path

import pytest

import xpotato_sim.plugins.robots.fast_arm.adapter.feasibility as joint_limits_module
from xpotato_sim.mujoco_backend.model_info import MuJoCoModelInfo
from xpotato_sim.plugins.robots.fast_arm.adapter.feasibility import (
    apply_fast_arm_qpos_feasibility_guard,
    load_and_validate_fast_arm_joint_limit_config,
    parse_fast_arm_joint_limit_config,
    validate_fast_arm_joint_limit_config,
)
from xpotato_sim.plugins.robots.fast_arm.adapter.resources import FAST_ARM_JOINT_LIMIT_RESOURCE
from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator
from xpotato_sim.runtime.composition.robot_resource import read_package_resource_bytes
from xpotato_sim.schemas import JointCommand, MotionCommand


DEFAULT_CONFIG_PATH = FAST_ARM_JOINT_LIMIT_RESOURCE
DEFAULT_CONFIG_TEXT = read_package_resource_bytes(DEFAULT_CONFIG_PATH).decode("utf-8")
HOME_QPOS = (0.0, -0.5235987755982989, 0.0, -1.0471975511965976)


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "joint_limits.toml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_home_qpos_is_checked_against_the_loaded_config(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        DEFAULT_CONFIG_TEXT.replace(
            "[joints.elbow_joint]\nlower_rad = -3.141592653589793\nupper_rad = 3.141592653589793",
            "[joints.elbow_joint]\nlower_rad = -0.5\nupper_rad = 0.5",
        ),
    )
    simulator = build_fast_arm_simulator()

    with pytest.raises(ValueError, match="home qpos"):
        load_and_validate_fast_arm_joint_limit_config(path, model=simulator.model)


def test_model_joint_order_is_checked_before_home_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    config = parse_fast_arm_joint_limit_config(DEFAULT_CONFIG_PATH)
    monkeypatch.setattr(
        joint_limits_module,
        "inspect_mujoco_model",
        lambda _model: MuJoCoModelInfo(
            joint_names=("elbow_joint", "sholder_joint_1", "sholder_joint_2", "sholder_joint_3"),
            body_names=(),
            site_names=(),
        ),
    )

    with pytest.raises(ValueError, match="joint order"):
        validate_fast_arm_joint_limit_config(config, object())


def _command(qpos_rad: tuple[float, ...]) -> MotionCommand:
    return MotionCommand(
        timestamp_s=1.0,
        joint=JointCommand(joint_angles_rad=qpos_rad),
        metadata={"desired_endpoint_m": (0.2, 0.0, 0.3)},
    )


def test_boundary_values_are_accepted_without_metadata_rewrite() -> None:
    config = parse_fast_arm_joint_limit_config(DEFAULT_CONFIG_PATH)
    command = _command((-3.141592653589793, 3.141592653589793, 0.0, -3.141592653589793))

    result = apply_fast_arm_qpos_feasibility_guard(
        command,
        current_qpos_rad=HOME_QPOS,
        joint_limits=config,
    )

    assert result.accepted is True
    assert result.action == "accept"
    assert result.motion_command is command
    assert result.violations == ()


def test_single_and_multiple_violations_hold_the_entire_current_qpos() -> None:
    config = parse_fast_arm_joint_limit_config(DEFAULT_CONFIG_PATH)
    command = _command((3.2, HOME_QPOS[1], HOME_QPOS[2], -3.2))

    result = apply_fast_arm_qpos_feasibility_guard(
        command,
        current_qpos_rad=HOME_QPOS,
        joint_limits=config,
    )

    assert result.accepted is False
    assert result.action == "hold_current_qpos"
    assert result.motion_command.joint is not None
    assert result.motion_command.joint.joint_angles_rad == HOME_QPOS
    assert result.motion_command.target is None
    assert [violation.joint_name for violation in result.violations] == [
        "sholder_joint_1",
        "elbow_joint",
    ]
    assert result.motion_command.metadata["qpos_feasibility_action"] == "hold_current_qpos"
    assert result.motion_command.metadata["qpos_rejection_reason"] == "joint_limit_violation"
    assert result.motion_command.metadata["qpos_candidate_rad"] == (3.2, HOME_QPOS[1], HOME_QPOS[2], -3.2)
    assert result.motion_command.metadata["qpos_limit_violations"][0]["joint_name"] == "sholder_joint_1"
