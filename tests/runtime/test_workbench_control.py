"""公開要求の境界。physics/結果は別のnative統合試験で確認する。"""
import asyncio
import json

import pytest

from xpotato_sim.runtime.runners.workbench import WorkbenchControl, Peer, decode_request, profile_catalog


KEY = "ephemeral-test-capability-not-a-real-secret"
TICKET = {"trial_id": "a" * 32, "epoch": "trial-a", "condition_sha256": "b" * 64}


def controller():
    c = WorkbenchControl([{"id": "known", "available": True}], KEY)
    c.command("owner", {"op": "claim", "capability": KEY})
    return c


def request(c, op, **extra):
    return {"op": op, "capability": KEY, "id": "request-1", "revision": c.revision,
            "ticket": c.state["ticket"], **extra}


@pytest.mark.parametrize("raw", ["null", "[]", "1", '"text"', '{"op":"status","op":"start"}',
    '{"op":"status","x":NaN}', "{", " " * 65537, b'{}', '{"op":1}'],
    ids=["null", "list", "integer", "string", "duplicate", "nonfinite", "syntax", "oversize", "binary", "op-type"])
def test_invalid_wire_is_rejected(raw):
    with pytest.raises((ValueError, TypeError)):
        decode_request(raw)


def test_catalog_has_existing_profiles_and_precise_unavailable_reason():
    rows = profile_catalog()
    assert len(rows) == len({r["id"] for r in rows})
    assert all(r["available"] or r["reason"] for r in rows)
    assert {r["id"] for r in rows if r["available"]} >= {
        "dynamic-cube-drop", "dynamic-cube-push", "dynamic-fixed-contact",
        "fast-arm-single-gamepad", "fast-arm-bimanual-gamepad", "contact-debug-single"}


def test_duplicate_revision_profile_id_and_active_mutation():
    c = controller()
    for name in ("../profile", "/etc/file", {"path": "known"}, "unknown"):
        with pytest.raises(ValueError):
            c.command("owner", request(c, "prepare", profile_id=name))
        assert c.revision == 0
    req = request(c, "prepare", profile_id="known")
    response, command = c.command("owner", req)
    assert response["type"] == "accepted" and not response["completed"]
    assert command["generation"] == 1
    assert c.command("owner", req) == (response, None)
    with pytest.raises(ValueError, match="内容変更"):
        c.command("owner", {**req, "profile_id": "other"})
    with pytest.raises(ValueError, match="旧revision"):
        c.command("owner", {**req, "id": "different"})
    with pytest.raises(ValueError, match="処理中"):
        c.command("owner", request(c, "prepare", id="new", profile_id="known"))
    c.busy = None
    c.state.update(phase="running", ticket=TICKET)
    with pytest.raises(ValueError):
        c.command("owner", request(c, "prepare", id="new", profile_id="known"))


def test_owner_arbitration_cannot_sabotage_active_trial():
    c = controller()
    c.state.update(phase="running", ticket=TICKET)
    for op in ("stop", "prepare", "start", "input"):
        with pytest.raises(ValueError, match="所有者"):
            c.command("observer", request(c, op))
        assert c.state["phase"] == "running" and c.busy is None
    with pytest.raises(ValueError):
        c.command("observer", {"op": "claim", "capability": KEY})
    with pytest.raises(ValueError, match="制御資格"):
        c.command("owner", {**request(c, "stop"), "capability": "wrong"})
    status, command = c.command("observer", {"op": "status"})
    assert command is None and status["phase"] == "running"
    assert KEY not in json.dumps(status)


def test_start_requires_current_renderer_ack_and_old_epoch_rejected():
    c = controller()
    c.state.update(phase="ready", ticket=TICKET)
    with pytest.raises(ValueError, match="shader"):
        c.command("owner", request(c, "start"))
    c.command("owner", request(c, "renderer_ready", id="ack"))
    accepted, command = c.command("owner", request(c, "start", id="start"))
    assert command["op"] == "start"
    c.update({"id": "start", "state": {"phase": "running"}})
    for ticket in (None, {**TICKET, "epoch": "old"}):
        with pytest.raises(ValueError):
            c.command("owner", {"op": "input", "capability": KEY, "ticket": ticket, "message": "{}"})
    assert c.state["phase"] == "running"
    with pytest.raises(ValueError):
        c.command("owner", request(c, "start", id="duplicate"))


def test_bounded_history_and_result_list_preserve_current_state():
    c = controller()
    c.state.update(phase="ready", ticket=TICKET)
    for index in range(160):
        c.command("owner", request(c, "renderer_ready", id=f"ack-{index}"))
        c.update({"state": {"result": {"trial_id": str(index)}}})
    assert len(c.history) == 128 and len(c.results) == 32
    assert c.results[-1]["trial_id"] == "159"
    with pytest.raises(ValueError, match="旧revision"):
        c.command("owner", request(c, "renderer_ready", id="ack-0", revision=0))


def test_frame_coalescing_never_overwrites_control():
    p = Peer(None)
    for i in range(1000):
        p.put({"type": "frame", "index": i})
    p.put({"type": "completed", "id": "end"})
    p.put({"type": "status", "phase": "terminal"})
    assert p.frame == {"type": "frame", "index": 999}
    assert p.control.get_nowait()["id"] == "end"
    assert p.control.get_nowait()["phase"] == "terminal"
    for i in range(32):
        p.put({"type": "completed", "id": i})
    with pytest.raises(asyncio.QueueFull):
        p.put({"type": "completed", "id": "overflow"})


def test_stop_has_unique_deadline_and_only_verified_completion_clears_it():
    c = controller()
    req = request(c, "prepare", profile_id="known")
    c.command("owner", req)
    prepare_generation = c.generation
    stop = c.supervise_stop()
    deadline = c.stop_deadline
    assert stop["id"] != req["id"] and deadline is not None
    assert c.command("owner", req)[0]["completed"]
    for event in (
        {"id": req["id"], "generation": prepare_generation, "state": {"phase": "ready"}},
        {"id": None, "generation": c.generation, "state": {"phase": "ready"}},
        {"id": stop["id"], "generation": c.generation, "state": {"phase": "terminal"}},
        {"id": stop["id"], "generation": c.generation, "completed_op": "stop", "state": {"phase": "running"}},
    ):
        assert not c.worker_event(event)
        assert c.stop_deadline == deadline
    assert c.supervise_stop() is None and c.stop_deadline == deadline
    assert c.worker_event({"id": stop["id"], "generation": c.generation,
        "completed_op": "stop", "state": {"phase": "unselected"}})
    assert c.stop_deadline is None and c.busy is None
    assert not c.worker_event({"generation": prepare_generation, "state": {"phase": "ready"}})


def test_dead_worker_cannot_revive_and_history_distinguishes_completion():
    c = controller()
    req = request(c, "prepare", profile_id="known")
    c.command("owner", req)
    assert not c.command("owner", req)[0]["completed"]
    c.dead = True
    c.state["phase"] = "faulted"
    assert not c.worker_event({"generation": c.generation, "state": {"phase": "ready"}})
    assert c.state["phase"] == "faulted"
    c.complete(req["id"], "worker died")
    replay = c.command("owner", req)[0]
    assert replay["completed"] and replay["error"] == "worker died"


@pytest.mark.parametrize("phase", ["faulted", "recording_failed", "unselected", "terminal"])
def test_explicit_prepare_is_recovery_not_automatic_start(phase):
    c = controller()
    c.state["phase"] = phase
    _, command = c.command("owner", request(c, "prepare", profile_id="known"))
    assert command["op"] == "prepare" and c.renderer_epoch is None


def test_recording_failure_cannot_retry_failed_trial():
    c = controller()
    c.state["phase"] = "recording_failed"
    with pytest.raises(ValueError):
        c.command("owner", request(c, "retry"))
    assert c.generation == 0 and c.busy is None


def test_overflow_json_number_rejected():
    with pytest.raises(ValueError):
        decode_request('{"op":"status","n":1e999}')
