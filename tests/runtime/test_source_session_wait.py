"""Source宣言の未受信理由を、初期化・空tick・restart/resetで保持する。"""
import pytest

from tests.plugins.input_sources.selfrionette_device.test_source import wire
from xpotato_sim.plugins.input_sources.viewer.session import GamepadSessionSource
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from xpotato_sim.runtime.execution.model_execution import ModelExecution


@pytest.mark.parametrize("name,reason", [
    ("inputs/bimanual-gamepad", "awaiting_gamepad_input"),
    ("inputs/single-selfrionette-fixture", "awaiting_device_input"),
    ("inputs/bimanual-selfrionette-fixture", "awaiting_device_input"),
])
def test_source_wait_reason_survives_initial_empty_ticks_reset_and_restart(name, reason):
    execution = ModelExecution(load_launch_profile(name), clock=lambda: 10.)
    try:
        home = execution.sample(0).qpos
        for epoch in (None, "model-reset"):
            if epoch is not None:
                execution.reset(epoch)
            assert execution.state == "waiting_neutral" and execution.reason == reason
            for frame_index in (1, 2):
                assert execution.tick() == 0
                sample = execution.sample(frame_index)
                assert sample.qpos == home and sample.time_s == 0.
                assert sample.metadata["motion_status"] == "waiting_neutral"
                assert sample.metadata["motion_rejection_reason"] == reason
            runtime = execution.runtime
            assert runtime.initial_wait_reason == runtime.source.initial_wait_reason == reason
            if runtime.latest_trigger_presentation is not None:
                assert runtime.latest_trigger_presentation["reason"] == reason
        previous_source = runtime.source
        with pytest.raises(RuntimeError, match="stopped/faulted"):
            runtime.restart(epoch="composition-restart")
        runtime.stop()
        runtime.restart(epoch="composition-restart")
        assert runtime.source is not previous_source and runtime.source.last_received_at_s is None
        assert runtime.initial_wait_reason == runtime.source.initial_wait_reason == reason
        if runtime.latest_trigger_presentation is not None:
            assert runtime.latest_trigger_presentation["reason"] == reason
    finally:
        execution.stop()


def test_partial_device_input_keeps_device_wait_until_all_roles_are_neutral():
    execution = ModelExecution(load_launch_profile("inputs/bimanual-selfrionette-fixture"), clock=lambda: 10.)
    try:
        home = execution.sample(0).qpos
        execution.ingest(wire("left"))
        assert execution.tick() == 0 and execution.state == "waiting_neutral"
        assert execution.reason == "awaiting_device_input"
        assert execution.sample(1).qpos == home
        execution.ingest(wire("right"))
        assert execution.tick() == 0 and execution.state == "running" and execution.reason is None
        assert execution.sample(2).qpos == home
    finally:
        execution.stop()


def test_composition_uses_source_declaration_and_rejects_untyped_reason(monkeypatch):
    profile = load_launch_profile("inputs/bimanual-gamepad")
    monkeypatch.setattr(GamepadSessionSource, "initial_wait_reason", "awaiting_declared_input")
    execution = ModelExecution(profile, clock=lambda: 10.)
    try:
        assert execution.reason == "awaiting_declared_input"
        assert execution.runtime.latest_trigger_presentation["reason"] == "awaiting_declared_input"
        assert execution.tick() == 0 and execution.reason == "awaiting_declared_input"
    finally:
        execution.stop()
    monkeypatch.setattr(GamepadSessionSource, "initial_wait_reason", None)
    with pytest.raises(ValueError, match="source initial wait reason"):
        ModelExecution(profile, clock=lambda: 10.)
