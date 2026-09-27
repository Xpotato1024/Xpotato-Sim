from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from websockets.asyncio.server import serve


class _WebSocketClient(Protocol):
    async def send(self, message: str) -> None:
        ...

    async def close(self, code: int = 1000, reason: str = "") -> None:
        ...

    async def wait_closed(self) -> None:
        ...


class WebSocketPublisherServer:
    """Local/dev WebSocket server that relays JSON strings to connected clients."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8766,
        on_message: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        if not host:
            raise ValueError("host must not be empty")
        if port < 1 or port > 65535:
            raise ValueError("port must be in the range 1..65535")

        self._host = host
        self._port = port
        self._on_message = on_message
        self._clients: set[_WebSocketClient] = set()
        self._client_connected = asyncio.Event()
        self._handler_errors: list[Exception] = []
        self._server_cm: Any | None = None
        self._server: Any | None = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def message_handler_errors(self) -> tuple[Exception, ...]:
        return tuple(self._handler_errors)

    @property
    def bound_port(self) -> int:
        if self._server is None or not getattr(self._server, "sockets", None):
            return self._port

        socket = self._server.sockets[0]
        address = socket.getsockname()
        if isinstance(address, tuple) and len(address) >= 2:
            return int(address[1])
        return self._port

    async def __aenter__(self) -> "WebSocketPublisherServer":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._server_cm is not None:
            raise RuntimeError("WebSocketPublisherServer is already running")

        self._server_cm = serve(self._handle_client, self._host, self._port)
        self._server = await self._server_cm.__aenter__()

    async def stop(self) -> None:
        if self._server_cm is None:
            return

        server_cm = self._server_cm
        self._server_cm = None
        self._server = None

        clients = tuple(self._clients)
        self._clients.clear()
        await asyncio.gather(
            *(client.close(code=1001, reason="server stopping") for client in clients),
            return_exceptions=True,
        )
        await server_cm.__aexit__(None, None, None)

    async def wait_for_client(self, timeout_s: float | None = None) -> bool:
        if self._clients:
            return True

        try:
            if timeout_s is None:
                await self._client_connected.wait()
            else:
                await asyncio.wait_for(self._client_connected.wait(), timeout=timeout_s)
        except TimeoutError:
            return bool(self._clients)

        return bool(self._clients)

    async def send(self, message: str) -> None:
        if not self._clients:
            return

        clients = tuple(self._clients)
        results = await asyncio.gather(
            *(client.send(message) for client in clients),
            return_exceptions=True,
        )

        for client, result in zip(clients, results, strict=False):
            if isinstance(result, Exception):
                self._clients.discard(client)

    async def _handle_client(self, websocket: _WebSocketClient) -> None:
        self._clients.add(websocket)
        self._client_connected.set()
        try:
            if self._on_message is None:
                await websocket.wait_closed()
                return

            async for message in websocket:
                if not isinstance(message, str):
                    continue

                try:
                    result = self._on_message(message)
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:  # keep the server alive on handler errors
                    self._handler_errors.append(exc)
        finally:
            self._clients.discard(websocket)
            if not self._clients:
                self._client_connected.clear()


__all__ = ["WebSocketPublisherServer"]
