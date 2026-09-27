from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from time import monotonic

from xpotato_sim.runtime.execution.live_timing import MonotonicClock
from xpotato_sim.schemas import MuJoCoState
from xpotato_sim.transport.websocket import WebSocketSender, serialize_mujoco_state_message

DEFAULT_LIVE_PUBLISHER_DRAIN_TIMEOUT_S = 1.0


@dataclass(frozen=True, slots=True)
class LiveWebSocketDeliverySummary:
    enqueued_frame_count: int
    sent_frame_count: int
    coalesced_frame_count: int
    sender_error_count: int
    shutdown_timeout_count: int
    shutdown_dropped_frame_count: int
    serialization_time_s: float
    enqueue_time_s: float
    send_wait_time_s: float
    latest_enqueued_frame_index: int | None
    latest_sent_frame_index: int | None

    def to_dict(self) -> dict[str, int | float | None]:
        return asdict(self)


class LiveLatestStateWebSocketPublisher:
    """Live-only bounded latest-state delivery; canonical publishers stay lossless."""

    def __init__(self, sender: WebSocketSender, *, clock: MonotonicClock = monotonic) -> None:
        self._sender = sender
        self._clock = clock
        self._pending: tuple[int, str] | None = None
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._shutdown_flush_succeeded: bool | None = None
        self._sender_error: BaseException | None = None
        self._sender_error_reported = False
        self._in_flight_frame_index: int | None = None
        self._enqueued_frame_count = 0
        self._sent_frame_count = 0
        self._coalesced_frame_count = 0
        self._sender_error_count = 0
        self._shutdown_timeout_count = 0
        self._shutdown_dropped_frame_count = 0
        self._serialization_time_s = 0.0
        self._enqueue_time_s = 0.0
        self._send_wait_time_s = 0.0
        self._latest_enqueued_frame_index: int | None = None
        self._latest_sent_frame_index: int | None = None

    async def __aenter__(self) -> "LiveLatestStateWebSocketPublisher":
        self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("live latest-state publisher is closed")
        if self._task is None:
            self._task = asyncio.create_task(self._sender_loop())

    async def publish(self, state: MuJoCoState) -> None:
        if self._closed:
            raise RuntimeError("live latest-state publisher is closed")
        if self._sender_error is not None:
            self._sender_error_reported = True
            raise RuntimeError("live latest-state sender failed") from self._sender_error
        if self._task is None:
            self.start()

        serialize_started_s = self._clock()
        message = serialize_mujoco_state_message(state)
        serialized_at_s = self._clock()
        if self._pending is not None:
            self._coalesced_frame_count += 1
        self._pending = (state.frame_index, message)
        self._enqueued_frame_count += 1
        self._latest_enqueued_frame_index = state.frame_index
        self._serialization_time_s += max(0.0, serialized_at_s - serialize_started_s)
        self._enqueue_time_s += max(0.0, self._clock() - serialized_at_s)
        self._idle.clear()
        self._wake.set()

    async def drain(self, *, timeout_s: float | None = DEFAULT_LIVE_PUBLISHER_DRAIN_TIMEOUT_S) -> bool:
        if self._sender_error is not None:
            self._sender_error_reported = True
            raise RuntimeError("live latest-state sender failed") from self._sender_error
        if self._closed:
            return self._shutdown_flush_succeeded is True
        try:
            if timeout_s is None:
                await self._idle.wait()
            else:
                if timeout_s < 0.0:
                    raise ValueError("timeout_s must be non-negative or None")
                await asyncio.wait_for(self._idle.wait(), timeout=timeout_s)
        except TimeoutError:
            self._shutdown_timeout_count += 1
            self._shutdown_flush_succeeded = False
            await self._terminate_sender(record_unconfirmed_drop=True)
            return False
        if self._sender_error is not None:
            self._sender_error_reported = True
            raise RuntimeError("live latest-state sender failed") from self._sender_error
        self._shutdown_flush_succeeded = True
        return True

    async def close(
        self,
        *,
        flush_timeout_s: float | None = DEFAULT_LIVE_PUBLISHER_DRAIN_TIMEOUT_S,
    ) -> bool:
        if self._closed:
            return self._shutdown_flush_succeeded is True
        if self._sender_error is not None and self._sender_error_reported:
            await self._terminate_sender(record_unconfirmed_drop=False)
            return False
        try:
            flushed = await self.drain(timeout_s=flush_timeout_s)
        except BaseException:
            await self._terminate_sender(record_unconfirmed_drop=False)
            raise
        if not self._closed:
            await self._terminate_sender(record_unconfirmed_drop=False)
        return flushed

    async def _terminate_sender(self, *, record_unconfirmed_drop: bool) -> None:
        if record_unconfirmed_drop:
            self._shutdown_dropped_frame_count += int(self._in_flight_frame_index is not None)
            self._shutdown_dropped_frame_count += int(self._pending is not None)
        self._closed = True
        self._pending = None
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._in_flight_frame_index = None
        self._idle.set()

    @property
    def sender_error(self) -> BaseException | None:
        return self._sender_error

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    def summary(self) -> LiveWebSocketDeliverySummary:
        return LiveWebSocketDeliverySummary(
            enqueued_frame_count=self._enqueued_frame_count,
            sent_frame_count=self._sent_frame_count,
            coalesced_frame_count=self._coalesced_frame_count,
            sender_error_count=self._sender_error_count,
            shutdown_timeout_count=self._shutdown_timeout_count,
            shutdown_dropped_frame_count=self._shutdown_dropped_frame_count,
            serialization_time_s=self._serialization_time_s,
            enqueue_time_s=self._enqueue_time_s,
            send_wait_time_s=self._send_wait_time_s,
            latest_enqueued_frame_index=self._latest_enqueued_frame_index,
            latest_sent_frame_index=self._latest_sent_frame_index,
        )

    async def _sender_loop(self) -> None:
        try:
            while not self._closed:
                await self._wake.wait()
                if self._closed:
                    return
                candidate = self._pending
                self._pending = None
                self._wake.clear()
                if candidate is None:
                    self._idle.set()
                    continue

                frame_index, message = candidate
                self._in_flight_frame_index = frame_index
                send_started_s = self._clock()
                try:
                    await self._sender.send(message)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:
                    self._sender_error = exc
                    self._sender_error_count += 1
                    self._in_flight_frame_index = None
                    self._idle.set()
                    return
                self._send_wait_time_s += max(0.0, self._clock() - send_started_s)
                self._sent_frame_count += 1
                self._latest_sent_frame_index = frame_index
                self._in_flight_frame_index = None
                if self._pending is None:
                    self._idle.set()
                else:
                    self._wake.set()
        finally:
            self._idle.set()


__all__ = [
    "DEFAULT_LIVE_PUBLISHER_DRAIN_TIMEOUT_S",
    "LiveLatestStateWebSocketPublisher",
    "LiveWebSocketDeliverySummary",
]
