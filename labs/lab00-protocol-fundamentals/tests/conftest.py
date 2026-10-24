"""Fixtures: run each playground variant in-process and talk to it with the real SDK client."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from a2a.types import AgentCard
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.a2a.client import Connected, connect
from a2a_labs_common.testing import asgi_client
from lab00_protocol.servers.playground import build_app


@pytest.fixture(autouse=True)
def _point_cards_at_testserver(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if request.node.get_closest_marker("compose"):
        return  # integration tests talk to the real compose services

    def bridge(card: AgentCard) -> None:
        for i in card.supported_interfaces:
            i.url = "http://testserver" + ("/rest" if i.protocol_binding == "HTTP+JSON" else "")

    monkeypatch.setattr(client_mod, "bridge_card_urls", bridge)
    monkeypatch.setenv("PLAYGROUND_ADMIN_TOKEN", "lab00-admin")


@pytest.fixture
def playground() -> Callable[..., object]:
    @asynccontextmanager
    async def make(variant: str = "full", **connect_kw: object) -> AsyncIterator[Connected]:
        app = build_app(variant, step_delay=0)
        http = asgi_client(app)
        c = await connect("http://testserver", httpx_client=http, **connect_kw)  # type: ignore[arg-type]
        try:
            yield c
        finally:
            await c.aclose()

    return make


def _live_url(binding: str, http_port: int, grpc_port: int) -> str:
    if binding == "GRPC":
        return f"127.0.0.1:{grpc_port}"
    return f"http://127.0.0.1:{http_port}" + ("/rest" if binding == "HTTP+JSON" else "")


# ── a real HTTP server, for tests that depend on streaming timing ──────────────────────────
@pytest.fixture
def live_playground(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """uvicorn on an ephemeral port in a thread. ASGITransport buffers SSE bodies, so cancel-
    during-stream and resubscribe need a real socket."""
    import socket
    import threading

    import uvicorn

    def free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    @asynccontextmanager
    async def make(variant: str = "full", step_delay: float = 0.05, **connect_kw: object):  # type: ignore[no-untyped-def]
        http_port, grpc_port = free_port(), free_port()
        monkeypatch.setenv("LAB00_PROTOCOL_AGENT_GRPC_PORT", str(grpc_port))
        monkeypatch.setattr(
            client_mod,
            "bridge_card_urls",
            lambda card: [
                setattr(i, "url", _live_url(i.protocol_binding, http_port, grpc_port))
                for i in card.supported_interfaces
            ],
        )
        server = uvicorn.Server(
            uvicorn.Config(
                build_app(variant, step_delay=step_delay),
                host="127.0.0.1",
                port=http_port,
                log_level="warning",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:  # noqa: ASYNC110 - uvicorn exposes no awaitable for this
            await asyncio.sleep(0.02)
        c = await connect(f"http://127.0.0.1:{http_port}", **connect_kw)  # type: ignore[arg-type]
        try:
            yield c
        finally:
            await c.aclose()
            server.should_exit = True
            thread.join(timeout=5)

    return make
