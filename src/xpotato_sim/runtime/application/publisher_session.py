"""有限publisherの接続待機とserver寿命を共通化する。実行/Taskは呼出側が所有する。"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from xpotato_sim.transport import WebSocketPublisherServer


@asynccontextmanager
async def connected_publisher_session(
    server: WebSocketPublisherServer,
    *,
    grace_period_s: float,
    on_ready: Callable[[], None] | None = None,
    verbose: bool = True,
) -> AsyncIterator[WebSocketPublisherServer | None]:
    """bind成功後にreadyを通知し、未接続なら実行せず、全終了経路でserverを閉じる。"""
    async with server:
        print(f"serving on ws://{server.host}:{server.bound_port}", flush=True)
        if on_ready is not None:
            on_ready()
        if verbose:
            print(f"Waiting for viewer during grace period ({grace_period_s:.2f}s)", flush=True)
        connected = await server.wait_for_client(timeout_s=grace_period_s)
        if not connected:
            print("No viewer connected during grace period; no payloads published.", flush=True)
            if verbose:
                print("Completed without publishing because no viewer connected.", flush=True)
        elif verbose:
            print("Viewer connected; publishing started.", flush=True)
        yield server if connected else None
