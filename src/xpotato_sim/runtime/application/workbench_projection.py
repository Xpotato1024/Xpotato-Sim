"""Executionから切り離した有界表示slotと制御FIFOの唯一のsender。"""
from collections import deque
from threading import Condition, Thread
import json


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
            if value["type"] == "frame":
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

    def close(self):
        with self.changed:
            self.closed = True
            self.changed.notify_all()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            self.wire.close()
            self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("projection sender did not close")
        self.check()
