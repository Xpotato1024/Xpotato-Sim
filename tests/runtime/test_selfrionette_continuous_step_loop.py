"""連続Selfrionette入力をproduction modelへ通すsoftware-only回帰。"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from math import dist

import pytest

from xpotato_sim.plugins.input_sources.selfrionette import SelfrionetteInputSource, SerialFrameParseError
from xpotato_sim.plugins.robots.catalog import resolve_robot_bundle
from xpotato_sim.runtime.control.input_source_selection import select_runtime_input_source
from xpotato_sim.runtime.execution.command_routes import project_joint_position_command
from xpotato_sim.runtime.execution.input_step_loop import (
    build_runtime_input_source_step_loop_plan,
    run_runtime_input_source_step_loop,
)
from xpotato_sim.runtime.experiment.input_source import InputSourceHealthStatus
from xpotato_sim.schemas import JointPositionCommand


def _selection(lines: tuple[str, ...], *, axis: int = 1):
    # これは実機の指/センサ対応ではなく、1 channelだけを動かす明示的なtest mapping。
    """実機校正ではない明示行列でinjected sourceを選択する。"""
    weights = tuple(tuple(float(channel == 0 and component == axis) for component in range(3)) for channel in range(7))
    return select_runtime_input_source(
        "selfrionette", steps=len(lines), line_source=lines,
        control_mapping_parameters={
            "mapping_config": {"channel_axis_weights": weights, "gain_m": 0.002, "max_delta_m": 0.002},
            # Runtimeはこの固定値ではなく同じmodelから観測した各stepのtipを使わなければならない。
            "current_tip_position_m": (9.0, 9.0, 9.0),
        },
    )


def _run(plan, steps: int):
    """一つのplanを固定cadenceで有限回だけ実行する。"""
    return asyncio.run(run_runtime_input_source_step_loop(plan, steps=steps, dt_s=0.02))


def test_continuous_loadcell_uses_one_model_and_one_reader_lifecycle(monkeypatch):
    """モデル共有、観測context更新、zero停止、sourceの後始末を確認する。"""
    built = []
    lifecycle = []
    plugin_type = type(resolve_robot_bundle("fast_arm").runtime_plugin)
    original_build = plugin_type.build_simulator
    original_start, original_close = SelfrionetteInputSource.start, SelfrionetteInputSource.close

    def build(plugin, **kwargs):
        """productionのモデル生成回数を数え、実物のsimulatorを返す。"""
        simulator = original_build(plugin, **kwargs)
        built.append(simulator)
        return simulator

    def start(source):
        """実際のinjected readerのstartを記録する。"""
        lifecycle.append(("start", id(source)))
        return original_start(source)

    def close(source):
        """実際のreaderのcloseを記録する。"""
        lifecycle.append(("close", id(source)))
        return original_close(source)

    monkeypatch.setattr(plugin_type, "build_simulator", build)
    monkeypatch.setattr(SelfrionetteInputSource, "start", start)
    monkeypatch.setattr(SelfrionetteInputSource, "close", close)
    lines = ("vector,0,0,0,0,0,0,0,0", "vector,20,0.25,0,0,0,0,0,0", "vector,40,0.25,0,0,0,0,0,0", "vector,60,0,0,0,0,0,0,0")
    selection = _selection(lines)
    parameters = selection.control_mapping_parameters
    plan = build_runtime_input_source_step_loop_plan(selection)
    initial = plan.pipeline.simulator.snapshot()
    records = _run(plan, 4)

    assert len(built) == 1 and built[0] is plan.pipeline.simulator
    assert [event for event, _ in lifecycle] == ["start", "close"]
    assert lifecycle[0][1] == lifecycle[1][1]
    assert [record.state.frame_index for record in records] == [1, 2, 3, 4]
    assert [record.state.time_s for record in records] == pytest.approx([0.02, 0.04, 0.06, 0.08])
    assert selection.control_mapping_parameters is parameters
    assert parameters["current_tip_position_m"] == (9.0, 9.0, 9.0)
    assert dist(initial.qpos, records[2].state.qpos) > 1e-6

    previous = initial
    for record, delta in zip(records, (0.0, 0.0005, 0.0005, 0.0), strict=True):
        tip = plan.endpoint_pose_provider.observe_endpoint_pose(previous).position_m
        assert tip is not None
        assert record.intent.metadata["current_tip_position_m"] == tip
        expected = (tip[0], tip[1] + delta, tip[2])
        assert record.intent.metadata["desired_endpoint_m"] == pytest.approx(expected, abs=1e-12)
        command = project_joint_position_command(record.motion_command)
        assert type(command) is JointPositionCommand
        assert command.timestamp_s == record.intent.timestamp_s
        assert len(command.joint_angles_rad) == len(record.state.qpos) == 4
        previous = record.state
    assert records[-1].intent.metadata["current_tip_position_m"] != plan.endpoint_pose_provider.observe_endpoint_pose(initial).position_m
    assert selection.runtime_reader.current_health().status is InputSourceHealthStatus.DISCONNECTED


@pytest.mark.parametrize("axis", (0, 1, 2))
@pytest.mark.parametrize("value", (-0.25, 0.25))
def test_axis_mapping_retains_delta_semantics_and_raw_signal(axis, value):
    """各軸の符号と位置増分を実MuJoCo観測で確認する。"""
    line = f"vector,20,{value},0,0,0,0,0,0"
    plan = build_runtime_input_source_step_loop_plan(_selection((line,), axis=axis))
    initial_tip = plan.endpoint_pose_provider.observe_endpoint_pose(plan.pipeline.simulator.snapshot()).position_m
    record, = _run(plan, 1)
    expected = tuple(initial_tip[i] + (value * 0.002 if i == axis else 0.0) for i in range(3))
    assert record.intent.metadata["desired_endpoint_m"] == pytest.approx(expected)
    assert record.frame.metadata["raw_line"] == line
    assert record.intent.values[0] == value
    actual_tip = plan.endpoint_pose_provider.observe_endpoint_pose(record.state).position_m
    assert value * (actual_tip[axis] - initial_tip[axis]) > 0.0


@pytest.mark.parametrize("lines, expected_exception", (
    (("vector,0,0,0,0,0,0,0,0", "vector,20,1,2"), SerialFrameParseError),
    (("vector,0,0,0,0,0,0,0,0",), RuntimeError),
))
def test_invalid_or_exhausted_input_closes_reader_without_restarting(lines, expected_exception):
    """壊れたframeやEOFを成功にせず、readerを閉じる。"""
    selection = _selection(lines)
    plan = build_runtime_input_source_step_loop_plan(selection)
    with pytest.raises(expected_exception):
        _run(plan, 2)
    assert plan.pipeline.simulator.snapshot().frame_index == 1
    assert selection.runtime_reader.current_health().status is InputSourceHealthStatus.DISCONNECTED


def test_missing_measured_tip_fails_before_mapping_or_motion():
    """観測位置欠落時に固定初期値へfallbackしない。"""
    selection = _selection(("vector,0,1,0,0,0,0,0,0",))
    plan = build_runtime_input_source_step_loop_plan(selection)
    original = plan.endpoint_pose_provider

    class MissingTip:
        def observe_endpoint_pose(self, state):
            """観測欠落のnegative controlを返す。"""
            return replace(original.observe_endpoint_pose(state), position_m=None)

    plan = replace(plan, endpoint_pose_provider=MissingTip())
    with pytest.raises(ValueError, match="measured endpoint"):
        _run(plan, 1)
    assert plan.pipeline.simulator.snapshot().frame_index == 0
    assert selection.runtime_reader.current_health().status is InputSourceHealthStatus.DISCONNECTED


def test_missing_reader_is_rejected_before_model_construction():
    """取得元の欠落を別sourceへのfallbackにしない。"""
    selection = replace(_selection(("vector,0,0,0,0,0,0,0,0",)), runtime_reader=None)
    with pytest.raises(ValueError, match="explicit managed reader"):
        build_runtime_input_source_step_loop_plan(selection)


def test_zero_weight_default_does_not_return_to_configured_initial_tip():
    """未設定weightsは移動を生成せず、固定configのtipへ復帰しない。"""
    selection = select_runtime_input_source(
        "selfrionette", steps=1, line_source=("vector,0,1,1,1,1,1,1,1",),
        control_mapping_parameters={"mapping_config": {}, "current_tip_position_m": (9.0, 9.0, 9.0)},
    )
    plan = build_runtime_input_source_step_loop_plan(selection)
    initial = plan.pipeline.simulator.snapshot()
    record, = _run(plan, 1)
    assert record.state.qpos == initial.qpos
    assert record.intent.metadata["current_tip_position_m"] == plan.endpoint_pose_provider.observe_endpoint_pose(initial).position_m
