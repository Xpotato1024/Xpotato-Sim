"""receipt履歴の消費とphysics積分を実MuJoCoで分離して検査する。"""
import json
from dataclasses import asdict

import pytest

from test_trial_runner import prepared, start, message
from test_workbench_worker import owner_projection
from tests.plugins.mappings.viewer_keyboard_gamepad_mapping.test_gamepad_triggers import message as trigger_message


@pytest.mark.parametrize("boundary", ["drain", "read", "clock"])
def test_freshness_barrier_before_fault(tmp_path, monkeypatch, boundary):
    runner, ticket, clock = prepared(tmp_path, ticks=100)
    try:
        start(runner, ticket, clock)
        queued, injected = [], [False]
        def stall():
            if not injected[0]:
                injected[0] = True
                clock.now += .210
                queued.append((message(1), clock.now))
        def pending():
            samples = tuple(queued)
            queued.clear()
            if boundary == "drain": stall()
            return samples
        composition = runner._execution.runtime
        original_read = composition.source.read_frame
        def read():
            frame = original_read()
            if boundary == "read": stall()
            return frame
        monkeypatch.setattr(composition.source, "read_frame", read)
        original_clock = composition.clock
        def observe_clock():
            if boundary == "clock": stall()
            return original_clock()
        composition.clock = observe_clock
        runner.advance(ticket, pending_input=pending)
        assert runner.status == "running" and runner.result is None
        assert runner.tick_count == 0
        runner.advance(ticket, pending_input=pending)
        assert runner.tick_count == 1
        assert runner.processed_input_sequence == 1
    finally:
        runner.close()


@pytest.mark.parametrize("control", [False, True])
def test_barrier_empty_queue_is_strict_and_control_defers(tmp_path, control):
    runner, ticket, clock = prepared(tmp_path, ticks=100)
    try:
        start(runner, ticket, clock)
        calls = [0]
        def pending():
            calls[0] += 1
            if calls[0] == 1:
                clock.now += .201
                return ()
            return None if control else ()
        runner.advance(ticket, pending_input=pending)
        assert calls[0] == 2 and runner.tick_count == 0
        if control:
            assert runner.result is None
            runner.abort(ticket)
            assert runner.result.to_document()["runner_stop_reason"] == "operator_abort"
        else:
            assert runner.result.to_document()["runner_stop_reason"] == "technical_invalid"
            assert "input_stale_or_future" in runner.result.to_document()["error"]
    finally:
        runner.close()


def wire_message(**kwargs):
    value = asdict(trigger_message(**kwargs))
    value.pop("keyboard")
    value.update(provider_id="gamepad/v1", provider_schema="viewer_gamepad_sample/v1")
    return json.dumps(value)


@pytest.mark.parametrize("stall", [.190, .250, .700])
def test_queued_fresh_sample_precedes_tick_without_motion_replay(tmp_path, stall):
    runner, ticket, clock = prepared(tmp_path, ticks=100)
    try:
        start(runner, ticket, clock)
        initial = runner._execution.instance.provider.trial_state()
        count = int(stall * 60)
        samples = [(message(i, (.3, 0, 0, 0)), 10 + i / 60) for i in range(1, count + 1)]
        clock.now += stall
        runner.ingest_batch(ticket, samples)
        assert runner.tick_count == 0
        assert runner._execution.instance.provider.trial_state() == initial
        assert runner._execution.runtime.source.last_received_at_s == samples[-1][1]
        runner.advance(ticket)
        assert runner.status == "running" and runner.tick_count == 1
        assert runner.processed_input_sequence == count
    finally:
        runner.close()


def test_intermediate_trigger_release_changes_next_sign_without_replaying_motion(tmp_path):
    runner, ticket, clock = prepared(tmp_path, ticks=100)
    try:
        runner.start(ticket, input_provenance={"source": "synthetic-trigger"})
        runner.ingest(ticket, wire_message(sequence=0), received_at_s=clock())
        runner.advance(ticket)
        clock.now += .1
        runner.ingest_batch(ticket, [
            (wire_message(sequence=1, triggers=(.55, 0)), 10.01),
            (wire_message(sequence=2, triggers=(.55, 0), held=(4,)), 10.02),
            (wire_message(sequence=3, held=(4,)), 10.03),
            (wire_message(sequence=4, triggers=(.55, 0), held=(4,)), 10.04),
        ])
        assert runner.tick_count == 0
        runner.advance(ticket)
        last = runner._execution.runtime.runtime._last
        assert last.endpoints[0].velocity_m_s[2] < 0
        assert runner.tick_count == 1
    finally:
        runner.close()


def test_initial_old_neutral_in_batch_waits_for_fresh_neutral(tmp_path):
    runner, ticket, clock = prepared(tmp_path, ticks=100)
    try:
        runner.start(ticket, input_provenance={"source": "synthetic"})
        clock.now += .25
        runner.ingest_batch(ticket, [(message(i, (0, 0, 0, 0) if i == 0 else (.3, 0, 0, 0)),
                                     10 + i / 60) for i in range(16)])
        runner.advance(ticket)
        assert runner.status == "waiting_input" and runner._execution.state == "waiting_neutral"
        runner.advance(ticket)
        assert runner.tick_count == 0
        runner.ingest(ticket, message(16), received_at_s=clock())
        runner.advance(ticket)
        assert runner.status == "running" and runner.tick_count == 0
        runner.advance(ticket)
        assert runner.tick_count == 1
    finally:
        runner.close()


@pytest.mark.parametrize("bad", ["disconnect", "hidden", "session", "sequence", "schema", "device",
                                "source_time", "nonfinite_time", "future", "pretrial"])
def test_invalid_intermediate_sample_is_not_discarded(tmp_path, bad):
    runner, ticket, clock = prepared(tmp_path, ticks=100)
    try:
        start(runner, ticket, clock)
        value = json.loads(message(1))
        if bad == "disconnect": value["gamepad"]["connected"] = False
        elif bad == "hidden": value["gamepad"]["stale"] = True
        elif bad == "session": value["metadata"]["viewer_provider_session_id"] = "different"
        elif bad == "sequence": value["sequence"] = 0
        elif bad == "schema": value["provider_schema"] = "legacy/v0"
        elif bad == "device": value["gamepad"]["id"] = "changed-device"
        elif bad == "source_time": value["timestamp_s"] = -1
        elif bad == "nonfinite_time": value["timestamp_s"] = float("nan")
        receipt = 10.1 if bad == "future" else 9.99 if bad == "pretrial" else 10.01
        clock.now += .05
        with pytest.raises(ValueError):
            runner.ingest_batch(ticket, [(json.dumps(value), receipt), (message(2), 10.04)])
        assert runner.status == "terminal" and runner.tick_count == 0
        assert runner.result.to_document()["runner_stop_reason"] == "technical_invalid"
    finally:
        runner.close()


def test_real_source_gap_and_no_sample_remain_strict(tmp_path):
    runner, ticket, clock = prepared(tmp_path, ticks=100)
    try:
        start(runner, ticket, clock)
        clock.now += .201
        runner.advance(ticket)
        assert "input_stale_or_future" in runner.result.to_document()["error"]
        assert runner.tick_count == 0
        runner.retry()
        ticket = runner.ticket
        start(runner, ticket, clock)
        clock.now += .201
        runner.ingest_batch(ticket, [(message(1), clock())])
        assert runner.processed_input_diagnostics["last_receipt_gap_s"] == pytest.approx(.201)
        runner.advance(ticket)
        assert runner.status == "running" and runner.tick_count == 1
    finally:
        runner.close()


def test_deferred_tick_still_enforces_wall_budget(tmp_path):
    runner, ticket, clock = prepared(tmp_path, ticks=100, wall_s=.1)
    try:
        start(runner, ticket, clock)
        clock.now += .101
        runner.advance(ticket, pending_input=lambda: None)
        assert runner.result.to_document()["runner_stop_reason"] == "wall_timeout"
        assert runner.tick_count == 0
    finally:
        runner.close()


@pytest.mark.parametrize("stop_queued", [False, True, "close"])
@pytest.mark.parametrize("backlog_count", [15, 65])
def test_worker_drains_available_batch_and_prioritizes_stop(tmp_path, monkeypatch, stop_queued, backlog_count, owner_projection):
    from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
    import xpotato_sim.runtime.application.workbench_worker as module
    now = [10.]
    events = []
    commands = [{"op": "prepare", "id": "p", "generation": 1, "profile_id": "fast-arm-bimanual-gamepad"},
                {"op": "start", "id": "start", "generation": 1}]
    ticket = [None]
    runners = []
    injected = [False]
    processed = [None]
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    def make_runner(**kw):
        runner = TrialRunner(**kw, clock=lambda: now[0])
        runners.append(runner)
        return runner
    monkeypatch.setattr(module, "TrialRunner", make_runner)

    class Wire:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send(self, raw):
            event = json.loads(raw)
            events.append(event)
            if runners:
                processed[0] = runners[0].processed_input_sequence
            if event.get("id") == "start":
                ticket[0] = event["state"]["ticket"]
                commands.extend([{"op": "input", "ticket": ticket[0], "message": message(), "received_at_s": 10.}, None])
            if injected[0] and event.get("type") == "frame" and event["payload"]["time_s"] > 0:
                commands.append({"op": "close"})
        def recv(self, **kwargs):
            if not injected[0] and runners[0].status == "running":
                assert runners[0].tick_count == 0
                injected[0] = True
                commands.extend([{"op": "input", "ticket": ticket[0], "message": message(i, (.3, 0, 0, 0)),
                                  "received_at_s": 10 + .25 * i / backlog_count} for i in range(1, backlog_count + 1)])
                if stop_queued: commands.append({"op": "close"} if stop_queued == "close" else
                                               {"op": "stop", "id": "stop", "generation": 2})
                commands.extend([None, {"op": "close"}] if stop_queued else [None, None])
                now[0] = 10.25
            if not commands:
                if kwargs["timeout"] == 0:
                    raise TimeoutError
                assert now[0] == 10., "worker failed to publish its armed state"
                now[0] = 10.02
                raise TimeoutError
            command = commands.pop(0)
            if command is None: raise TimeoutError
            return json.dumps(command)
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.AsyncWorkerConnection", lambda *a, **kw: Wire())
    module.execution_worker("ws://test", {"result_root": str(tmp_path / "results"),
        "asset_root": str(tmp_path / "assets"), "software_revision": "test", "ticks": 100,
        "input_wait_s": 5, "wall_s": 30, "prepare_s": 30})
    assert injected[0], "backlog was never injected"
    print(json.dumps({"backlog_injected": backlog_count, "stop_queued": stop_queued,
                      "processed_sequence": processed[0]}))
    if stop_queued == "close":
        assert not any(e.get("type") == "frame" and e["payload"]["time_s"] > 0 for e in events)
        assert not any(e.get("error") for e in events)
    elif stop_queued:
        stopped = next(e for e in events if e.get("id") == "stop")
        assert stopped["state"]["result"]["runner_stop_reason"] == "operator_abort"
        assert stopped["state"]["ticks"] == 0
    else:
        frames = [e for e in events if e.get("type") == "frame"]
        assert frames[-1]["payload"]["time_s"] == pytest.approx(1 / 60)
        assert not any(e.get("error") for e in events)
        assert processed[0] == backlog_count


class _WorkerDeadlineExpired(TimeoutError):
    pass


@pytest.mark.parametrize("source_gap", [False, True])
def test_slow_display_sender_keeps_execution_and_real_source_gap_is_stale(tmp_path, monkeypatch, source_gap):
    _exercise_stalled_worker(tmp_path, monkeypatch, .350, "slow_display", source_gap=source_gap)


def _stalled_worker_process(url, config, stall, position, injected):
    """native実行ownerを専用processへ隔離し、親から期限付きで終了可能にする。"""
    from time import sleep
    from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
    from xpotato_sim.runtime.application.workbench_worker import execution_worker
    if position == "slow_display":
        from xpotato_sim.runtime.application import workbench_worker as module
        original_connect = module.AsyncWorkerConnection
        class SlowDisplay(original_connect):
            def send(self, raw):
                if json.loads(raw).get("type") == "frame":
                    injected.set();sleep(stall)
                return super().send(raw)
        module.AsyncWorkerConnection = SlowDisplay
        execution_worker(url, config)
        return
    original = TrialRunner.advance
    def advance(runner, ticket, **kwargs):
        inject = runner.tick_count == 5 and not injected.is_set()
        if inject and position == "after_drain":
            pending = kwargs["pending_input"]
            def delayed_pending():
                samples = pending()
                if not injected.is_set():
                    injected.set()
                    sleep(stall)
                return samples
            kwargs["pending_input"] = delayed_pending
        if inject and position == "before_advance":
            injected.set()
            sleep(stall)
        result = original(runner, ticket, **kwargs)
        if inject and position == "after_advance":
            injected.set()
            sleep(stall)
        return result
    TrialRunner.advance = advance
    execution_worker(url, config)


def _exercise_stalled_worker(tmp_path, monkeypatch, stall, position, *,
                             acknowledge_stop=True, deadline_s=None, source_gap=False):
    from multiprocessing import get_context
    from threading import Thread, Event
    from time import monotonic
    import os
    from websockets.sync.server import serve
    events, errors, supply, connections, producers = [], [], [], [], []
    finished, cancel = Event(), Event()
    context = get_context("spawn")
    injected = context.Event()
    sample_count = int(os.environ.get("XPOTATO_TEST_INPUT_SAMPLES", "120"))
    assert 120 <= sample_count <= 4000
    expires = monotonic() + (sample_count / 60 + 45 if deadline_s is None else deadline_s)
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    def remaining():
        left = expires - monotonic()
        if left <= 0:
            raise TimeoutError("actual-worker test deadline exceeded")
        return min(left, 1.0)
    def handler(ws):
        connections.append(ws)
        producer = None
        try:
            assert json.loads(ws.recv(timeout=remaining()))["op"] == "worker"
            ws.send(json.dumps({"op": "prepare", "id": "p", "generation": 1,
                                "profile_id": "fast-arm-bimanual-gamepad"}))
            while not cancel.is_set():
                try:
                    raw = ws.recv(timeout=remaining())
                except TimeoutError:
                    remaining()
                    continue
                event = json.loads(raw)
                if event.get("type") != "frame":
                    event["test_received_at_s"] = monotonic()
                    events.append(event)
                if event.get("id") == "p":
                    ws.send(json.dumps({"op": "start", "id": "start", "generation": 1}))
                if event.get("id") == "start":
                    ticket = event["state"]["ticket"]
                    def produce():
                        try:
                            # fixture I/O・検証は供給開始前に一度だけ行う。
                            template = json.loads(message())
                            began = monotonic()
                            diagnostics = {"samples": 0, "max_receipt_gap_s": 0.,
                                           "max_send_duration_s": 0., "last_receipt_s": None}
                            supply.append(diagnostics)
                            for i in range(sample_count):
                                if source_gap and i == 30:
                                    if cancel.wait(.350): return
                                if cancel.wait(max(0, began + i / 60 - monotonic())):
                                    return
                                remaining()
                                axes = (0, 0, 0, 0) if i == 0 else (.15 if (i // 30) % 2 else -.15, 0, 0, 0)
                                template.update(sequence=i, timestamp_s=i / 60)
                                template["gamepad"].update(raw_axes=list(axes), axes=list(axes), zero_state=not any(axes))
                                receipt = monotonic()
                                previous = diagnostics["last_receipt_s"]
                                if previous is not None:
                                    diagnostics["max_receipt_gap_s"] = max(diagnostics["max_receipt_gap_s"], receipt - previous)
                                diagnostics.update(samples=i + 1, last_receipt_s=receipt)
                                ws.send(json.dumps({"op": "input", "ticket": ticket,
                                    "message": json.dumps(template), "received_at_s": receipt}))
                                diagnostics["max_send_duration_s"] = max(diagnostics["max_send_duration_s"], monotonic() - receipt)
                            diagnostics["duration_s"] = monotonic() - began
                            ws.send(json.dumps({"op": "stop", "id": "stop", "generation": 2}))
                        except Exception as exc:
                            errors.append(exc)
                            cancel.set()
                            ws.close()
                    producer = Thread(target=produce, daemon=True)
                    producers.append(producer)
                    producer.start()
                if event.get("id") == "stop" and acknowledge_stop:
                    ws.send('{"op":"close"}')
                    break
        except Exception as exc:
            errors.append(exc)
        finally:
            cancel.set()
            ws.close()
            if producer:
                producer.join(timeout=5)
                if producer.is_alive(): errors.append(RuntimeError("producer failed to stop"))
            finished.set()
    with serve(handler, "127.0.0.1", 0, close_timeout=1) as server:
        serving = Thread(target=server.serve_forever, daemon=True)
        serving.start()
        worker = context.Process(target=_stalled_worker_process, args=(
            f"ws://127.0.0.1:{server.socket.getsockname()[1]}", {
                "result_root": str(tmp_path / "results"), "asset_root": str(tmp_path / "assets"),
                "software_revision": "test", "ticks": sample_count + 1000, "input_wait_s": 5,
                "wall_s": sample_count / 60 + 10, "prepare_s": 30}, stall, position, injected))
        started = False
        try:
            worker.start()
            started = True
            while worker.is_alive() and monotonic() < expires:
                worker.join(timeout=min(1, max(0, expires - monotonic())))
                last = next((e for e in reversed(events) if e.get("state")), {})
                (tmp_path / "wire-progress.json").write_text(json.dumps({
                    "events": len(events), "supply": supply, "last_state": last.get("state"),
                    "last_error": last.get("error"), "errors": [str(e) for e in errors],
                    "producer_alive": [t.is_alive() for t in producers],
                    "injected": injected.is_set(), "seconds_left": expires - monotonic(),
                }), encoding="utf-8")
            if worker.is_alive() or any(isinstance(e, TimeoutError) for e in errors):
                raise _WorkerDeadlineExpired("actual-worker test deadline exceeded")
            assert worker.exitcode == 0, (worker.exitcode, errors)
            assert finished.wait(timeout=max(0, expires - monotonic())), "handler deadline exceeded"
        finally:
            cancel.set()
            for ws in tuple(connections):
                ws.close()
            if started and worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
            if started and worker.is_alive():
                worker.kill()
                worker.join(timeout=5)
            server.shutdown()
            serving.join(timeout=5)
            assert not started or not worker.is_alive(), "owned worker survived deadline cleanup"
            assert not serving.is_alive(), "owned server survived deadline cleanup"
            if connections:
                assert finished.wait(5), "owned handler survived deadline cleanup"
            assert all(not t.is_alive() for t in producers), "owned producer survived cleanup"
            if started:
                worker.close()
    assert not errors, errors
    assert injected.is_set()
    stopped = next(e for e in events if e.get("id") == "stop")
    expected = "technical_invalid" if source_gap else "operator_abort"
    if stopped["state"]["result"]["runner_stop_reason"] != expected:
        print(json.dumps({"unexpected_terminal": stopped["state"]["result"],
                          "input_diagnostics": stopped["state"].get("input_diagnostics"),
                          "supply": supply, "injected": injected.is_set(),
                          "first_errors": [e.get("error") for e in events if e.get("error")][:8]}))
    assert stopped["state"]["result"]["runner_stop_reason"] == expected, stopped
    if source_gap:
        assert "input_stale_or_future" in stopped["state"]["result"]["error"]
        assert supply[0]["max_receipt_gap_s"] > .2
    else:
        assert stopped["state"]["ticks"] > 60
        assert stopped["state"]["input_diagnostics"]["processed_sequence"] >= sample_count - 5
        assert supply[0]["max_receipt_gap_s"] <= .2, "producer itself lost continuous supply"
    assert not any(e.get("error") for e in events)
    assert supply[0]["duration_s"] >= (sample_count - 1) / 60
    print(json.dumps({"actual_wire": True, "stall_s": stall, "position": position,
                     "supply": supply[0], "ticks": stopped["state"]["ticks"],
                     "processed_sequence": stopped["state"]["input_diagnostics"]["processed_sequence"],
                     "result": stopped["state"]["result"]["runner_stop_reason"]}))


@pytest.mark.parametrize("stall", [.190, .250, .700])
@pytest.mark.parametrize("position", ["before_advance", "after_advance"])
def test_actual_worker_websocket_with_continuous_input_during_stall(tmp_path, monkeypatch, stall, position):
    """実wireで継続供給し、owner stallを投入。全process/socketの失敗時寿命も有限。"""
    _exercise_stalled_worker(tmp_path, monkeypatch, stall, position)


def test_actual_worker_websocket_61_seconds(tmp_path, monkeypatch):
    """全CIでも短いsnapshotだけでなく、61秒の継続操作と700ms停滞を確認する。"""
    monkeypatch.setenv("XPOTATO_TEST_INPUT_SAMPLES", "3661")
    _exercise_stalled_worker(tmp_path, monkeypatch, .700, "before_advance")


def test_actual_worker_stall_after_drain(tmp_path, monkeypatch):
    _exercise_stalled_worker(tmp_path, monkeypatch, .210, "after_drain")


def test_actual_worker_true_source_gap(tmp_path, monkeypatch):
    _exercise_stalled_worker(tmp_path, monkeypatch, .01, "before_advance", source_gap=True)


def test_actual_worker_missing_close_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("XPOTATO_TEST_INPUT_SAMPLES", "120")
    with pytest.raises(_WorkerDeadlineExpired, match=r"^actual-worker test deadline exceeded$"):
        _exercise_stalled_worker(tmp_path, monkeypatch, .01, "before_advance", acknowledge_stop=False, deadline_s=6)


@pytest.mark.parametrize("limit", ["wall", "input_wait"])
def test_continuous_full_batches_do_not_bypass_supervision(tmp_path, monkeypatch, limit, owner_projection):
    from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
    import xpotato_sim.runtime.application.workbench_worker as module
    now, runners, events, bursts = [10.], [], [], [0]
    commands = [{"op": "prepare", "id": "p", "generation": 1,
                 "profile_id": "fast-arm-bimanual-gamepad"},
                {"op": "start", "id": "start", "generation": 1}]
    ticket = [None]
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    def make_runner(**kw):
        runner = TrialRunner(**kw, clock=lambda: now[0])
        runners.append(runner)
        return runner
    monkeypatch.setattr(module, "TrialRunner", make_runner)
    class Wire:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send(self, raw):
            event = json.loads(raw)
            events.append(event)
            if event.get("id") == "start": ticket[0] = event["state"]["ticket"]
        def recv(self, **kw):
            if not commands and ticket[0]:
                if runners[0].status == "terminal": return '{"op":"close"}'
                assert bursts[0] < 10, "full batches bypassed supervision"
                now[0] += .03
                first = bursts[0] * 64
                commands.extend({"op": "input", "ticket": ticket[0],
                    "message": message(i, (.1, 0, 0, 0)), "received_at_s": now[0]}
                    for i in range(first, first + 64))
                bursts[0] += 1
            return json.dumps(commands.pop(0))
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.AsyncWorkerConnection", lambda *a, **kw: Wire())
    module.execution_worker("ws://test", {"result_root": str(tmp_path / "results"),
        "asset_root": str(tmp_path / "assets"), "software_revision": "test", "ticks": 100,
        "input_wait_s": .1 if limit == "input_wait" else 5,
        "wall_s": .1 if limit == "wall" else 30, "prepare_s": 30})
    terminal = next(e["state"] for e in events if e.get("state", {}).get("phase") == "terminal")
    assert terminal["result"]["runner_stop_reason"] == limit + "_timeout"
    assert terminal["ticks"] == 0 and bursts[0] >= 3
    print(json.dumps({"full_batches": bursts[0], "supervision": limit,
                      "processed_sequence": terminal["input_diagnostics"]["processed_sequence"]}))
