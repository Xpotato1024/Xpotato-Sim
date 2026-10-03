"""遅い表示送信とFIFO/障害/closeの有界責務を実threadで検査する。"""
import json
from threading import Event
from time import perf_counter
import pytest
from xpotato_sim.runtime.application.workbench_projection import ProjectionSender
from xpotato_sim.runtime.application.workbench_projection import ExecutionInbox
from queue import Queue


def test_slow_sender_does_not_block_owner_and_keeps_latest_frame():
    entered, release = Event(), Event()
    sent = []
    class Wire:
        def send(self, raw):
            entered.set()
            assert release.wait(2)
            sent.append(json.loads(raw))
    sender = ProjectionSender(Wire())
    try:
        sender.put({"type": "frame", "tick": 0})
        assert entered.wait(1)
        start = perf_counter()
        for tick in range(1, 601): sender.put({"type": "frame", "tick": tick})
        assert perf_counter() - start < .1
        assert sender.frame == {"type": "frame", "tick": 600}
        assert not sender.control
    finally:
        release.set()
        sender.close()
    assert sent == [{"type": "frame", "tick": 0}, {"type": "frame", "tick": 600}]


def test_control_overflow_and_transition_invalidates_frame():
    entered, release = Event(), Event()
    sent = []
    class Wire:
        def send(self, raw):
            entered.set(); assert release.wait(2); sent.append(json.loads(raw))
    sender = ProjectionSender(Wire(), capacity=2)
    try:
        sender.put({"type": "worker_status", "id": 0}); assert entered.wait(1)
        sender.put({"type": "frame", "epoch": "old"})
        sender.put({"type": "worker_status", "id": 1})
        assert sender.frame is None
        sender.put({"type": "worker_status", "id": 2})
        with pytest.raises(RuntimeError, match="overflow"):sender.put({"type": "worker_status", "id": 3})
    finally:
        release.set(); sender.close()
    assert [v["id"] for v in sent] == [0, 1, 2]


def test_send_failure_is_visible_and_closed_sender_rejects():
    entered = Event()
    class Wire:
        def send(self, raw):entered.set();raise OSError("injected send failure")
    sender = ProjectionSender(Wire())
    sender.put({"type": "frame"});assert entered.wait(1)
    sender.thread.join(1)
    with pytest.raises(RuntimeError, match="injected send failure"):sender.check()
    with pytest.raises(RuntimeError, match="injected send failure"):sender.close()
    class OK:
        def send(self, raw):pass
    closed = ProjectionSender(OK());closed.close()
    assert not closed.thread.is_alive()
    with pytest.raises(RuntimeError, match="closed"):closed.put({"type": "frame"})


class InputWire:
    def __init__(self):self.queue=Queue()
    def recv(self):
        raw=self.queue.get(timeout=2)
        if raw is None:raise OSError("closed")
        return raw
    def close(self):self.queue.put(None)
    def put(self, **value):self.queue.put(json.dumps(value))


def test_inbox_preserves_fifo_receipt_and_stop_has_own_slot():
    wire=InputWire();inbox=ExecutionInbox(wire)
    try:
        wire.put(op="input",received_at_s=10,sequence=1)
        wire.put(op="prepare",id="p")
        wire.put(op="input",received_at_s=10.01,sequence=2)
        with inbox.changed:
            assert inbox.changed.wait_for(lambda:len(inbox.inputs)==2 and len(inbox.control)==1,1)
        assert json.loads(inbox.recv(0))["received_at_s"]==10
        assert json.loads(inbox.recv(0))["id"]=="p"
        assert json.loads(inbox.recv(0))["sequence"]==2
        for sequence in range(200):wire.put(op="input",received_at_s=10.02,sequence=sequence)
        wire.put(op="stop",id="s")
        with inbox.changed:
            assert inbox.changed.wait_for(lambda:inbox.stop is not None,1)
        assert json.loads(inbox.recv(0))["id"]=="s"
        assert not inbox.inputs and not inbox.control
    finally:
        inbox.close()
    assert not inbox.thread.is_alive()


@pytest.mark.parametrize("op,limit", [("input",256),("prepare",32)])
def test_inbox_overflow_is_a_visible_failure(op, limit):
    wire=InputWire();inbox=ExecutionInbox(wire)
    try:
        for number in range(limit+1):wire.put(op=op,sequence=number,received_at_s=10)
        with inbox.changed:
            assert inbox.changed.wait_for(lambda:inbox.error is not None,1)
        with pytest.raises(RuntimeError,match="FIFO overflow"):inbox.recv(0)
    finally:
        inbox.close()
