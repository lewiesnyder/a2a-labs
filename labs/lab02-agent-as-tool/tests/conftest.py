"""Fixtures: the three specialists on real ephemeral ports, controllers in-process, fake models.

Controllers call specialists over real HTTP (a controller needs a live remote agent), so the
specialists run under uvicorn in threads and every service URL is overridden through the
``<SERVICE>_URL`` environment variables that ``a2a_labs_common.config`` honours.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import uvicorn
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.a2a.client import Connected, connect
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import asgi_client, clear_fake_calls

SPECIALISTS = {
    "lab02-langgraph-currency": "langgraph_currency",
    "lab02-strands-weather": "strands_weather",
    "lab02-adk-facts": "adk_facts",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("compose"):
        return
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)  # cards are accurate


@pytest.fixture
async def specialists(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, int]]:
    """Start currency, weather and facts; yield {service: port}."""
    ports = {svc: free_port() for svc in SPECIALISTS}
    for svc, port in ports.items():
        key = svc.upper().replace("-", "_")
        monkeypatch.setenv(f"{key}_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv(f"{key}_PORT", str(port))
    servers: list[tuple[uvicorn.Server, threading.Thread]] = []
    for svc, mod in SPECIALISTS.items():
        app = __import__(f"lab02_tool.servers.{mod}", fromlist=["build_app"]).build_app()
        srv = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=ports[svc], log_level="warning")
        )
        t = threading.Thread(target=srv.run, daemon=True)
        t.start()
        servers.append((srv, t))
    for srv, _ in servers:
        while not srv.started:  # noqa: ASYNC110 - uvicorn exposes no awaitable for this
            await asyncio.sleep(0.02)
    try:
        yield ports
    finally:
        for srv, t in servers:
            srv.should_exit = True
            t.join(timeout=5)


@pytest.fixture
def controller(specialists: dict[str, int]) -> Any:
    """``async with controller("langchain") as client``: a controller served in-process."""

    @asynccontextmanager
    async def make(kind: str) -> AsyncIterator[Connected]:
        mod = __import__(f"lab02_tool.servers.{kind}_controller", fromlist=["build_app"])
        app = mod.build_app()
        async with app.router.lifespan_context(app):
            c = await connect("http://testserver", httpx_client=asgi_client(app), streaming=False)
            try:
                yield c
            finally:
                await c.aclose()

    return make


@pytest.fixture
async def specialist_client(specialists: dict[str, int]) -> Any:
    """Direct client to a specialist (to inspect what the controller did to it)."""

    @asynccontextmanager
    async def make(service: str) -> AsyncIterator[Connected]:
        c = await connect(service, streaming=False)
        try:
            yield c
        finally:
            await c.aclose()

    return make
