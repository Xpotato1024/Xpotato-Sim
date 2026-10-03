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


def test_real_async_socket_backpressure_still_receives_input_and_stop():
    import asyncio
    from threading import Thread
    from time import monotonic, sleep
    from websockets.asyncio.server import serve
    from xpotato_sim.runtime.application.workbench_projection import AsyncWorkerConnection
    ready, inject, done = Event(), Event(), Event()
    address=[];errors=[];finished=Event()
    async def server():
        async def peer(ws):
            # recvを呼ばず実socketのflow controlを詰まらせる。
            while not inject.is_set():await asyncio.sleep(.005)
            await ws.send(json.dumps({"op":"input","received_at_s":123.25,"sequence":7}))
            await ws.send(json.dumps({"op":"stop","id":"urgent"}))
            while not done.is_set():await asyncio.sleep(.005)
        async with serve(peer,"127.0.0.1",0,max_queue=1,max_size=None,compression=None,close_timeout=1) as srv:
            address.append(srv.sockets[0].getsockname()[1]);ready.set()
            while not done.is_set():await asyncio.sleep(.005)
    thread=Thread(target=lambda:asyncio.run(server()),daemon=True);thread.start()
    assert ready.wait(2)
    wire=AsyncWorkerConnection(f"ws://127.0.0.1:{address[0]}",proxy=None,compression=None)
    def flood():
        try:
            for _ in range(8):wire.send("x"*(8*1024*1024))
        except Exception as exc:errors.append(exc)
        finally:finished.set()
    sending=Thread(target=flood,daemon=True);sending.start()
    try:
        async def buffer_size():return wire.ws.transport.get_write_buffer_size()
        deadline=monotonic()+5
        while asyncio.run_coroutine_threadsafe(buffer_size(),wire.loop).result()<65536 and monotonic()<deadline:sleep(.005)
        assert not finished.is_set(),"real display socket did not reach backpressure"
        assert asyncio.run_coroutine_threadsafe(buffer_size(),wire.loop).result()>=65536
        inject.set()
        assert json.loads(wire.recv(timeout=2))["received_at_s"]==123.25
        assert json.loads(wire.recv(timeout=2))["op"]=="stop"
        assert not finished.is_set(),"receive only progressed after display unblocked"
    finally:
        wire.close();done.set();sending.join(3);thread.join(3)
    assert not wire.thread.is_alive() and not sending.is_alive() and not thread.is_alive()


def test_required_ready_and_terminal_frames_use_ordered_fifo():
    entered,release=Event(),Event();sent=[]
    class Wire:
        def send(self,raw):entered.set();assert release.wait(2);sent.append(json.loads(raw))
    sender=ProjectionSender(Wire())
    try:
        sender.put({"type":"worker_status","phase":"ready"});assert entered.wait(1)
        sender.put({"type":"frame","required":True,"phase":"ready"})
        sender.put({"type":"worker_status","phase":"terminal"})
        sender.put({"type":"frame","required":True,"phase":"terminal"})
    finally:release.set();sender.close()
    assert [(v["type"],v["phase"]) for v in sent]==[("worker_status","ready"),("frame","ready"),("worker_status","terminal"),("frame","terminal")]


def test_async_connection_startup_failure_leaves_no_io_thread():
    import socket
    from threading import enumerate as threads
    from xpotato_sim.runtime.application.workbench_projection import AsyncWorkerConnection
    before={thread.ident for thread in threads() if thread.name=="workbench-async-io"}
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1",0))
        # listenしない予約socketへの接続は拒否される。
        with pytest.raises(RuntimeError,match="connection failed"):
            AsyncWorkerConnection(f"ws://127.0.0.1:{reserved.getsockname()[1]}",proxy=None)
    assert {thread.ident for thread in threads() if thread.name=="workbench-async-io"}==before


def backpressured_native_worker(url, config, blocked):
    import asyncio
    from xpotato_sim.runtime.application import workbench_worker as module
    original=module.AsyncWorkerConnection
    class SaturatedDisplay(original):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            async def observe():
                while True:
                    if self.ws.transport.get_write_buffer_size()>65536:blocked.set()
                    await asyncio.sleep(.005)
            asyncio.run_coroutine_threadsafe(observe(),self.loop)
        def send(self,raw):
            value=json.loads(raw)
            if value.get("type")=="frame" and value["payload"]["metadata"].get("motion_status")=="running":
                value["padding"]="x"*(8*1024*1024)
                raw=json.dumps(value)
            return super().send(raw)
    module.AsyncWorkerConnection=SaturatedDisplay
    module.execution_worker(url,config)


def test_native_execution_and_stop_progress_while_real_display_socket_is_blocked(tmp_path,monkeypatch):
    import asyncio
    from multiprocessing import get_context
    from threading import Thread
    from time import monotonic,sleep
    from pathlib import Path
    from websockets.asyncio.server import serve
    context=get_context("spawn");blocked=context.Event();ready,done=Event(),Event();ports=[];errors=[]
    template=json.loads((Path(__file__).parents[1]/"fixtures/trial_gamepad/short-movement.json").read_text(encoding="utf8"))["samples"][0]["message"]
    async def server():
        async def peer(ws):
            try:
                assert json.loads(await ws.recv())["op"]=="worker"
                await ws.send(json.dumps({"op":"prepare","id":"p","generation":1,"profile_id":"fast-arm-bimanual-gamepad"}))
                while True:
                    event=json.loads(await ws.recv())
                    if event.get("id")=="p":break
                ticket=event["state"]["ticket"]
                await ws.send('{"op":"start","id":"s"}')
                while json.loads(await ws.recv()).get("id")!="s":pass
                # 以後表示を受け取らない。入力とSTOPは同じ実接続で供給し続ける。
                for i in range(90):
                    template.update(sequence=i,timestamp_s=i/60)
                    await ws.send(json.dumps({"op":"input","ticket":ticket,"message":json.dumps(template),"received_at_s":monotonic()}))
                    await asyncio.sleep(1/60)
                await ws.send('{"op":"stop","id":"stop","generation":2}')
                while not done.is_set():await asyncio.sleep(.01)
                await ws.send('{"op":"close"}')
            except Exception as exc:errors.append(exc)
        async with serve(peer,"127.0.0.1",0,max_queue=1,max_size=None,compression=None,close_timeout=1) as srv:
            ports.append(srv.sockets[0].getsockname()[1]);ready.set()
            while not done.is_set():await asyncio.sleep(.01)
            await asyncio.sleep(.2)
    server_thread=Thread(target=lambda:asyncio.run(server()),daemon=True);server_thread.start();assert ready.wait(2)
    monkeypatch.setenv("XPOTATO_WORKBENCH_WORKER_KEY","test")
    worker=context.Process(target=backpressured_native_worker,args=(f"ws://127.0.0.1:{ports[0]}",{"result_root":str(tmp_path/"results"),"asset_root":str(tmp_path/"assets"),"software_revision":"backpressure","ticks":1000,"input_wait_s":5,"wall_s":10,"prepare_s":30},blocked))
    worker.start()
    try:
        deadline=monotonic()+12
        while not list((tmp_path/"results").glob("*/terminal.json")) and monotonic()<deadline:sleep(.02)
        paths=list((tmp_path/"results").glob("*/terminal.json"));assert paths,errors
        assert blocked.is_set(),"actual socket did not reach backpressure"
        record=json.loads(paths[0].read_text(encoding="utf8"))
        assert record["runner_stop_reason"]=="operator_abort",record
        assert record["ticks"]>=10
    finally:
        done.set();worker.join(5)
        if worker.is_alive():worker.terminate();worker.join(2)
        server_thread.join(3)
    assert worker.exitcode==0 and not server_thread.is_alive(),errors
    worker.close()
