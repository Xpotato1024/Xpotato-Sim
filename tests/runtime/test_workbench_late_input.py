"""実worker/実MuJoCoで同ticket終端入力を再現する。利用者環境の再現ではない。"""
import json
from pathlib import Path

import pytest
from test_workbench_worker import owner_projection
pytestmark=pytest.mark.usefixtures("owner_projection")

from xpotato_sim.runtime.experiment.edited_condition import preset_condition
from xpotato_sim.runtime.experiment.trial_runner import TrialRunner
from xpotato_sim.runtime.application import workbench_worker as module
from xpotato_sim.runtime.application.workbench_control import WorkbenchControl


def neutral(sequence=0):
    fixture = Path(__file__).parents[1] / "fixtures/trial_gamepad/short-movement.json"
    value = json.loads(fixture.read_text(encoding="utf8"))["samples"][0]["message"]
    value.update(sequence=sequence, timestamp_s=sequence * .04)
    return json.dumps(value)


@pytest.mark.parametrize("ending", ["budget", "task", "stale", "stop"])
@pytest.mark.parametrize("late_bad", [None, "malformed", "old_ticket"])
def test_native_worker_late_input_preserves_result_and_retry_isolates_ticket(tmp_path, monkeypatch, ending, late_bad):
    now = [10.0]
    events, saved, tickets, runners = [], {}, {}, []
    condition = preset_condition("dynamic-cube-drop")
    condition["limits"]["max_ticks"] = 4 if ending == "budget" else 100
    if ending == "task":
        condition["task"]["parameters"]["duration_s"] = .05
    commands = ["prepare", "start", "neutral", "finish", "late", "retry", "old", "new_ready", "close"]
    if late_bad:
        commands[4:5] = ["late_batch", "late_bad", "late"]
    sequence = 0
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
            state = event.get("state", {})
            if event.get("id") == "prepare":
                tickets["old"] = state["ticket"]
            if event.get("id") == "retry":
                tickets["new"] = state["ticket"]
            if state.get("phase") == "terminal" and not saved:
                saved.update({p: p.read_bytes() for p in (tmp_path / "results").rglob("*.json")})

        def recv(self, **kw):
            nonlocal sequence
            op = commands[0]
            if op == "finish":
                if runners[0].status != "terminal":
                    now[0] += .02
                    assert now[0] < 12, "bounded worker failed to terminate"
                    if ending == "stop":
                        return json.dumps({"op": "stop", "id": "stop", "generation": 2})
                    if ending != "stale" and round((now[0] - 10) / .02) % 2 == 0:
                        sequence += 1
                        return json.dumps({"op": "input", "ticket": tickets["old"],
                                           "message": neutral(sequence), "received_at_s": now[0]})
                    raise TimeoutError
                commands.pop(0)
                op = commands[0]
            commands.pop(0)
            generation = 2 if ending == "stop" else 1
            if op == "prepare":
                return json.dumps({"op": op, "id": op, "generation": 1,
                    "profile_id": "dynamic-cube-drop", "condition": condition})
            if op in {"neutral", "late", "old", "new_ready", "late_batch", "late_bad"}:
                sequence += 1
                ticket = tickets.get("new") if op == "new_ready" else tickets["old"]
                if op == "late_bad" and late_bad == "old_ticket":
                    ticket = {**ticket, "epoch": "different"}
                request = {"op": "input", "ticket": ticket,
                           "message": "{" if op == "late_bad" and late_bad == "malformed" else neutral(sequence),
                           "received_at_s": now[0]}
                if op not in {"late_batch", "late_bad"}: request["id"] = op
                return json.dumps(request)
            return json.dumps({"op": op, "id": op, "generation": generation})

    monkeypatch.setattr("xpotato_sim.runtime.application.workbench_worker.AsyncWorkerConnection", lambda *a, **kw: Wire())
    module.execution_worker("ws://test", {"result_root": str(tmp_path / "results"),
        "asset_root": str(tmp_path / "assets"), "software_revision": "608-regression",
        "input_wait_s": 5, "wall_s": 30, "prepare_s": 30})
    terminal = next(e["state"] for e in events if e.get("state", {}).get("phase") == "terminal")
    expected = {"budget": "simulation_budget", "task": "task_success", "stale": "technical_invalid", "stop": "operator_abort"}[ending]
    assert terminal["result"]["runner_stop_reason"] == expected
    late = next(e for e in events if e.get("id") == "late")
    assert late["error"] is None, late
    assert late["state"]["result"] == terminal["result"]
    assert late["state"]["error"] == terminal["error"]
    if late_bad:
        rejected = [e for e in events if e.get("operation") == "input" and e.get("error")]
        assert rejected, "invalid trailing late input was silently discarded"
        assert all(e["state"]["result"] == terminal["result"] for e in rejected)
    assert saved and {p: p.read_bytes() for p in saved} == saved
    old = next(e for e in events if e.get("id") == "old")
    assert old["error"] == "旧ticket"
    ready = next(e for e in events if e.get("id") == "new_ready")
    assert ready["error"] == "input requires an active trial"
    assert ready["state"]["phase"] == "ready" and ready["state"]["ticks"] == 0
    assert tickets["old"] != tickets["new"]


@pytest.mark.parametrize("phase", ["terminal", "recording_failed"])
def test_control_terminal_same_ticket_returns_status_without_forwarding(phase):
    control = WorkbenchControl([], "test")
    control.owner = "owner"
    ticket = {"trial_id": "one", "epoch": "one", "condition_sha256": "digest"}
    result = {"trial_id": "one", "runner_stop_reason": "technical_invalid", "error": "original stale"}
    control.state.update(phase=phase, ticket=ticket, result=result, error="original stale")
    request = {"op": "input", "capability": "test", "ticket": ticket, "message": neutral()}
    response, command = control.command("owner", request)
    assert response["type"] == "status" and command is None
    assert response["error"] == "original stale" and response["result"] == result
    for changed in ({"ticket": {**ticket, "epoch": "old"}}, {"message": "{"},
                    {"message": "{}"}, {"capability": "wrong"}, {"message": 123}):
        with pytest.raises((ValueError, TypeError)):
            control.command("owner", {**request, **changed})
    with pytest.raises(ValueError):
        control.command("observer", request)
    control.state["fixture_mode"] = True
    with pytest.raises(ValueError, match="fixture"):
        control.command("owner", request)
    control.state["fixture_mode"] = False
    for phase in ("ready", "unselected", "finalizing"):
        control.state["phase"] = phase
        with pytest.raises(ValueError):
            control.command("owner", request)


def test_control_stop_pending_discards_same_ticket_without_completing_stop():
    control = WorkbenchControl([], "test")
    control.owner = "owner"
    ticket = {"trial_id": "one", "epoch": "one", "condition_sha256": "digest"}
    control.state.update(phase="running", ticket=ticket)
    control.supervise_stop("stop")
    deadline, generation = control.stop_deadline, control.generation
    response, command = control.command("owner", {"op": "input", "capability": "test",
        "ticket": ticket, "message": neutral()})
    assert command is None and response["busy_operation"] == "stop"
    assert control.stop_deadline == deadline and control.generation == generation
    assert control.stop_id == "stop" and control.state["phase"] == "running"


@pytest.mark.parametrize("invalid", ["malformed", "stale", "disconnected"])
def test_terminal_invalid_input_is_rejected_without_result_mutation(invalid):
    control = WorkbenchControl([], "test")
    control.owner = "owner"
    ticket = {"trial_id": "one", "epoch": "one", "condition_sha256": "digest"}
    control.state.update(phase="terminal", ticket=ticket, result={"runner_stop_reason": "task_success"})
    before = control.status()
    value = json.loads(neutral())
    if invalid == "stale": value["gamepad"]["stale"] = True
    if invalid == "disconnected": value["gamepad"]["connected"] = False
    with pytest.raises(ValueError):
        control.command("owner", {"op": "input", "capability": "test", "ticket": ticket,
                                  "message": "{" if invalid == "malformed" else json.dumps(value)})
    assert control.status() == before
