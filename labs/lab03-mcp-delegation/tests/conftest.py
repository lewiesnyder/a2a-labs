"""Fixtures: every Lab 03 server on a real ephemeral port (MCP and A2A are both HTTP)."""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
import uvicorn
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import clear_fake_calls


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Live:
    """A uvicorn server in a thread, stoppable."""

    def __init__(self, app: Any, port: int) -> None:
        self.port = port
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    async def start(self) -> Live:
        self.thread.start()
        while not self.server.started:  # noqa: ASYNC110 - uvicorn exposes no awaitable for this
            await asyncio.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.force_exit = True  # open MCP streams would otherwise delay shutdown
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("compose"):
        return
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)  # cards are accurate


def point(monkeypatch: pytest.MonkeyPatch, service: str, port: int, path: str = "") -> str:
    """Override a service's URL and port so every process in the test finds it."""
    key = service.upper().replace("-", "_")
    url = f"http://127.0.0.1:{port}{path}"
    monkeypatch.setenv(f"{key}_URL", url)
    monkeypatch.setenv(f"{key}_PORT", str(port))
    return url


@pytest.fixture
async def stack(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """tools-mcp + both specialists + the bridge, all live. Yields {service: url}."""
    from lab03_mcp.servers import adk_specialist, mcp_a2a_bridge, strands_specialist, tools_mcp

    lives: list[Live] = []
    urls: dict[str, str] = {}

    async def up(service: str, app: Any, path: str = "") -> None:
        port = free_port()
        urls[service] = point(monkeypatch, service, port, path)
        lives.append(await Live(app, port).start())

    # Ports/URLs must be known before apps are built (cards and clients read them).
    for svc, path in [
        ("lab03-tools-mcp", "/mcp"),
        ("lab03-strands-specialist", ""),
        ("lab03-adk-specialist", ""),
        ("lab03-mcp-a2a-bridge", "/mcp"),
    ]:
        point(monkeypatch, svc, free_port(), path)
    urls = {
        s: __import__("os").environ[f"{s.upper().replace('-', '_')}_URL"]
        for s in (
            "lab03-tools-mcp",
            "lab03-strands-specialist",
            "lab03-adk-specialist",
            "lab03-mcp-a2a-bridge",
        )
    }

    def port_of(service: str) -> int:
        return int(urls[service].split(":")[-1].split("/")[0])

    try:
        for svc, app in [
            ("lab03-tools-mcp", tools_mcp.build_server().streamable_http_app()),
            ("lab03-strands-specialist", strands_specialist.build_app()),
            ("lab03-adk-specialist", adk_specialist.build_app()),
        ]:
            lives.append(await Live(app, port_of(svc)).start())
        bridge = mcp_a2a_bridge.build_server()
        lives.append(
            await Live(bridge.streamable_http_app(), port_of("lab03-mcp-a2a-bridge")).start()
        )
        yield urls
    finally:
        for lv in lives:
            lv.stop()


@asynccontextmanager
async def mcp_session(url: str, logging_callback: Any = None) -> AsyncIterator[Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with (
        streamablehttp_client(url) as (read, write, _),
        ClientSession(read, write, logging_callback=logging_callback) as session,
    ):
        await session.initialize()
        yield session


@pytest.fixture
def session() -> Callable[[str], Any]:
    """``async with session(url) as s``: a real MCP client session over streamable HTTP."""
    return mcp_session


@pytest.fixture
def pick_port() -> Callable[[], int]:
    return free_port


@pytest.fixture
async def serve() -> AsyncIterator[Callable[[Any], Any]]:
    """``live = await serve(asgi_app)``: run any ASGI app on a free port; stopped at teardown."""
    started: list[Live] = []

    async def make(app: Any, port: int | None = None) -> Live:
        live = await Live(app, port or free_port()).start()
        started.append(live)
        return live

    yield make
    for lv in started:
        lv.stop()
