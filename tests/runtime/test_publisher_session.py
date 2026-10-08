"""publisherのready順序、未接続、例外/取消時のserver回収を検証する。"""
from __future__ import annotations

import asyncio

import pytest

from xpotato_sim.runtime.application.publisher_session import connected_publisher_session


class Server:
    host = "127.0.0.1"
    bound_port = 8766

    def __init__(self, events, *, connected=True, failure=None):
        self.events = events
        self.connected = connected
        self.failure = failure

    async def __aenter__(self):
        self.events.append("bind")
        return self

    async def __aexit__(self, kind, exc, tb):
        self.events.append(("closed", kind))

    async def wait_for_client(self, *, timeout_s):
        self.events.append(("wait", timeout_s))
        if self.failure:
            raise self.failure
        return self.connected


@pytest.mark.parametrize("connected", [True, False])
def test_ready_after_bind_and_before_wait_without_running_unconnected(connected):
    events = []
    server = Server(events, connected=connected)

    async def run():
        async with connected_publisher_session(
            server, grace_period_s=0.25, on_ready=lambda: events.append("ready"),
        ) as active:
            if active is not None:
                assert active is server
                events.append("execute")

    asyncio.run(run())
    assert events == ["bind", "ready", ("wait", 0.25),
                      *(["execute"] if connected else []), ("closed", None)]


@pytest.mark.parametrize("stage", ["ready", "wait", "execute"])
@pytest.mark.parametrize("kind", [RuntimeError, asyncio.CancelledError])
def test_every_failure_keeps_original_exception_and_closes_server(stage, kind):
    events = []
    failure = kind(stage)
    server = Server(events, failure=failure if stage == "wait" else None)

    def ready():
        if stage == "ready":
            raise failure

    async def run():
        async with connected_publisher_session(server, grace_period_s=0, on_ready=ready):
            if stage == "execute":
                raise failure

    with pytest.raises(kind) as caught:
        asyncio.run(run())
    assert caught.value is failure
    assert events[-1] == ("closed", kind)
