"""Fixtures: the three specialists on real ephemeral ports (lab05 names), supervisors in-process.

The supervisors call specialists over real HTTP, so the specialists run under uvicorn in threads
and every service URL is overridden through the ``<SERVICE>_URL`` variables that
``a2a_labs_common.config`` honours. The same Lab 02 server modules run here as in compose, with
``OTEL_SERVICE_NAME`` selecting the lab05 identity.
"""

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
from a2a_labs_common.a2a.client import Connected, connect
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import asgi_client, clear_fake_calls

SPECIALISTS = {
    "lab05-currency": "langgraph_currency",
    "lab05-weather": "strands_weather",
    "lab05-facts": "adk_facts",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Live:
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
        self.server.force_exit = True
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("compose"):
        return
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("DECISION_PROVIDER", "fake")
    monkeypatch.setenv("DISCOVERY_TTL", "0")  # tests change the specialists: always re-discover
    monkeypatch.delenv("ROUTER_MODE", raising=False)
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)


class Specialists:
    """The running specialists; ``stop(service)`` simulates an outage."""

    def __init__(self) -> None:
        self.live: dict[str, Live] = {}
        self.ports: dict[str, int] = {}


@pytest.fixture
async def specialists(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Specialists]:
    sp = Specialists()
    for svc, mod in SPECIALISTS.items():
        port = free_port()
        key = svc.upper().replace("-", "_")
        monkeypatch.setenv(f"{key}_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv(f"{key}_PORT", str(port))
        monkeypatch.setenv("OTEL_SERVICE_NAME", svc)  # the lab02 module reads its identity here
        app = __import__(f"lab02_tool.servers.{mod}", fromlist=["build_app"]).build_app()
        sp.live[svc] = await Live(app, port).start()
        sp.ports[svc] = port
    monkeypatch.delenv("OTEL_SERVICE_NAME")
    try:
        yield sp
    finally:
        for lv in sp.live.values():
            lv.stop()


@pytest.fixture
def serve_supervisor(specialists: Specialists) -> Any:
    """``async with serve_supervisor("langgraph") as client``: a supervisor served in-process."""

    @asynccontextmanager
    async def make(kind: str = "langgraph", **kw: Any) -> AsyncIterator[Connected]:
        mod = {
            "langgraph": "langgraph_supervisor",
            "adk": "adk_coordinator",
            "strands": "strands_graph",
        }[kind]
        app = __import__(f"lab05_supervisor.servers.{mod}", fromlist=["build_app"]).build_app(**kw)
        async with app.router.lifespan_context(app):
            c = await connect("http://testserver", httpx_client=asgi_client(app), streaming=False)
            c.app = app  # type: ignore[attr-defined]
            try:
                yield c
            finally:
                await c.aclose()

    return make


@pytest.fixture
def pick_port() -> Callable[[], int]:
    return free_port
