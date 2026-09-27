from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.runtime import build_fast_arm_simulator

from dataclasses import replace
import asyncio

import pytest

from xpotato_sim.runtime.safety.input_safety import (
    DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS,
    build_runtime_input_safety_result,
)
from xpotato_sim.runtime.control.input_source_selection import (
    RuntimeInputSourceSelection,
    select_runtime_input_source,
)
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.control.input_source_state import build_runtime_input_source_state
from xpotato_sim.runtime.experiment.input_source import (
    InputSourceHealth,
    InputSourceHealthStatus,
)
from xpotato_sim.schemas import MotionCommand
from xpotato_sim.transport import mujoco_state_to_payload


class RecordingPublisher:
    def __init__(self) -> None:
        self.states = []

    async def publish(self, state) -> None:
        self.states.append(state)


def _build_programmed_target_selection(*, steps: int = 1) -> RuntimeInputSourceSelection:
    return select_runtime_input_source("programmed_target", steps=steps)


def test_source_provided_safety_semantics_are_deterministic() -> None:
    fresh_state = build_runtime_input_source_state("replay", source_active=True, command_age_ms=0)
    stale_by_age_state = build_runtime_input_source_state(
        "replay",
        source_active=True,
        command_age_ms=DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS + 1,
    )
    stale_by_source_state = build_runtime_input_source_state("replay", source_active=False, command_age_ms=0)
    stale_by_reason_state = build_runtime_input_source_state("replay", source_active=True, command_age_ms=0, stale_reason="explicit_stale")
    command = MotionCommand(timestamp_s=1.0, metadata={"source_kind": "replay"})

    fresh_result = build_runtime_input_safety_result(command, source_state=fresh_state)
    stale_by_age_result = build_runtime_input_safety_result(command, source_state=stale_by_age_state)
    stale_by_source_result = build_runtime_input_safety_result(command, source_state=stale_by_source_state)
    stale_by_reason_result = build_runtime_input_safety_result(command, source_state=stale_by_reason_state)

    assert fresh_result.is_stale is False
    assert fresh_result.should_update_target_position_m is True
    assert fresh_result.stale_reason is None
    assert fresh_result.command_age_ms == 0

    assert stale_by_age_result.is_stale is True
    assert stale_by_age_result.should_update_target_position_m is False
    assert stale_by_age_result.stale_reason == f"command_age_ms_exceeded_timeout_{DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS}"
    assert stale_by_age_result.command_age_ms == DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS + 1

    assert stale_by_source_result.is_stale is True
    assert stale_by_source_result.should_update_target_position_m is False
    assert stale_by_source_result.stale_reason == "source_inactive"
    assert stale_by_source_result.command_age_ms == 0

    assert stale_by_reason_result.is_stale is True
    assert stale_by_reason_result.should_update_target_position_m is False
    assert stale_by_reason_result.stale_reason == "explicit_stale"
    assert stale_by_reason_result.command_age_ms == 0


def test_fresh_runtime_input_safety_result_leaves_motion_command_unchanged() -> None:
    command = MotionCommand(
        timestamp_s=1.0,
        metadata={
            "source_kind": "replay",
            "desired_endpoint_m": (0.4, 0.0, 0.6),
        },
    )
    source_state = build_runtime_input_source_state(
        "replay",
        source_active=True,
        command_age_ms=0,
    )

    result = build_runtime_input_safety_result(command, source_state=source_state)

    assert result.is_stale is False
    assert result.should_update_target_position_m is True
    assert result.stale_reason is None
    assert result.command_age_ms == 0
    assert result.motion_command is command


def test_stale_runtime_input_safety_result_holds_current_qpos_deterministically() -> None:
    simulator = build_fast_arm_simulator()
    current_state = simulator.snapshot()
    command = MotionCommand(
        timestamp_s=1.0,
        metadata={
            "source_kind": "replay",
            "desired_endpoint_m": (0.7, 0.0, 0.6),
            "target_position_m": (0.7, 0.0, 0.6),
        },
    )
    source_state = build_runtime_input_source_state(
        "replay",
        source_active=True,
        command_age_ms=DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS + 1,
    )

    result = build_runtime_input_safety_result(
        command,
        source_state=source_state,
        current_state=current_state,
    )

    assert result.is_stale is True
    assert result.should_update_target_position_m is False
    assert result.stale_reason == f"command_age_ms_exceeded_timeout_{DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS}"
    assert result.motion_command is not command
    assert result.motion_command.target is None
    assert result.motion_command.joint is not None
    assert result.motion_command.joint.joint_angles_rad == pytest.approx(current_state.qpos)
    assert "desired_endpoint_m" not in result.motion_command.metadata
    assert "target_position_m" not in result.motion_command.metadata
    assert result.motion_command.metadata["source_kind"] == "replay"
    assert result.motion_command.metadata["command_age_ms"] == DEFAULT_RUNTIME_INPUT_COMMAND_TIMEOUT_MS + 1
    assert result.motion_command.metadata["stale_reason"] == result.stale_reason
    assert result.motion_command.metadata["runtime_input_safety_applied"] is True


def test_fresh_programmed_target_updates_target_marker_and_endpoint_evaluation() -> None:
    selection = _build_programmed_target_selection(steps=1)
    publisher = RecordingPublisher()
    plan = build_runtime_input_source_step_loop_plan(selection, publisher=publisher)

    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=1.0 / 60.0))

    desired_endpoint_m = records[0].motion_command.metadata["desired_endpoint_m"]

    assert len(records) == 1
    assert records[0].motion_command.joint is not None
    assert records[0].state.target_position_m == desired_endpoint_m
    assert publisher.states[0].target_position_m == desired_endpoint_m
    assert "runtime_input_safety_applied" not in records[0].state.metadata
    assert records[0].state.metadata["desired_endpoint_m"] == desired_endpoint_m
    assert records[0].state.metadata["target_position_m"] == desired_endpoint_m
    assert publisher.states[0].metadata["endpoint_evaluation"]["desired_endpoint_m"] == list(desired_endpoint_m)
    assert publisher.states[0].metadata["endpoint_evaluation"]["fk_endpoint_coordinate_frame"] == "solver-defined frame"
    assert len(publisher.states[0].metadata["endpoint_evaluation"]["desired_to_site_error_vector_m"]) == 3
    assert len(publisher.states[0].metadata["endpoint_evaluation"]["fk_to_site_error_vector_m"]) == 3


def test_stale_programmed_target_does_not_update_target_marker_or_endpoint_evaluation() -> None:
    selection = _build_programmed_target_selection(steps=1)
    stale_frame = replace(
        selection.frames[0],
        metadata={
            **selection.frames[0].metadata,
            "source_active": False,
            "command_age_ms": 0,
        },
    )
    stale_selection = replace(
        selection,
        frames=(stale_frame,),
        initial_metadata=dict(stale_frame.metadata),
        runtime_reader=type(
            "StaleFrameReader",
            (),
            {
                "read_frame": lambda self: stale_frame,
                "current_health": lambda self: InputSourceHealth(
                    InputSourceHealthStatus.INACTIVE,
                    age_ms=0,
                ),
            },
        )(),
    )
    publisher = RecordingPublisher()
    plan = build_runtime_input_source_step_loop_plan(stale_selection, publisher=publisher)

    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=1.0 / 60.0))

    stale_desired_endpoint_m = stale_frame.metadata["desired_endpoint_m"]

    assert len(records) == 1
    assert records[0].motion_command.target is None
    assert records[0].motion_command.joint is not None
    assert records[0].motion_command.metadata["stale_reason"] == "source_inactive"
    assert records[0].motion_command.metadata["runtime_input_safety_applied"] is True
    assert plan.pipeline.simulator.last_joint_position_command is not None
    assert (
        plan.pipeline.simulator.last_joint_position_command.joint_angles_rad
        == pytest.approx(records[0].motion_command.joint.joint_angles_rad)
    )
    assert "desired_endpoint_m" not in records[0].motion_command.metadata
    assert "target_position_m" not in records[0].motion_command.metadata
    assert records[0].state.target_position_m != stale_desired_endpoint_m
    assert records[0].state.metadata["source_kind"] == "programmed_target"
    assert records[0].state.metadata["source_active"] is False
    assert records[0].state.metadata["command_age_ms"] == 0
    assert records[0].state.metadata["stale_reason"] == "source_inactive"
    assert records[0].state.metadata["runtime_input_safety_applied"] is True
    assert "desired_endpoint_m" not in records[0].state.metadata
    assert "target_position_m" not in records[0].state.metadata
    assert records[0].state.metadata["endpoint_evaluation"] is None
    assert "endpoint_evaluation" not in mujoco_state_to_payload(publisher.states[0])
