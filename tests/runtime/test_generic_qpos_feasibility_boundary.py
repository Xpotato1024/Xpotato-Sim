from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from xpotato_sim.mujoco_backend import HeadlessMuJoCoSimulator
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.execution.pipeline import ControlMappedRuntimePipeline
from xpotato_sim.runtime.composition.replay_mujoco_pipeline import build_replay_mujoco_pipeline
from xpotato_sim.runtime.execution.input_step_loop import build_runtime_input_source_step_loop_plan
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from tests.support.mapped_pipeline_builders import build_test_mujoco_pipeline
from xpotato_sim.plugins.robots.fast_arm.adapter.feasibility import FastArmJointLimitGuard
from xpotato_sim.runtime.safety.qpos_feasibility import NoOpQposFeasibilityGuard, QposFeasibilityGuard
from xpotato_sim.schemas import (
    MotionCommand,
    MuJoCoState,
    RawInputFrame,
)
from generic_qpos_test_doubles import RejectingGenericQposGuard


MINIMAL_MODEL = """\
<mujoco model="generic_minimal">
  <option gravity="0 0 0"/>
  <worldbody>
    <body name="generic_body">
      <joint name="generic_joint" type="hinge" axis="0 0 1"/>
      <geom type="sphere" size="0.01" density="1000"/>
    </body>
  </worldbody>
  <keyframe>
    <key name="home" qpos="0"/>
  </keyframe>
</mujoco>
"""


def _write_minimal_model(tmp_path: Path) -> Path:
    path = tmp_path / "generic_minimal.xml"
    path.write_text(MINIMAL_MODEL, encoding="utf-8", newline="\n")
    return path


def test_generic_pipeline_accepts_non_fast_arm_model_without_fast_arm_config(tmp_path: Path) -> None:
    model_path = _write_minimal_model(tmp_path)
    config = RuntimeConfig(joint_limit_config_path=tmp_path / "missing-fast-arm-limits.toml")

    pipeline = build_test_mujoco_pipeline(model_path=model_path, config=config)

    assert isinstance(pipeline, ControlMappedRuntimePipeline)
    assert isinstance(pipeline.simulator, HeadlessMuJoCoSimulator)
    assert pipeline.qpos_feasibility_guard is None
    state = asyncio.run(pipeline.run_once())

    assert isinstance(state, MuJoCoState)
    assert state.qpos == (0.0,)


def test_production_replay_pipeline_rejects_foreign_model_before_return(
    tmp_path: Path,
) -> None:
    model_path = _write_minimal_model(tmp_path)
    with pytest.raises(ValueError, match="qpos dimension mismatch"):
        build_replay_mujoco_pipeline(
            model_path=model_path,
            config=RuntimeConfig(robot_profile_id="fast_arm"),
            frames=(RawInputFrame(source="replay", timestamp_s=0.0),),
            robot_bundle=resolve_robot_bundle("fast_arm"),
        )


def test_fast_arm_replay_production_step_loop_uses_bundle_owned_guard() -> None:
    plan = build_runtime_input_source_step_loop_plan(
        select_runtime_input_source("replay", steps=1),
    )

    assert isinstance(plan.pipeline.qpos_feasibility_guard, FastArmJointLimitGuard)


def test_fast_arm_production_composition_rejects_non_fast_arm_model(tmp_path: Path) -> None:
    model_path = _write_minimal_model(tmp_path)

    from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline

    try:
        build_concrete_mujoco_pipeline(model_path=model_path, publisher=_RecordingPublisher())
    except ValueError as exc:
        assert (
            "qpos dimension mismatch" in str(exc)
            or "joint order" in str(exc)
            or "missing" in str(exc)
        )
    else:
        raise AssertionError("fast_arm production composition accepted a non-fast-arm model")


def test_generic_feasibility_contract_has_explicit_no_guard_behavior() -> None:
    guard = NoOpQposFeasibilityGuard()

    assert isinstance(guard, QposFeasibilityGuard)
    result = guard.evaluate(
        MotionCommand(timestamp_s=0.0),
        current_qpos_rad=(0.0,),
    )

    assert result.accepted is True
    assert result.action == "accept_no_qpos_candidate"
    assert result.diagnostics == ()


def test_runtime_pipeline_uses_typed_rejection_without_fast_arm_metadata() -> None:
    from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline

    pipeline = build_concrete_mujoco_pipeline(publisher=_RecordingPublisher())
    pipeline.qpos_feasibility_guard = RejectingGenericQposGuard()
    initial_qpos = pipeline.simulator.snapshot().qpos

    state = asyncio.run(pipeline.run_once())

    assert state.qpos == initial_qpos
    assert state.metadata["endpoint_evaluation"] is None
    assert pipeline.simulator.last_command is not None
    assert "qpos_feasibility_rejected" not in pipeline.simulator.last_command.metadata
    assert "qpos_rejection_reason" not in pipeline.simulator.last_command.metadata


def test_generic_runtime_package_root_excludes_fast_arm_implementation_details() -> None:
    import xpotato_sim.runtime as runtime

    assert not hasattr(runtime, "FastArmJointLimitConfig")
    assert not hasattr(runtime, "FastArmJointLimitViolation")
    assert not hasattr(runtime, "FastArmQposFeasibilityResult")
    assert not hasattr(runtime, "QposFeasibilityGuard")


def test_generic_runtime_modules_do_not_import_fast_arm_limit_implementation() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "xpotato_sim" / "runtime"
    for relative_path in (
        "execution/pipeline.py",
        "safety/input_safety.py",
        "composition/replay_mujoco_pipeline.py",
    ):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "fast_arm_joint_limits" not in source


def test_runtime_reject_control_flow_does_not_read_fast_arm_rejection_metadata() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "xpotato_sim" / "runtime"
    for relative_path in (
        "execution/pipeline.py",
        "runners/dry_run.py",
        "runners/websocket_publisher.py",
        "control/input_step_diagnostics.py",
    ):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert 'metadata.get("qpos_feasibility_rejected"' not in source


class _RecordingPublisher:
    async def publish(self, state) -> None:  # noqa: ANN001
        _ = state
