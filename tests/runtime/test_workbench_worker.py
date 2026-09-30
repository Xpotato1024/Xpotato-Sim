"""workerの明示診断と、開始前STOP後のnative準備の復旧を検査する。"""
import json

import pytest

from xpotato_sim.runtime.runners.workbench import execution_worker


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

    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: Wire())
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

    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: Wire())
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
    import xpotato_sim.runtime.runners.workbench as module
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
    monkeypatch.setattr("websockets.sync.client.connect", lambda *a, **kw: Wire())
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
