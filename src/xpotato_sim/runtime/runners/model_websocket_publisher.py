"""モデル選択に基づく名前付き手先runtimeを既存Viewer WebSocketへ投影する。"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic

from xpotato_sim.runtime.execution.live_timing import AbsoluteDeadlinePacer
from xpotato_sim.runtime.runners.live_websocket_delivery import LiveLatestStateWebSocketPublisher
from xpotato_sim.runtime.composition.launch_profile import LaunchProfile
from xpotato_sim.runtime.execution.model_execution import ModelExecution
from xpotato_sim.transport import WebSocketPublisherServer


async def _run_model_websocket_publisher_async(
    profile: LaunchProfile,
    *,
    clock: Callable[[], float],
    on_ready: Callable[[], None] | None,
) -> None:
    execution = ModelExecution(profile, clock=clock)

    def on_message(message: str) -> None:
        execution.ingest(message)

    try:
        async with WebSocketPublisherServer(
            host=profile.host, port=profile.backend_port, on_message=on_message,
        ) as server:
            print(f"serving on ws://{server.host}:{server.bound_port}", flush=True)
            if on_ready is not None:
                on_ready()
            if not await server.wait_for_client(timeout_s=profile.grace_period_s):
                print("No viewer connected during grace period; no payloads published.", flush=True)
                return

            pacer = AbsoluteDeadlinePacer(profile.interval_s)
            pacer.start()
            async with LiveLatestStateWebSocketPublisher(server) as publisher:
                for frame_index in range(profile.steps):
                    execution.tick()
                    state = execution.sample(frame_index, advance_task=True,
                        budget_exhausted=frame_index + 1 == profile.steps)
                    await publisher.publish(state)
                    # fault後も姿勢と理由を表示する。入力復帰による自動再開はしない。
                    if frame_index + 1 < profile.steps:
                        await asyncio.sleep(0)
                        await pacer.pace()
                await publisher.drain()
    finally:
        execution.stop()


def run_model_websocket_publisher(
    profile: LaunchProfile,
    *,
    clock: Callable[[], float] = monotonic,
    on_ready: Callable[[], None] | None = None,
) -> None:
    if type(profile) is not LaunchProfile or profile.model is None:
        raise TypeError("LaunchProfile is required")
    if not callable(clock):
        raise TypeError("monotonic clock is required")
    if on_ready is not None and not callable(on_ready):
        raise TypeError("on_ready must be callable")
    asyncio.run(_run_model_websocket_publisher_async(
        profile, clock=clock, on_ready=on_ready,
    ))


__all__ = ["run_model_websocket_publisher"]
