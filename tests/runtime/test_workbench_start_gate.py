"""開始要求、記録成立、入力受付の別時計とcommit表示の寿命を検査する。"""
import json
import pytest
from test_trial_runner import prepared, message, start
from xpotato_sim.runtime.experiment.trial_record import TrialRecorder
from threading import Event
from time import monotonic, sleep
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits
from xpotato_sim.runtime.composition.launch_profile import load_launch_profile
from test_trial_runner import Clock


@pytest.mark.parametrize("pre_recording", [False, True])
def test_input_deadline_begins_after_start_recording(tmp_path, monkeypatch, pre_recording):
    runner, ticket, clock = prepared(tmp_path, ticks=100, input_wait_s=5, wall_s=30)
    original = TrialRecorder.start
    def delayed(recorder, **kwargs):
        result = original(recorder, **kwargs)
        clock.now += 7
        return result
    monkeypatch.setattr(TrialRecorder, "start", delayed)
    try:
        runner.start(ticket, input_provenance={"software_fixture": True})
        assert runner.input_accepting_monotonic_s == 17
        record = json.loads((tmp_path/ticket.trial_id/"start.json").read_text())
        assert record["started_monotonic_s"] == 10
        if pre_recording:
            with pytest.raises(ValueError, match="input_pre_recording"):
                runner.ingest(ticket, message(), received_at_s=16.9)
            assert runner.result.to_document()["runner_stop_reason"] == "technical_invalid"
        else:
            runner.advance(ticket)
            assert runner.status == "waiting_input"
            clock.now = 21.99
            runner.advance(ticket)
            assert runner.status == "waiting_input"
            clock.now = 22
            runner.advance(ticket)
            terminal = runner.result.to_document()
            assert terminal["runner_stop_reason"] == "input_wait_timeout"
            assert terminal["input_accepting_monotonic_s"] == 17
    finally:
        runner.close()


def test_commit_projection_is_one_shot_and_invalidated_by_input_and_stop(tmp_path):
    runner, ticket, clock = prepared(tmp_path, ticks=100)
    try:
        start(runner, ticket, clock)
        runner.advance(ticket)
        projection = runner.take_committed_projection()
        assert projection is not None and projection.time_s == runner.tick_count * runner._execution.profile.dt_s
        assert projection.metadata["coordinated_runtime_v1"]["epoch"] == ticket.epoch
        assert runner.take_committed_projection() is None
        runner.advance(ticket)
        runner.ingest(ticket, message(1), received_at_s=clock())
        assert runner.take_committed_projection() is None
        runner.advance(ticket)
        runner.abort(ticket)
        assert runner.take_committed_projection() is None
        assert runner.snapshot().metadata["motion_status"] == "stopped"
        runner.retry()
        assert runner.take_committed_projection() is None
    finally:
        runner.close()


@pytest.mark.parametrize("failed", [False, True])
def test_async_recording_gate_does_not_block_execution_owner(tmp_path, monkeypatch, failed):
    entered, release = Event(), Event()
    original = TrialRecorder.terminal
    def delayed(recorder, **kwargs):
        entered.set()
        assert release.wait(2)
        if failed:raise OSError("injected terminal record failure")
        return original(recorder, **kwargs)
    monkeypatch.setattr(TrialRecorder, "terminal", delayed)
    clock = Clock()
    runner = TrialRunner(result_root=tmp_path, software_revision="test", clock=clock, async_terminal_recording=True)
    ticket = runner.prepare(load_launch_profile("fast-arm-bimanual-gamepad"), TrialLimits(1))
    try:
        start(runner, ticket, clock)
        began = monotonic()
        runner.advance(ticket)
        assert monotonic()-began < .1
        assert entered.wait(1)
        assert runner.status == "finalizing"
        ticks = runner.tick_count
        assert runner.advance(ticket) is None
        assert runner.abort(ticket) is None
        assert runner.tick_count == ticks
        with pytest.raises(RuntimeError):runner.retry()
        assert not (tmp_path/ticket.trial_id/"terminal.json").exists()
        release.set()
        deadline = monotonic()+1
        while runner.status == "finalizing" and monotonic()<deadline:
            runner.advance(ticket);sleep(.001)
        assert runner.status == ("recording_failed" if failed else "terminal")
        assert runner.result.to_document()["recording"] == ("failed" if failed else "complete")
        if failed:
            with pytest.raises(RuntimeError):runner.retry()
        else:
            assert runner.retry().epoch != ticket.epoch
    finally:
        release.set();runner.close()
    assert runner._record_executor is None
