"""workerの明示診断と、開始前STOP後のnative準備の復旧を検査する。"""
import json

import pytest

from xpotato_sim.runtime.application.workbench_worker import execution_worker


@pytest.fixture
def owner_projection(monkeypatch):
    """仮想時計testはhandoffまでを検査。実senderの待ち/closeは専用testで検査する。"""
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.perf_counter_ns",lambda:int(__import__("xpotato_sim.runtime.application.workbench_worker",fromlist=["monotonic"]).monotonic()*1e9))
    class ImmediateProjection:
        def __init__(self, wire): self.wire = wire
        def put(self, value): self.wire.send(json.dumps(value, allow_nan=False))
        def check(self): pass
        def close(self, **kwargs): pass
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.ProjectionSender", ImmediateProjection)
    class ImmediateInbox:
        def __init__(self, wire):
            self.wire=wire
            if not hasattr(wire,"close"):wire.close=lambda:None
        def recv(self, timeout):return self.wire.recv(timeout=timeout)
        def close(self):pass
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.ExecutionInbox", ImmediateInbox)
    from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
    original = TrialRunner.__init__
    def synchronous_init(self, **kwargs):
        kwargs["async_terminal_recording"] = False
        original(self, **kwargs)
    monkeypatch.setattr(TrialRunner, "__init__", synchronous_init)


pytestmark = pytest.mark.usefixtures("owner_projection")


@pytest.mark.parametrize("diagnostic", [False, True])
def test_worker_memory_profiling_is_explicit(tmp_path, monkeypatch, diagnostic):
    tracing = []
    events = []
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    monkeypatch.setattr("tracemalloc.start", lambda depth: tracing.append(depth))
    monkeypatch.setattr("tracemalloc.is_tracing", lambda: bool(tracing))
    monkeypatch.setattr("tracemalloc.get_traced_memory", lambda: (123, 456))

    class Wire:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send(self, message):
            events.append(json.loads(message))

        def recv(self, **kwargs):
            return '{"op":"close"}'

    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.AsyncWorkerConnection", lambda *args, **kwargs: Wire())
    execution_worker("ws://test", {"result_root": str(tmp_path), "software_revision": "test",
                                   "diagnostic_memory": diagnostic})
    assert tracing == ([1] if diagnostic else [])
    assert events[1]["state"]["python_heap"] == (123 if diagnostic else None)


@pytest.mark.parametrize("failed_reset", [False, True])
def test_worker_explicit_prepare_recovers_after_discard_or_reset_failure(tmp_path, monkeypatch, failed_reset):
    from xpotato_sim.runtime.execution.model_execution import ModelExecution
    events = []
    prepare = {"op": "prepare", "id": "p1", "generation": 1, "profile_id": "dynamic-cube-drop"}
    commands = [prepare]
    if not failed_reset:
        commands.append({"op": "stop", "id": "s1", "generation": 2})
    commands.extend([{**prepare, "id": "p2", "generation": 3}, {"op": "close"}])
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    original_reset = ModelExecution.reset
    first = True

    def reset(execution, epoch):
        nonlocal first
        if first and failed_reset:
            first = False
            raise RuntimeError("injected reset failure")
        return original_reset(execution, epoch)

    monkeypatch.setattr(ModelExecution, "reset", reset)

    class Wire:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send(self, value):
            events.append(json.loads(value))

        def recv(self, **kwargs):
            return json.dumps(commands.pop(0))

    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.AsyncWorkerConnection", lambda *args, **kwargs: Wire())
    execution_worker("ws://test", {"result_root": str(tmp_path / "results"),
        "asset_root": str(tmp_path / "assets"), "software_revision": "test", "ticks": 2,
        "input_wait_s": 5, "wall_s": 30, "prepare_s": 30})
    states = {event["id"]: event for event in events if event.get("type") == "worker_status" and event.get("id")}
    assert states["p1"]["state"]["phase"] == ("faulted" if failed_reset else "ready")
    if not failed_reset:
        assert states["s1"]["completed_op"] == "stop"
        assert states["s1"]["state"]["phase"] == "unselected"
        assert states["s1"]["assets"] == []
    assert states["p2"]["state"]["phase"] == "ready"
    assert states["p2"]["state"]["native_builds"] == 2
    assert states["p2"]["state"]["ticks"] == 0 and states["p2"]["assets"]
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize("operator_stop", [False, True])
def test_worker_retry_reuses_prepared_assets_after_terminal_or_stop(tmp_path, monkeypatch, operator_stop):
    """実workerのretryは空directoryを要求するasset writerを呼び直さない。"""
    from pathlib import Path
    import xpotato_sim.runtime.application.workbench_worker as module
    from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
    now = [10.0]
    events = []
    commands = [{"op": "prepare", "id": "p", "generation": 1, "profile_id": "dynamic-cube-drop"},
                {"op": "start", "id": "start", "generation": 1}]
    commands += ([{"op": "stop", "id": "stop", "generation": 2}] if operator_stop else ["wait_terminal"])
    commands += [{"op": "retry", "id": "retry", "generation": 2 if operator_stop else 1}, {"op": "close"}]
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(module, "TrialRunner", lambda **kw: TrialRunner(**kw, clock=lambda: now[0]))
    original_files = {}
    asset_root = tmp_path / "assets"
    class Wire:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send(self, value):
            event = json.loads(value)
            events.append(event)
            if event.get("id") == "p":
                original_files.update({p.relative_to(asset_root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                                       for p in asset_root.rglob("*") if p.is_file()})
        def recv(self, **kwargs):
            if commands[0] == "wait_terminal":
                if not any(e.get("state", {}).get("phase") == "terminal" for e in events):
                    now[0] += .02
                    assert now[0] < 12.0, "worker did not finish the bounded fixture"
                    raise TimeoutError
                commands.pop(0)
            return json.dumps(commands.pop(0))
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.AsyncWorkerConnection", lambda *a, **kw: Wire())
    execution_worker("ws://test", {"result_root": str(tmp_path / "results"),
        "asset_root": str(asset_root), "software_revision": "test", "ticks": 2,
        "input_wait_s": 5, "wall_s": 30, "prepare_s": 30,
        "fixture": str(Path(__file__).parents[1] / "fixtures/trial_gamepad/short-movement.json")})
    states = {e["id"]: e for e in events if e.get("type") == "worker_status" and e.get("id")}
    assert states["retry"]["error"] is None
    assert states["retry"]["state"]["phase"] == "ready"
    assert states["retry"]["assets"] == states["p"]["assets"]
    assert states["retry"]["state"]["native_builds"] == 1
    assert states["retry"]["state"]["ticket"]["epoch"] != states["p"]["state"]["ticket"]["epoch"]
    assert {p.relative_to(asset_root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in asset_root.rglob("*") if p.is_file()} == original_files


@pytest.mark.parametrize("failure", ["start.json", "terminal.json"])
def test_worker_manual_prepare_recovers_recording_failure_without_overwrite(tmp_path, monkeypatch, failure):
    from pathlib import Path
    import xpotato_sim.runtime.application.workbench_worker as module
    from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
    from xpotato_sim.runtime.experiment.trial_record import TrialRecorder
    from xpotato_sim.runtime.experiment.edited_condition import preset_condition
    now=[10.];events=[];failed_files={};failed_trial=[]
    d=preset_condition("dynamic-cube-drop");d["limits"].update(max_ticks=2,prepare_s=11.)
    commands=[{"op":"prepare","id":"p1","generation":1,"profile_id":"dynamic-cube-drop","condition":d},
        {"op":"start","id":"start","generation":1},"wait_failure",
        {"op":"retry","id":"retry","generation":1},
        {"op":"prepare","id":"p2","generation":2,"profile_id":"dynamic-cube-drop","condition":d},
        {"op":"close"}]
    original=TrialRecorder.write
    def write(recorder,name,doc):
        if name==failure and not failed_trial:raise OSError("injected persistence failure")
        return original(recorder,name,doc)
    monkeypatch.setattr(TrialRecorder,"write",write)
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY","test")
    monkeypatch.setattr(module,"monotonic",lambda:now[0])
    monkeypatch.setattr(module,"TrialRunner",lambda **kw:TrialRunner(**kw,clock=lambda:now[0]))
    class Wire:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def send(self,value):
            event=json.loads(value);events.append(event)
            state=event.get("state",{})
            if state.get("phase")=="recording_failed" and not failed_trial:
                failed_trial.append(state["ticket"]["trial_id"])
                failed_files.update({p:p.read_bytes() for p in (tmp_path/"results"/failed_trial[0]).rglob("*") if p.is_file()})
        def recv(self,**kw):
            if commands[0]=="wait_failure":
                if not failed_trial:
                    now[0]+=.02;assert now[0]<12;raise TimeoutError
                commands.pop(0)
            return json.dumps(commands.pop(0))
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.AsyncWorkerConnection",lambda *a,**kw:Wire())
    execution_worker("ws://test",{"result_root":str(tmp_path/"results"),"asset_root":str(tmp_path/"assets"),
        "software_revision":"test","ticks":2,"input_wait_s":5,"wall_s":30,"prepare_s":.01,
        "fixture":str(Path(__file__).parents[1]/"fixtures/trial_gamepad/short-movement.json")})
    states={e["id"]:e for e in events if e.get("type")=="worker_status" and e.get("id")}
    assert states["retry"]["error"] and states["retry"]["state"]["phase"]=="recording_failed"
    assert states["retry"]["state"]["result"]["recording"]=="failed"
    assert states["p2"]["state"]["phase"]=="ready" and states["p2"]["state"]["ticks"]==0
    assert states["p2"]["state"]["ticket"]["trial_id"]!=failed_trial[0]
    assert states["p2"]["state"]["ticket"]["epoch"]!=states["p1"]["state"]["ticket"]["epoch"]
    assert {p:p.read_bytes() for p in failed_files}==failed_files
    assert len(list((tmp_path/"results").iterdir()))==1


def test_worker_publishes_ready_once_and_new_ticket_promptly(tmp_path, monkeypatch):
    events = []
    commands = [{"op": "prepare", "id": "p", "generation": 1, "profile_id": "dynamic-cube-drop"},
                None, None, None, {"op": "prepare", "id": "p2", "generation": 2, "profile_id": "dynamic-cube-drop"},
                None, None, {"op": "close"}]
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    # 時計を進めなくてもprepare/reprepareの新しいticketは即座に公開される。
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.monotonic", lambda: 10.)
    class Wire:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send(self, raw): events.append(json.loads(raw))
        def recv(self, **kwargs):
            command = commands.pop(0)
            if command is None: raise TimeoutError
            return json.dumps(command)
    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.AsyncWorkerConnection", lambda *args, **kwargs: Wire())
    execution_worker("ws://test", {"result_root": str(tmp_path / "results"),
        "asset_root": str(tmp_path / "assets"), "software_revision": "test", "ticks": 2,
        "input_wait_s": 5, "wall_s": 30, "prepare_s": 30})
    frames = [event for event in events if event.get("type") == "frame"]
    assert len(frames) == 2
    assert frames[0]["ticket"] != frames[1]["ticket"]
    assert frames[0]["payload"]["qpos"] == frames[1]["payload"]["qpos"]


@pytest.mark.parametrize("failure",["sender failure","receiver closure","control FIFO overflow"])
def test_worker_transport_failure_records_technical_invalid(tmp_path,monkeypatch,failure):
    from xpotato_sim.runtime.application import workbench_worker as module
    events=[];commands=[{"op":"prepare","generation":1,"profile_id":"dynamic-cube-push"},{"op":"start"}]
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY","test")
    class Wire:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def send(self,raw):events.append(json.loads(raw))
        def recv(self,**kw):
            if commands:return json.dumps(commands.pop(0))
            raise OSError(failure)
    monkeypatch.setattr(module,"AsyncWorkerConnection",lambda *a,**kw:Wire())
    if failure != "receiver closure":
        original=module.ProjectionSender
        class BrokenSender(original):
            def check(self):
                if any(e.get("state",{}).get("phase")=="waiting_input" for e in events):raise OSError(failure)
        monkeypatch.setattr(module,"ProjectionSender",BrokenSender)
    with pytest.raises(OSError,match=failure):
        module.execution_worker("ws://test",{"result_root":str(tmp_path/"results"),"asset_root":str(tmp_path/"assets"),"software_revision":"test","ticks":100,"input_wait_s":5,"wall_s":30,"prepare_s":30})
    record=json.loads(next((tmp_path/"results").glob("*/terminal.json")).read_text())
    assert record["runner_stop_reason"]=="technical_invalid"
    assert failure in record["error"]


@pytest.mark.parametrize("failure",["handshake","runner_close"])
def test_worker_releases_connection_even_before_inbox_or_after_close_exception(tmp_path,monkeypatch,failure):
    from xpotato_sim.runtime.application import workbench_worker as module
    from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
    closed=[]
    class Wire:
        def send(self,raw):
            if failure=="handshake":raise OSError("handshake failure")
        def recv(self,**kwargs):return '{"op":"close"}'
        def close(self):closed.append(True)
    if failure=="runner_close":
        original=TrialRunner.close
        def broken(runner):original(runner);raise OSError("runner_close failure")
        monkeypatch.setattr(TrialRunner,"close",broken)
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY","test")
    monkeypatch.setattr(module,"AsyncWorkerConnection",lambda *a,**kw:Wire())
    with pytest.raises(OSError,match=failure):
        module.execution_worker("ws://test",{"result_root":str(tmp_path/"results"),"asset_root":str(tmp_path/"assets"),"software_revision":"test","input_wait_s":5,"wall_s":30,"prepare_s":30})
    assert closed


def test_overdue_worker_polls_input_without_adding_another_wait(tmp_path, monkeypatch):
    """周期超過後も入力/STOPを確認するが、存在しない入力の待ちを追加しない。"""
    from pathlib import Path
    from xpotato_sim.runtime.application import workbench_worker as module
    from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
    clock = [10.0]
    runners, events, overdue_timeouts = [], [], []
    commands = [
        {"op": "prepare", "id": "prepare", "generation": 1, "profile_id": "dynamic-cube-drop"},
        {"op": "start", "id": "start", "generation": 1},
    ]
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    def create_runner(**kwargs):
        runner = TrialRunner(**kwargs, clock=lambda: clock[0])
        runners.append(runner)
        return runner
    monkeypatch.setattr(module, "TrialRunner", create_runner)
    original = TrialRunner.advance
    def slow_advance(runner, ticket, **kwargs):
        result = original(runner, ticket, **kwargs)
        clock[0] += .022  # 16.67 ms周期を超える処理を決定的に模擬する。
        return result
    monkeypatch.setattr(TrialRunner, "advance", slow_advance)
    class Wire:
        def send(self, raw): events.append(json.loads(raw))
        def recv(self, timeout):
            if commands:
                return json.dumps(commands.pop(0))
            runner = runners[0]
            if runner.status == "terminal":
                return '{"op":"close"}'
            if runner.tick_count > 0:
                overdue_timeouts.append(timeout)
            clock[0] += timeout
            assert clock[0] < 11.0, "bounded trial failed to complete"
            raise TimeoutError
    monkeypatch.setattr(module, "AsyncWorkerConnection", lambda *args, **kwargs: Wire())
    module.execution_worker("ws://test", {
        "result_root": str(tmp_path / "results"), "asset_root": str(tmp_path / "assets"),
        "software_revision": "overdue-regression", "ticks": 5,
        "input_wait_s": 5, "wall_s": 30, "prepare_s": 30,
        "fixture": str(Path(__file__).parents[1] / "fixtures/trial_gamepad/short-movement.json"),
    })
    record = json.loads(next((tmp_path / "results").glob("*/terminal.json")).read_text())
    assert record["ticks"] == 5 and record["runner_stop_reason"] == "simulation_budget"
    assert overdue_timeouts and all(timeout == 0 for timeout in overdue_timeouts)


def test_inactive_worker_uses_bounded_wait_not_expired_deadline(tmp_path, monkeypatch):
    """未選択・readyでは古いtick期限によるbusy pollingを作らない。"""
    from xpotato_sim.runtime.application import workbench_worker as module
    commands = [{"op": "prepare", "id": "p", "generation": 1,
                 "profile_id": "dynamic-cube-drop"}, None, None, {"op": "close"}]
    timeouts = []
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY", "test")
    monkeypatch.setattr(module, "monotonic", lambda: 10.)
    class Wire:
        def send(self, raw): pass
        def recv(self, timeout):
            timeouts.append(timeout)
            command = commands.pop(0)
            if command is None:
                raise TimeoutError
            return json.dumps(command)
    monkeypatch.setattr(module, "AsyncWorkerConnection", lambda *args, **kwargs: Wire())
    module.execution_worker("ws://test", {
        "result_root": str(tmp_path / "results"), "asset_root": str(tmp_path / "assets"),
        "software_revision": "inactive-wait", "ticks": 5,
        "input_wait_s": 5, "wall_s": 30, "prepare_s": 30,
    })
    assert timeouts == [.02, .02, .02, .02]
    assert not (tmp_path / "results").exists()


def test_stage_timings_are_bounded_independent_and_resettable():
    from xpotato_sim.runtime.application.workbench_metrics import StageTimings
    stages = StageTimings(capacity=3)
    assert stages.snapshot()["advance"]["p95_ns"] is None
    for value in (1, 2, 3, 4, 5):
        stages.observe("advance", value)
    stages.observe("receive_wait", 0)
    snapshot = stages.snapshot()
    assert snapshot["advance"] == {"count": 3, "observed": 5, "last_ns": 5,
        "mean_ns": 4., "p50_ns": 4, "p95_ns": 5, "p99_ns": 5, "max_ns": 5}
    assert snapshot["receive_wait"]["count"] == 1
    assert snapshot["receive_wait"]["p95_ns"] == 0
    assert snapshot["projection"]["p95_ns"] is None
    stages.clear()
    assert all(row["count"] == 0 and row["observed"] == 0 and row["max_ns"] is None
               for row in stages.snapshot().values())
    assert snapshot["advance"]["count"] == 3  # 過去の通知snapshotを書き換えない。


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5])
def test_stage_timings_reject_invalid_capacity(capacity):
    from xpotato_sim.runtime.application.workbench_metrics import StageTimings
    with pytest.raises(ValueError):
        StageTimings(capacity=capacity)
