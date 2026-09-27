"""実行入口ではなくresolved routeが変換を決めることを実MuJoCoで確認する。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from math import dist

import pytest

from xpotato_sim.plugins.input_sources.selfrionette import SelfrionetteInputSource
from xpotato_sim.plugins.mappings.loadcell_endpoint_mapping.implementation import (
    LOADCELL_ENDPOINT_MAPPING_PLUGIN,
)
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.execution.command_routes import project_joint_position_command
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.experiment.input_source import InputSourceHealthStatus
from xpotato_sim.runtime.safety.qpos_feasibility import QposFeasibilityResult
from xpotato_sim.schemas import JointCommand, JointPositionCommand, MuJoCoState


def _selection(values, *, gain=0.002, limit=0.002, extra=None):
    # channel0 -> Yだけの合成写像。装置のcalibrationを意味しない。
    weights = ((0.0, 1.0, 0.0),) + ((0.0, 0.0, 0.0),) * 6
    parameters = {"mapping_config": {
        "channel_axis_weights": weights, "gain_m": gain, "max_delta_m": limit,
    }}
    if extra is not None:
        parameters.update(extra)
    lines = tuple(f"vector,{i * 20},{value},0,0,0,0,0,0" for i, value in enumerate(values))
    return select_runtime_input_source(
        "selfrionette", steps=len(lines), line_source=lines,
        control_mapping_parameters=parameters,
    )


@pytest.mark.parametrize("dt", (0.01, 0.02, 0.1))
@pytest.mark.parametrize("gain,limit", ((0.002, 0.002), (0.1, 0.03)))
def test_entrypoints_emit_identical_backend_commands_without_placeholder(monkeypatch, dt, gain, limit):
    values = (0.0, 0.25, 1.0, -0.25, 0.0)
    selection_a = _selection(values, gain=gain, limit=limit)
    selection_b = _selection(values, gain=gain, limit=limit)
    plan_a = build_runtime_input_source_step_loop_plan(selection_a)
    plan_b = build_runtime_input_source_step_loop_plan(selection_b)
    assert plan_a.command_semantics_route == plan_b.command_semantics_route
    parameters = dict(selection_a.control_mapping_parameters)
    assert "current_tip_position_m" not in parameters

    requests = {id(plan_a.pipeline.simulator): [], id(plan_b.pipeline.simulator): []}
    simulator_type = type(plan_a.pipeline.simulator)
    execute = simulator_type.apply_joint_position_command

    def record_request(simulator, command):
        requests[id(simulator)].append(command)
        return execute(simulator, command)

    monkeypatch.setattr(simulator_type, "apply_joint_position_command", record_request)
    records = asyncio.run(run_runtime_input_source_step_loop(plan_a, steps=len(values), dt_s=dt))

    async def run_ticks():
        reader = selection_b.runtime_reader
        reader.start()
        try:
            return tuple([await plan_b.pipeline.run_once(dt) for _ in values])
        finally:
            reader.close()

    observations = asyncio.run(run_ticks())
    left = requests[id(plan_a.pipeline.simulator)]
    right = requests[id(plan_b.pipeline.simulator)]
    assert len(left) == len(right) == len(values)
    assert left == right
    for i, (record, observation, request) in enumerate(zip(records, observations, left, strict=True)):
        assert type(request) is JointPositionCommand
        assert type(observation) is MuJoCoState
        assert request == project_joint_position_command(record.motion_command)
        assert record.state.qpos == observation.qpos
        assert observation.frame_index == i + 1
        assert observation.time_s == pytest.approx((i + 1) * dt)
    assert dist(observations[0].qpos, observations[2].qpos) > 1e-6
    assert observations[-1].qpos == observations[-2].qpos
    assert dict(selection_a.control_mapping_parameters) == parameters
    assert "current_tip_position_m" not in plan_a.pipeline.control_mapping_parameters
    assert selection_a.runtime_reader.current_health().status is InputSourceHealthStatus.DISCONNECTED
    assert selection_b.runtime_reader.current_health().status is InputSourceHealthStatus.DISCONNECTED


def test_mapping_request_policy_bound_and_measured_state_are_separate():
    plan = build_runtime_input_source_step_loop_plan(_selection((1.0,), gain=0.1, limit=0.03))
    initial = plan.pipeline.simulator.snapshot()
    record, = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=0.02))
    command = record.motion_command
    assert record.intent.metadata["endpoint_delta_m"] == (0.0, 0.03, 0.0)
    assert command.metadata["mapped_endpoint_delta_m"] == (0.0, 0.03, 0.0)
    assert command.metadata["endpoint_delta_requested_m"] == (0.0, 0.01, 0.0)
    assert command.metadata["motion_status"] == "scaled"
    policy = command.metadata["motion_policy_v1"]
    assert policy["identity"] == "local_endpoint_dls_bounds/v1"
    assert policy["max_endpoint_delta_norm_m"] == 0.01
    assert policy["max_qpos_delta_norm_rad"] == 0.2
    request = plan.pipeline.simulator.last_joint_position_command
    assert type(request) is JointPositionCommand
    assert request == project_joint_position_command(command)
    assert request.joint_angles_rad == command.metadata["candidate_qpos_rad"]
    observed_tip = plan.endpoint_pose_provider.observe_endpoint_pose(record.state).position_m
    initial_tip = plan.endpoint_pose_provider.observe_endpoint_pose(initial).position_m
    actual = tuple(after - before for after, before in zip(observed_tip, initial_tip, strict=True))
    assert record.state.metadata["actual_tip_delta_m"] == pytest.approx(actual)
    # 予測値をactualとしてコピーした実装は、上のsnapshot由来の照合で検出する。
    assert record.state.frame_index == initial.frame_index + 1


def test_rejected_candidate_does_not_become_backend_request():
    plan = build_runtime_input_source_step_loop_plan(_selection((1.0,)))
    initial = plan.pipeline.simulator.snapshot()
    seen = []

    class RejectCandidate:
        """実機制約値を仮定せず、既存guard境界のreject分岐だけを注入する。"""

        def evaluate(self, command, *, current_qpos_rad):
            candidate = command.joint.joint_angles_rad
            seen.append(candidate)
            held = replace(command, joint=JointCommand(joint_angles_rad=tuple(current_qpos_rad)))
            return QposFeasibilityResult(held, False, "reject", candidate)

    plan.pipeline.qpos_feasibility_guard = RejectCandidate()
    record, = asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=0.02))
    request = plan.pipeline.simulator.last_joint_position_command
    assert seen and seen[0] != initial.qpos
    assert record.motion_command.metadata["candidate_qpos_rad"] == seen[0]
    assert request.joint_angles_rad == initial.qpos
    assert request.joint_angles_rad != seen[0]
    assert record.state.qpos == initial.qpos
    assert record.state.metadata["endpoint_evaluation"] is None


@pytest.mark.parametrize("position", (None, (1.0, 2.0), (float("nan"), 0.0, 0.0), (True, 0.0, 0.0)))
def test_invalid_context_is_rejected_before_backend_request(position):
    selection = _selection((1.0,))
    plan = build_runtime_input_source_step_loop_plan(selection)
    original = plan.endpoint_pose_provider

    class InvalidObservation:
        def observe_endpoint_pose(self, state):
            return replace(original.observe_endpoint_pose(state), position_m=position)

    plan = replace(plan, endpoint_pose_provider=InvalidObservation())
    with pytest.raises(ValueError, match="measured endpoint"):
        asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=0.02))
    assert plan.pipeline.simulator.last_joint_position_command is None
    assert plan.pipeline.simulator.snapshot().frame_index == 0
    assert selection.runtime_reader.current_health().status is InputSourceHealthStatus.DISCONNECTED


@pytest.mark.parametrize("extra", ({"typo": 1}, {"current_tip_position_m": (float("nan"), 0.0, 0.0)}))
def test_invalid_explicit_parameters_fail_before_reader_construction(monkeypatch, extra):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid parameters reached reader construction")

    monkeypatch.setattr(SelfrionetteInputSource, "__init__", forbidden)
    with pytest.raises(ValueError):
        _selection((1.0,), extra=extra)


def test_pure_mapping_still_requires_explicit_context():
    with pytest.raises(ValueError, match="current_tip_position_m"):
        LOADCELL_ENDPOINT_MAPPING_PLUGIN.normalize_parameters({"mapping_config": {}})
    normalized = LOADCELL_ENDPOINT_MAPPING_PLUGIN.normalize_runtime_parameters({"mapping_config": {}})
    assert "current_tip_position_m" not in normalized



def test_context_declaration_mismatch_is_rejected_before_model_creation(monkeypatch):
    from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
    from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline
    from tests.support.transport_doubles import NoOpStatePublisher

    def forbidden(*args, **kwargs):
        pytest.fail("context mismatch reached model construction")

    plugin_type = type(resolve_robot_bundle("fast_arm").runtime_plugin)
    monkeypatch.setattr(plugin_type, "build_simulator", forbidden)
    mapping = replace(LOADCELL_ENDPOINT_MAPPING_PLUGIN, runtime_context_parameters=frozenset())
    with pytest.raises(ValueError, match="context declaration mismatch"):
        build_concrete_mujoco_pipeline(control_mapping=mapping, publisher=NoOpStatePublisher())


@pytest.mark.parametrize("frame", ("world", "tool"))
def test_gamepad_velocity_has_same_backend_request_at_both_entrypoints(frame):
    from xpotato_sim.plugins.input_sources.viewer import ViewerInputSource
    from xpotato_sim.runtime.control.viewer_control_ingress import ingest_viewer_control_message
    from xpotato_sim.schemas import ViewerControlMessage, ViewerControlGamepadMessage

    sources = [ViewerInputSource(clock=lambda: 0.0) for _ in range(2)]
    plans = [build_runtime_input_source_step_loop_plan(
        select_runtime_input_source("viewer", steps=1), viewer_input_source=source,
    ) for source in sources]
    message = ViewerControlMessage(
        type="viewer_control_message", timestamp_s=0.0, source_kind="gamepad",
        gamepad=ViewerControlGamepadMessage(connected=True, axes=(0.4, 0.2, 0.0), buttons=()),
        metadata={"control_frame": frame, "intent_kind": "local_endpoint_delta"},
    )
    for source in sources:
        ingest_viewer_control_message(source, message)
    record, = asyncio.run(run_runtime_input_source_step_loop(plans[0], steps=1, dt_s=0.02))
    reader = plans[1].pipeline.input_source
    reader.start()
    try:
        observed = asyncio.run(plans[1].pipeline.run_once(0.02))
    finally:
        reader.close()
    assert plans[0].pipeline.simulator.last_joint_position_command == plans[1].pipeline.simulator.last_joint_position_command
    assert record.state.qpos == observed.qpos
    assert record.motion_command.metadata["intent_kind"] == "local_endpoint_velocity"


@pytest.mark.parametrize("entry", ("step_loop", "run_once"))
@pytest.mark.parametrize("status", ("inactive", "stale", "invalid", "disconnected"))
def test_managed_health_stops_motion_at_both_entrypoints(entry, status):
    """取得済みhealthの非active状態を、frameの省略値でactiveへ戻さない。"""
    from xpotato_sim.runtime.experiment.input_source import InputSourceHealth

    plan = build_runtime_input_source_step_loop_plan(_selection((1.0,)))
    original = plan.pipeline.input_source
    initial = plan.pipeline.simulator.snapshot()
    lifecycle = []

    class HealthOverride:
        def start(self):
            lifecycle.append("start")
            original.start()

        def close(self):
            lifecycle.append("close")
            original.close()

        def read_frame(self):
            return original.read_frame()

        def current_health(self):
            return InputSourceHealth(InputSourceHealthStatus(status), age_ms=500,
                                     reason=None if status == "inactive" else "audit_" + status)

    reader = HealthOverride()
    plan.pipeline.input_source = reader
    if entry == "step_loop":
        asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=0.02))
    else:
        reader.start()
        try:
            asyncio.run(plan.pipeline.run_once(0.02))
        finally:
            reader.close()
    assert lifecycle == ["start", "close"]
    assert plan.pipeline.simulator.last_joint_position_command.joint_angles_rad == initial.qpos
    assert plan.pipeline.simulator.snapshot().qpos == initial.qpos
    assert plan.pipeline.simulator.last_command.metadata["runtime_input_safety_applied"] is True


@pytest.mark.parametrize("entry", ("step_loop", "run_once"))
@pytest.mark.parametrize("field,value", (("source_active", False), ("command_age_ms", 500), ("stale_reason", "old")))
def test_managed_health_conflict_fails_before_backend_request(entry, field, value):
    """frameとhealthに明示的不一致がある場合、どちらの入口も指令前に拒否する。"""
    from xpotato_sim.runtime.experiment.input_source import InputSourceHealth

    plan = build_runtime_input_source_step_loop_plan(_selection((1.0,)))
    original = plan.pipeline.input_source

    class ConflictingSource:
        def start(self):
            original.start()

        def close(self):
            original.close()

        def read_frame(self):
            frame = original.read_frame()
            return replace(frame, metadata={**frame.metadata, field: value})

        def current_health(self):
            return InputSourceHealth(InputSourceHealthStatus.ACTIVE, age_ms=0)

    reader = ConflictingSource()
    plan.pipeline.input_source = reader
    with pytest.raises(ValueError, match="frame metadata and typed health disagree"):
        if entry == "step_loop":
            asyncio.run(run_runtime_input_source_step_loop(plan, steps=1, dt_s=0.02))
        else:
            reader.start()
            try:
                asyncio.run(plan.pipeline.run_once(0.02))
            finally:
                reader.close()
    assert plan.pipeline.simulator.last_joint_position_command is None
    assert plan.pipeline.simulator.snapshot().frame_index == 0
