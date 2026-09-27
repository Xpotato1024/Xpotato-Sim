from __future__ import annotations

import asyncio

from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source


class RecordingPublisher:
    def __init__(self) -> None:
        self.states = []

    async def publish(self, state) -> None:
        self.states.append(state)


def test_programmed_target_source_reflected_in_step_loop_and_endpoint_evaluation() -> None:
    selection = select_runtime_input_source("programmed_target", steps=2)
    publisher = RecordingPublisher()
    plan = build_runtime_input_source_step_loop_plan(selection, publisher=publisher)

    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=2, dt_s=1.0 / 60.0))

    assert len(records) == 2
    assert records[0].frame.metadata["source_kind"] == "programmed_target"
    assert records[0].motion_command.metadata["source_kind"] == "programmed_target"
    assert records[0].state.target_position_m == records[0].motion_command.metadata["desired_endpoint_m"]
    assert publisher.states[0].target_position_m == records[0].motion_command.metadata["desired_endpoint_m"]
    assert publisher.states[0].metadata["endpoint_evaluation"]["desired_endpoint_m"] == list(
        records[0].motion_command.metadata["desired_endpoint_m"]
    )
    assert publisher.states[0].metadata["endpoint_evaluation"]["fk_endpoint_coordinate_frame"] == "solver-defined frame"
    assert len(publisher.states[0].metadata["endpoint_evaluation"]["desired_to_site_error_vector_m"]) == 3
    assert len(publisher.states[0].metadata["endpoint_evaluation"]["fk_to_site_error_vector_m"]) == 3


def test_programmed_input_progression_is_preserved_through_the_loop() -> None:
    selection = select_runtime_input_source("programmed_target", steps=4)
    publisher = RecordingPublisher()
    plan = build_runtime_input_source_step_loop_plan(selection, publisher=publisher)

    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=4, dt_s=1.0 / 60.0))

    assert [record.frame.metadata["frame_index"] for record in records] == [0, 1, 2, 3]
    assert [record.frame.metadata["phase"] for record in records] == [
        "initial_hold",
        "initial_hold",
        "initial_hold",
        "move_positive_x",
    ]
    assert records[0].motion_command.metadata["desired_endpoint_m"] == records[1].motion_command.metadata["desired_endpoint_m"]
    assert records[1].motion_command.metadata["desired_endpoint_m"] == records[2].motion_command.metadata["desired_endpoint_m"]
    assert records[3].motion_command.metadata["desired_endpoint_m"] != records[0].motion_command.metadata["desired_endpoint_m"]


def test_replay_smoke_keeps_endpoint_evaluation_optional() -> None:
    selection = select_runtime_input_source("replay", steps=1)
    publisher = RecordingPublisher()
    plan = build_runtime_input_source_step_loop_plan(selection, publisher=publisher)

    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=1.0 / 60.0))

    assert len(records) == 1
    assert records[0].motion_command.joint is not None
    assert records[0].motion_command.joint.joint_angles_rad == (
        plan.pipeline.simulator.last_joint_position_command.joint_angles_rad
    )
    assert records[0].state.target_position_m is None
    assert records[0].state.metadata["desired_endpoint_m"] == (0.6, 0.0, 0.1)
    assert "endpoint_evaluation" not in publisher.states[0].metadata
