"""Executionから切り離した有界表示slotと制御FIFOの唯一のsender。"""
from collections import deque
from threading import Condition, Thread
import json
import asyncio
from queue import Queue, Empty, Full
from threading import Event


class AsyncWorkerConnection:
    """公開async APIで同一接続の送受信を分離する。Executionはsocketを操作しない。"""
    def __init__(self, url, **options):
        self.url, self.options = url, options
        self.incoming = Queue(maxsize=256)
        self.ready = Event()
        self.error = None
        self.closed = False
        self.loop = None
        self.thread = Thread(target=self._run, name="workbench-async-io", daemon=True)
        self.thread.start()
        if not self.ready.wait(15):
            self.close()
            raise RuntimeError("worker connection startup timeout")
        if self.error is not None:
            self.thread.join(2)
            raise RuntimeError(f"worker connection failed: {self.error}") from self.error

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:
            self.error = exc
        finally:
            self.closed = True
            self.ready.set()

    async def _serve(self):
        from websockets.asyncio.client import connect
        self.loop = asyncio.get_running_loop()
        async with connect(self.url, **self.options, open_timeout=10, close_timeout=1) as self.ws:
            self.stopping = asyncio.Event()
            self.ready.set()
            async def receive():
                async for raw in self.ws:
                    try:
                        self.incoming.put_nowait(raw)
                    except Full as exc:
                        raise RuntimeError("async ingress FIFO overflow") from exc
                if not self.closed:
                    raise RuntimeError("worker connection closed by peer")
            reader = asyncio.create_task(receive())
            stop = asyncio.create_task(self.stopping.wait())
            try:
                done, _ = await asyncio.wait((reader, stop), return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    await reader
            finally:
                reader.cancel(); stop.cancel()
                await asyncio.gather(reader, stop, return_exceptions=True)

    def send(self, raw):
        if self.error is not None or self.closed:
            raise RuntimeError(f"worker connection unavailable: {self.error}")
        future = asyncio.run_coroutine_threadsafe(self.ws.send(raw), self.loop)
        while True:
            from concurrent.futures import TimeoutError as FutureTimeout
            try:
                return future.result(timeout=.05)
            except FutureTimeout:
                if self.closed or self.error is not None:
                    future.cancel()
                    raise RuntimeError(f"async send interrupted: {self.error}")

    def recv(self, timeout=None):
        while True:
            try:
                return self.incoming.get_nowait()
            except Empty:
                pass
            if self.error is not None:
                raise RuntimeError(f"worker connection receive failed: {self.error}") from self.error
            if self.closed:
                raise RuntimeError("worker connection closed")
            try:
                return self.incoming.get(timeout=.05 if timeout is None else timeout)
            except Empty:
                if timeout is not None:
                    raise TimeoutError

    def close(self):
        if not self.closed:
            self.closed = True
            if self.thread.is_alive() and self.loop is not None and not self.loop.is_closed() and hasattr(self, "stopping"):
                self.loop.call_soon_threadsafe(self.stopping.set)
        self.thread.join(3)
        if self.thread.is_alive():
            raise RuntimeError("async worker IO did not close")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        # ownerがtechnical_invalidを記録してからfinallyで接続を解放する。
        pass


class ExecutionInbox:
    """元receiptを保持する入力FIFO、制御FIFO、専用STOP通知のsocket reader。"""
    def __init__(self, wire):
        self.wire = wire
        self.control, self.inputs = deque(), deque()
        self.stop = None
        self.error = None
        self.closed = False
        self.changed = Condition()
        self.thread = Thread(target=self._read, name="workbench-ingress", daemon=True)
        self.thread.start()

    def _read(self):
        ordinal = 0
        try:
            while not self.closed:
                raw = self.wire.recv()
                value = json.loads(raw)
                with self.changed:
                    if value["op"] in {"stop", "close"}:
                        if self.stop is not None and value["op"] != "close":
                            raise RuntimeError("execution STOP slot overflow")
                        self.stop = raw
                        self.inputs.clear(); self.control.clear()
                    else:
                        queue = self.inputs if value["op"] == "input" else self.control
                        limit = 256 if value["op"] == "input" else 32
                        if len(queue) >= limit:
                            raise RuntimeError("execution ingress FIFO overflow")
                        ordinal += 1
                        queue.append((ordinal, raw))
                    self.changed.notify_all()
        except Exception as exc:
            with self.changed:
                if not self.closed:self.error = exc
                self.changed.notify_all()

    def recv(self, timeout):
        with self.changed:
            if not self.changed.wait_for(lambda: self.stop is not None or self.control or self.inputs or self.error is not None, timeout):
                raise TimeoutError
            if self.stop is not None:
                raw, self.stop = self.stop, None
                return raw
            if self.error is not None:
                raise RuntimeError(f"execution ingress failed: {self.error}") from self.error
            queue = (self.inputs if not self.control else self.control if not self.inputs
                     else self.control if self.control[0][0] < self.inputs[0][0] else self.inputs)
            return queue.popleft()[1]

    def close(self):
        with self.changed:
            self.closed = True
        self.wire.close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():raise RuntimeError("execution ingress did not close")


class ProjectionSender:
    """渡したobjectは以後変更禁止。JSON化とsocket送信はsenderだけが行う。"""
    def __init__(self, wire, capacity=32):
        self.wire = wire
        self.capacity = capacity
        self.control = deque()
        self.frame = None
        self.error = None
        self.closed = False
        self.changed = Condition()
        self.thread = Thread(target=self._run, name="workbench-projection", daemon=True)
        self.thread.start()

    def check(self):
        if self.error is not None:
            raise RuntimeError(f"projection sender failed: {self.error}") from self.error

    def put(self, value):
        with self.changed:
            self.check()
            if self.closed:
                raise RuntimeError("projection sender closed")
            if value["type"] == "frame" and not value.get("required"):
                self.frame = value
            else:
                if len(self.control) >= self.capacity:
                    raise RuntimeError("projection control FIFO overflow")
                # 制御遷移より前の表示を新状態の後へ送らない。
                self.frame = None
                self.control.append(value)
            self.changed.notify()

    def _run(self):
        try:
            while True:
                with self.changed:
                    self.changed.wait_for(lambda: self.closed or self.control or self.frame is not None)
                    if self.control:
                        value = self.control.popleft()
                    elif self.frame is not None:
                        value, self.frame = self.frame, None
                    elif self.closed:
                        return
                self.wire.send(json.dumps(value, allow_nan=False))
        except Exception as exc:
            with self.changed:
                self.error = exc
                self.changed.notify_all()

    def close(self, *, discard=False):
        with self.changed:
            self.closed = True
            if discard:
                self.control.clear()
                self.frame = None
            self.changed.notify_all()
        if discard:
            self.wire.close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            self.wire.close()
            self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("projection sender did not close")
        if not discard:
            self.check()
