from __future__ import annotations

from xpotato_sim.plugins.robots.fast_arm.adapter.endpoint import extract_fast_arm_tip_site_endpoint_from_state

import asyncio
from math import dist

import pytest

from xpotato_sim.plugins.input_sources.viewer import ViewerInputSource
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.control.viewer_control_ingress import ingest_viewer_control_message
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.schemas import (
    JointPositionCommand,
    ViewerControlGamepadButtonMessage,
    ViewerControlGamepadMessage,
    ViewerControlKeyboardMessage,
    ViewerControlMessage,
)
from xpotato_sim.runtime.experiment.contracts import PluginSelection
from tests.support.transport_doubles import NoOpStatePublisher


class _ClockSequence:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._values = iter(values)

    def monotonic(self) -> float:
        return next(self._values)


def _keyboard_message(timestamp_s: float, *key_codes: str) -> ViewerControlMessage:
    key_state = {key_code: True for key_code in key_codes}
    return ViewerControlMessage(
        type="viewer_control_message",
        timestamp_s=timestamp_s,
        source_kind="keyboard",
        keyboard=ViewerControlKeyboardMessage(
            active_key_codes=key_codes,
            key_state=key_state,
            focus_state="focused",
            zero_state=False,
        ),
    )


def _run_single_viewer_step(plan, *, dt_s: float) -> object:
    return asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=dt_s))[0]


def _legacy_frontend_gamepad_projection(value: float) -> float:
    clamped = max(-1.0, min(1.0, value))
    magnitude = abs(clamped)
    if magnitude <= 0.1:
        return 0.0
    return (1.0 if clamped > 0.0 else -1.0) * ((magnitude - 0.1) / 0.9)


def _build_viewer_plan(clock: _ClockSequence, *, steps: int = 1):
    source = ViewerInputSource(clock=clock.monotonic)
    plan = build_runtime_input_source_step_loop_plan(
        select_runtime_input_source("viewer", steps=steps),
        publisher=NoOpStatePublisher(),
        viewer_clock=clock.monotonic,
        viewer_input_source=source,
    )
    return source, plan


def test_viewer_plan_keeps_the_resolved_typed_command_binding() -> None:
    _, plan = _build_viewer_plan(_ClockSequence((0.0,)))

    assert plan.command_execution is plan.pipeline.command_execution
    assert plan.command_execution.command_type is JointPositionCommand
    assert (
        plan.command_execution.route_identity
        == plan.command_semantics_route.identity
    )


def test_viewer_step_loop_accepts_continuous_keyboard_motion_with_small_bounded_qpos_delta() -> None:
    clock = _ClockSequence((0.0, 0.0))
    source, plan = _build_viewer_plan(clock)
    initial_state = plan.pipeline.simulator.snapshot()
    initial_tip_site_position_m = extract_fast_arm_tip_site_endpoint_from_state(initial_state).position_m

    ingest_viewer_control_message(source, _keyboard_message(1.0, "KeyD"))
    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.frame.metadata["intent_kind"] == "local_endpoint_velocity"
    assert record.frame.metadata["input_continuity"] == "continuous"
    assert record.frame.metadata["control_frame"] == "world"
    assert record.frame.metadata["axis_values"] == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)
    assert record.frame.metadata["endpoint_velocity_m_s"] == pytest.approx((0.1, 0.0, 0.0), abs=1e-12)
    assert record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"] == pytest.approx((0.1, 0.0, 0.0), abs=1e-12)
    assert record.motion_command.metadata["endpoint_delta_m"] == pytest.approx((1.0 / 600.0, 0.0, 0.0), abs=1e-12)
    assert record.motion_command.metadata["endpoint_delta_requested_m"] == pytest.approx((1.0 / 600.0, 0.0, 0.0), abs=1e-12)
    assert record.motion_command.metadata["local_motion_policy"] == "finite_difference_jacobian"
    assert record.motion_command.metadata["motion_status"] in {"accepted", "scaled"}
    assert record.motion_command.metadata["qpos_delta_norm_rad"] <= 0.2 + 1e-12
    assert record.motion_command.metadata["endpoint_model"] == "mujoco_model_aligned_tip_site"
    assert record.state.metadata["endpoint_model"] == "mujoco_model_aligned_tip_site"
    assert record.motion_command.metadata["endpoint_velocity_m_s"][0] > 0.0
    assert record.state.metadata["actual_tip_delta_m"][0] > 0.0
    assert record.state.metadata["endpoint_progress_status"] == "progressing"
    assert record.state.metadata["motion_status"] == record.motion_command.metadata["motion_status"]
    post_step_tip_site_position_m = extract_fast_arm_tip_site_endpoint_from_state(record.state).position_m
    expected_actual_tip_delta_m = tuple(
        post_step_tip_site_position_m[index] - initial_tip_site_position_m[index]
        for index in range(3)
    )
    assert record.state.metadata["actual_tip_delta_m"] == pytest.approx(
        expected_actual_tip_delta_m,
        abs=0.0,
    )
    assert dist(initial_state.qpos[:4], record.state.qpos[:4]) > 0.0
    assert dist(initial_state.qpos[:4], record.state.qpos[:4]) <= 0.2 + 1e-12
    assert record.state.target_position_m is not None
    assert source.current_endpoint_m == pytest.approx(record.state.target_position_m, abs=1e-12)
    assert record.frame.metadata["current_tip_position_m"] == pytest.approx(initial_tip_site_position_m, abs=1e-12)


def test_actual_provider_raw_gamepad_axis_reaches_custom_mapping_and_motion_state() -> None:
    clock = _ClockSequence((0.0, 0.0))
    selection = select_runtime_input_source(
        "viewer",
        steps=1,
        control_mapping_selection=PluginSelection("viewer_keyboard_gamepad_mapping", 1),
        control_mapping_parameters={
            "gamepad_speed_m_s": 0.1,
            "gamepad_deadzone": 0.0,
            "gamepad_max_delta_m": 0.03,
        },
    )
    source = ViewerInputSource(clock=clock.monotonic)
    plan = build_runtime_input_source_step_loop_plan(
        selection,
        viewer_clock=clock.monotonic,
        viewer_input_source=source,
        publisher=NoOpStatePublisher(),
    )
    ingest_viewer_control_message(
        source,
        ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=1.0,
            source_kind="gamepad",
            gamepad=ViewerControlGamepadMessage(
                connected=True,
                index=0,
                id="provider-pad",
                raw_axes=(0.15, 0.0, 0.0),
                axes=(_legacy_frontend_gamepad_projection(0.15), 0.0, 0.0),
                buttons=(ViewerControlGamepadButtonMessage(pressed=False, value=0.0),),
                stale=False,
                zero_state=False,
            ),
        ),
    )

    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.frame.metadata["source_active"] is True
    assert record.frame.metadata["viewer_input_sample"]["gamepad"]["raw_axes"] == (0.15, 0.0, 0.0)
    assert record.intent.values[0] == pytest.approx(1.0 / 18.0, abs=1e-12)
    assert record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"][0] == pytest.approx(
        1.0 / 180.0, abs=1e-12
    )
    assert record.motion_command.metadata["endpoint_delta_m"][0] > 0.0
    assert record.state.metadata["source_kind"] == "viewer_gamepad"
    assert record.state.metadata["actual_tip_delta_m"] != pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-12
    )


def test_actual_provider_custom_zero_deadzone_keeps_legacy_neutral_raw_axis_at_hold() -> None:
    clock = _ClockSequence((0.0, 0.0))
    selection = select_runtime_input_source(
        "viewer",
        steps=1,
        control_mapping_parameters={
            "gamepad_speed_m_s": 0.1,
            "gamepad_deadzone": 0.0,
            "gamepad_max_delta_m": 0.03,
        },
    )
    source = ViewerInputSource(clock=clock.monotonic)
    plan = build_runtime_input_source_step_loop_plan(
        selection,
        viewer_clock=clock.monotonic,
        viewer_input_source=source,
        publisher=NoOpStatePublisher(),
    )
    ingest_viewer_control_message(
        source,
        ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=1.0,
            source_kind="gamepad",
            gamepad=ViewerControlGamepadMessage(
                connected=True,
                index=0,
                id="neutral-pad",
                raw_axes=(0.05, 0.0, 0.0),
                axes=(0.0, 0.0, 0.0),
                buttons=(),
                stale=False,
                zero_state=True,
            ),
        ),
    )

    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.frame.metadata["source_active"] is False
    assert record.intent.values == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)
    assert record.motion_command.metadata["endpoint_delta_m"] == pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-12
    )
    assert record.state.metadata["actual_tip_delta_m"] == pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-12
    )


@pytest.mark.parametrize(
    ("raw_axis", "expected_axis"),
    ((0.15, 0.0), (0.19, 0.0), (0.2, 1.0 / 9.0)),
)
def test_actual_provider_default_gamepad_transfer_preserves_legacy_motion_boundary(
    raw_axis: float,
    expected_axis: float,
) -> None:
    clock = _ClockSequence((0.0, 0.0))
    source = ViewerInputSource(clock=clock.monotonic)
    plan = build_runtime_input_source_step_loop_plan(
        select_runtime_input_source("viewer", steps=1),
        publisher=NoOpStatePublisher(),
        viewer_input_source=source,
    )
    ingest_viewer_control_message(
        source,
        ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=1.0,
            source_kind="gamepad",
            gamepad=ViewerControlGamepadMessage(
                connected=True,
                index=0,
                id="default-pad",
                raw_axes=(raw_axis, 0.0, 0.0),
                axes=(_legacy_frontend_gamepad_projection(raw_axis), 0.0, 0.0),
                buttons=(),
                stale=False,
                zero_state=_legacy_frontend_gamepad_projection(raw_axis) == 0.0,
            ),
        ),
    )

    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.intent.values[0] == pytest.approx(expected_axis, abs=1e-12)
    assert record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"][0] == pytest.approx(
        expected_axis * 0.1, abs=1e-12
    )
    if expected_axis == 0.0:
        assert record.motion_command.metadata["endpoint_delta_m"] == pytest.approx(
            (0.0, 0.0, 0.0), abs=1e-12
        )
        assert record.state.metadata["actual_tip_delta_m"] == pytest.approx(
            (0.0, 0.0, 0.0), abs=1e-12
        )
    else:
        assert record.state.metadata["actual_tip_delta_m"] != pytest.approx(
            (0.0, 0.0, 0.0), abs=1e-12
        )


def test_explicit_mapping_parameters_reach_runtime_mapping_independently() -> None:
    clock = _ClockSequence((0.0, 0.0))
    source = ViewerInputSource(clock=clock.monotonic)
    plan = build_runtime_input_source_step_loop_plan(
        select_runtime_input_source(
            "viewer",
            steps=1,
            control_mapping_parameters={
                "keyboard_config": {
                    "bindings": {"KeyQ": {"axis": "z", "direction": -1}},
                    "speed_m_s": 0.2,
                    "deadzone": 0.0,
                    "max_delta_m": 0.05,
                },
                "gamepad_speed_m_s": 0.2,
                "gamepad_deadzone": 0.0,
                "gamepad_max_delta_m": 0.05,
            },
        ),
        publisher=NoOpStatePublisher(),
        viewer_input_source=source,
    )

    assert plan.control_mapping_parameters["gamepad_speed_m_s"] == pytest.approx(0.2)
    assert plan.control_mapping_parameters["gamepad_deadzone"] == pytest.approx(0.0)
    assert plan.control_mapping_parameters["gamepad_max_delta_m"] == pytest.approx(0.05)
    assert plan.control_mapping_parameters["keyboard_config"].bindings["KeyQ"].direction == -1

    ingest_viewer_control_message(
        source,
        ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=1.0,
            source_kind="gamepad",
            gamepad=ViewerControlGamepadMessage(
                connected=True,
                index=0,
                id="compat-pad",
                raw_axes=(0.15, 0.0, 0.0),
                axes=(_legacy_frontend_gamepad_projection(0.15), 0.0, 0.0),
                buttons=(),
                stale=False,
                zero_state=False,
            ),
        ),
    )
    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.intent.values[0] == pytest.approx(1.0 / 18.0, abs=1e-12)
    assert record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"][0] == pytest.approx(
        0.2 / 18.0, abs=1e-12
    )
    assert record.motion_command.metadata["endpoint_delta_requested_m"][0] == pytest.approx(
        (0.2 / 18.0) / 60.0, abs=1e-12
    )
    assert record.state.metadata["actual_tip_delta_m"] != pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-12
    )


def test_explicit_runtime_mapping_parameters_are_not_affected_by_source() -> None:
    clock = _ClockSequence((0.0, 0.0, 0.0, 0.0, 0.0))
    source = ViewerInputSource(clock=clock.monotonic)
    selection = select_runtime_input_source(
        "viewer",
        steps=1,
        control_mapping_parameters={
            "gamepad_speed_m_s": 0.3,
            "gamepad_deadzone": 0.2,
            "gamepad_max_delta_m": 0.01,
            "keyboard_config": {
                "bindings": {"KeyE": {"axis": "x", "direction": 1}},
                "speed_m_s": 0.3,
                "deadzone": 0.0,
                "max_delta_m": 0.01,
            },
        },
    )
    plan = build_runtime_input_source_step_loop_plan(
        selection,
        publisher=NoOpStatePublisher(),
        viewer_input_source=source,
    )

    assert plan.control_mapping_parameters["gamepad_speed_m_s"] == pytest.approx(0.3)
    assert plan.control_mapping_parameters["gamepad_deadzone"] == pytest.approx(0.2)
    assert plan.control_mapping_parameters["gamepad_max_delta_m"] == pytest.approx(0.01)
    assert plan.control_mapping_parameters["keyboard_config"].bindings["KeyE"].direction == 1
    assert "KeyQ" not in plan.control_mapping_parameters["keyboard_config"].bindings

    ingest_viewer_control_message(
        source,
        ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=1.0,
            source_kind="gamepad",
            gamepad=ViewerControlGamepadMessage(
                connected=True,
                index=0,
                id="explicit-pad",
                raw_axes=(0.5, 0.0, 0.0),
                axes=(_legacy_frontend_gamepad_projection(0.5), 0.0, 0.0),
                buttons=(),
                stale=False,
                zero_state=False,
            ),
        ),
    )
    gamepad_record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)
    assert gamepad_record.intent.values[0] == pytest.approx(4.0 / 9.0, abs=1e-12)
    assert gamepad_record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"][0] == pytest.approx(
        0.3 * 4.0 / 9.0, abs=1e-12
    )

    ingest_viewer_control_message(source, _keyboard_message(1.0, "KeyE"))
    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.intent.values == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)
    assert record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"][0] == pytest.approx(
        0.3, abs=1e-12
    )
    assert record.motion_command.metadata["endpoint_delta_requested_m"][0] == pytest.approx(
        0.3 / 60.0, abs=1e-12
    )


def test_disconnected_provider_sample_holds_runtime_motion_state() -> None:
    clock = _ClockSequence((0.0, 0.0))
    source = ViewerInputSource(clock=clock.monotonic)
    plan = build_runtime_input_source_step_loop_plan(
        select_runtime_input_source("viewer", steps=1),
        viewer_clock=clock.monotonic,
        viewer_input_source=source,
        publisher=NoOpStatePublisher(),
    )
    ingest_viewer_control_message(
        source,
        ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=2.0,
            source_kind="gamepad",
            gamepad=ViewerControlGamepadMessage(
                connected=False,
                index=0,
                id="disconnected-pad",
                raw_axes=(0.8, 0.0, 0.0),
                axes=(0.0, 0.0, 0.0),
                buttons=(),
                stale=False,
                zero_state=False,
            ),
        ),
    )

    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert source.health_snapshot()[0] == "disconnected"
    assert record.frame.metadata["source_active"] is False
    assert record.motion_command.metadata["endpoint_delta_m"] == pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-12
    )
    assert record.state.metadata["actual_tip_delta_m"] == pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-12
    )


@pytest.mark.parametrize(
    ("key_code", "expected_axis_index", "expected_sign"),
    (
        ("KeyD", 0, 1.0),
        ("KeyA", 0, -1.0),
        ("KeyW", 1, 1.0),
        ("KeyS", 1, -1.0),
        ("Space", 2, 1.0),
        ("ShiftLeft", 2, -1.0),
    ),
)
def test_viewer_step_loop_world_frame_preserves_keyboard_axis_mapping(
    key_code: str,
    expected_axis_index: int,
    expected_sign: float,
) -> None:
    clock = _ClockSequence((0.0, 0.0))
    source, plan = _build_viewer_plan(clock)

    ingest_viewer_control_message(source, _keyboard_message(2.0, key_code))
    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.frame.metadata["control_frame"] == "world"
    assert record.frame.metadata["axis_values"][expected_axis_index] == pytest.approx(expected_sign, abs=1e-12)
    assert record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"][expected_axis_index] == pytest.approx(
        expected_sign * 0.1,
        abs=1e-12,
    )
    assert record.motion_command.metadata["motion_status"] in {"accepted", "scaled"}
    assert record.state.metadata["actual_tip_delta_m"][expected_axis_index] * expected_sign > 0.0
    assert record.state.metadata["endpoint_progress_status"] == "progressing"


@pytest.mark.parametrize(
    ("key_code", "expected_z_sign"),
    (
        ("Space", 1.0),
        ("ShiftLeft", -1.0),
        ("ShiftRight", -1.0),
    ),
)
def test_viewer_step_loop_preserves_keyboard_z_axis_binding(
    key_code: str,
    expected_z_sign: float,
) -> None:
    clock = _ClockSequence((0.0, 0.0))
    source, plan = _build_viewer_plan(clock)

    ingest_viewer_control_message(source, _keyboard_message(2.0, key_code))
    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.frame.metadata["axis_values"][0] == pytest.approx(0.0, abs=1e-12)
    assert record.frame.metadata["axis_values"][1] == pytest.approx(0.0, abs=1e-12)
    assert record.frame.metadata["axis_values"][2] == pytest.approx(expected_z_sign, abs=1e-12)
    assert record.frame.metadata["control_frame"] == "world"
    assert record.frame.metadata["endpoint_velocity_m_s"][2] == pytest.approx(expected_z_sign * 0.1, abs=1e-12)
    assert record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"][2] == pytest.approx(
        expected_z_sign * 0.1,
        abs=1e-12,
    )
    assert record.motion_command.metadata["endpoint_delta_m"][2] == pytest.approx(expected_z_sign / 600.0, abs=1e-12)
    assert record.motion_command.metadata["endpoint_delta_requested_m"][2] == pytest.approx(expected_z_sign / 600.0, abs=1e-12)
    assert record.motion_command.metadata["endpoint_model"] == "mujoco_model_aligned_tip_site"
    assert record.state.metadata["actual_tip_delta_m"][2] * expected_z_sign > 0.0


def test_viewer_step_loop_dt_scales_endpoint_delta() -> None:
    source_fast, plan_fast = _build_viewer_plan(_ClockSequence((0.0, 0.0)))
    source_slow, plan_slow = _build_viewer_plan(_ClockSequence((0.0, 0.0)))

    ingest_viewer_control_message(source_fast, _keyboard_message(3.0, "KeyW"))
    ingest_viewer_control_message(source_slow, _keyboard_message(3.0, "KeyW"))

    fast_record = _run_single_viewer_step(plan_fast, dt_s=1.0 / 60.0)
    slow_record = _run_single_viewer_step(plan_slow, dt_s=1.0 / 30.0)

    assert fast_record.motion_command.metadata["endpoint_delta_requested_m"][1] == pytest.approx(1.0 / 600.0, abs=1e-12)
    assert slow_record.motion_command.metadata["endpoint_delta_requested_m"][1] == pytest.approx(1.0 / 300.0, abs=1e-12)
    assert slow_record.motion_command.metadata["endpoint_delta_requested_m"][1] == pytest.approx(
        fast_record.motion_command.metadata["endpoint_delta_requested_m"][1] * 2.0,
        abs=1e-12,
    )


@pytest.mark.parametrize("key_code", ("Space", "KeyW", "KeyA", "KeyD"))
def test_viewer_step_loop_holds_motion_without_repeated_keydown_until_keyup(
    key_code: str,
) -> None:
    clock = _ClockSequence((0.0, 0.0, 0.01, 0.02, 0.03, 0.04))
    source, plan = _build_viewer_plan(clock, steps=4)

    ingest_viewer_control_message(source, _keyboard_message(4.0, key_code))
    records = asyncio.run(run_runtime_input_source_step_loop(plan, steps=3, dt_s=1.0 / 60.0))

    assert len(records) == 3
    assert records[0].motion_command.metadata["motion_status"] in {"accepted", "scaled"}
    assert records[1].motion_command.metadata["motion_status"] in {"accepted", "scaled"}
    assert records[2].motion_command.metadata["motion_status"] in {"accepted", "scaled"}
    assert all(
        record.motion_command.metadata["qpos_delta_norm_rad"] <= 0.2 + 1e-12
        for record in records
    )
    assert records[0].state.qpos[:4] != records[1].state.qpos[:4]
    assert records[1].state.qpos[:4] != records[2].state.qpos[:4]
    assert any(abs(component) > 1e-12 for component in records[0].state.metadata["actual_tip_delta_m"])
    assert any(abs(component) > 1e-12 for component in records[1].state.metadata["actual_tip_delta_m"])
    assert any(abs(component) > 1e-12 for component in records[2].state.metadata["actual_tip_delta_m"])

    ingest_viewer_control_message(
        source,
        ViewerControlMessage(
            type="viewer_control_message",
            timestamp_s=5.0,
            source_kind="keyboard",
            keyboard=ViewerControlKeyboardMessage(
                active_key_codes=(),
                key_state={},
                focus_state="focused",
                zero_state=True,
            ),
        ),
    )
    stopped_record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert stopped_record.motion_command.metadata["motion_status"] == "accepted"
    assert stopped_record.state.metadata["endpoint_progress_status"] == "not_requested"
    assert stopped_record.motion_command.metadata["endpoint_delta_m"] == (0.0, 0.0, 0.0)
    assert stopped_record.state.metadata["actual_tip_delta_m"] == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)


def test_malformed_ingress_replaces_old_active_command_with_runtime_hold() -> None:
    clock = _ClockSequence((0.0, 0.0, 0.0))
    source, plan = _build_viewer_plan(clock)
    ingest_viewer_control_message(source, _keyboard_message(4.0, "KeyD"))
    with pytest.raises(ValueError, match="malformed JSON"):
        ingest_viewer_control_message(source, "{not json")

    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)

    assert record.frame.metadata["source_health_status"] == "invalid"
    assert record.frame.metadata["source_active"] is False
    # The existing runtime hold contract represents a safe hold as an
    # accepted zero-motion command rather than a new physical command.
    assert record.motion_command.metadata["motion_status"] == "accepted"
    assert record.motion_command.metadata["endpoint_delta_m"] == pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-12
    )
    assert record.state.metadata["actual_tip_delta_m"] == pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-12
    )


def test_viewer_step_loop_scales_large_dt_boundary_motion() -> None:
    clock = _ClockSequence((0.0, 0.0))
    source, plan = _build_viewer_plan(clock)

    ingest_viewer_control_message(source, _keyboard_message(6.0, "KeyD"))
    record = _run_single_viewer_step(plan, dt_s=1.0)

    assert record.frame.metadata["endpoint_velocity_m_s"] == pytest.approx((0.1, 0.0, 0.0), abs=1e-12)
    assert record.frame.metadata["control_frame"] == "world"
    assert record.motion_command.metadata["motion_status"] == "scaled"
    assert record.motion_command.metadata["endpoint_delta_m"] == pytest.approx((0.01, 0.0, 0.0), abs=1e-12)
    assert record.motion_command.metadata["motion_rejection_reason"] is None


@pytest.mark.parametrize("key,raw", [
    ("KeyD", (1.0, 0.0, 0.0, 0.0)), ("KeyA", (-1.0, 0.0, 0.0, 0.0)),
    ("KeyW", (0.0, -1.0, 0.0, 0.0)), ("KeyS", (0.0, 1.0, 0.0, 0.0)),
    ("Space", (0.0, 0.0, 0.0, -1.0)), ("ShiftLeft", (0.0, 0.0, 0.0, 1.0)),
])
def test_explicit_gamepad_world_axes_match_keyboard_mujoco_motion(key, raw):
    # 期待の運動経路を別入力で照合し、到達不能を軸符号の誤りと混同しない。
    keyboard_source, keyboard_plan = _build_viewer_plan(_ClockSequence((0.0, 0.0)))
    ingest_viewer_control_message(keyboard_source, _keyboard_message(1.0, key))
    keyboard_record = _run_single_viewer_step(keyboard_plan, dt_s=1.0 / 60.0)
    clock = _ClockSequence((0.0, 0.0))
    source = ViewerInputSource(clock=clock.monotonic)
    selection = select_runtime_input_source(
        "viewer", steps=1,
        control_mapping_selection=PluginSelection("viewer_keyboard_gamepad_mapping", 1),
        control_mapping_parameters={"gamepad_axis_map": {
            "axis_indices": (0, 1, 3), "axis_signs": (1, -1, -1),
        }},
    )
    plan = build_runtime_input_source_step_loop_plan(
        selection, viewer_clock=clock.monotonic, viewer_input_source=source,
        publisher=NoOpStatePublisher(),
    )
    ingest_viewer_control_message(source, ViewerControlMessage(
        type="viewer_control_message", timestamp_s=1.0, source_kind="gamepad",
        gamepad=ViewerControlGamepadMessage(
            connected=True, raw_axes=raw, axes=raw, buttons=(), stale=False, zero_state=False,
        ),
    ))
    record = _run_single_viewer_step(plan, dt_s=1.0 / 60.0)
    assert record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"] == pytest.approx(
        keyboard_record.motion_command.metadata["resolved_world_endpoint_velocity_m_s"], abs=1e-12,
    )
    assert record.state.qpos == pytest.approx(keyboard_record.state.qpos, abs=1e-12)
    assert record.state.metadata["actual_tip_delta_m"] == pytest.approx(
        keyboard_record.state.metadata["actual_tip_delta_m"], abs=1e-12,
    )
    assert record.state.metadata["motion_status"] == keyboard_record.state.metadata["motion_status"]
