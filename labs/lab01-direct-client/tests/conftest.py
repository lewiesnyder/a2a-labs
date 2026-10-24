"""Fixtures: each Lab 01 server in-process, driven by the real SDK client, models faked."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest
from a2a.types import AgentCard
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.a2a.client import Connected, connect
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import asgi_client, clear_fake_calls

MODULES = {
    "sdk-echo": "sdk_echo",
    "adk": "adk_agent",
    "strands": "strands_agent",
    "langgraph": "langgraph_agent",
}


@pytest.fixture(autouse=True)
def _fake_models(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("compose"):
        return  # integration tests use whatever the running services were started with
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    model_settings.reset_cache()
    clear_fake_calls()

    def bridge(card: AgentCard) -> None:
        for i in card.supported_interfaces:
            i.url = "http://testserver"

    monkeypatch.setattr(client_mod, "bridge_card_urls", bridge)


@pytest.fixture
def server() -> Callable[..., object]:
    @asynccontextmanager
    async def make(name: str, **connect_kw: object) -> AsyncIterator[Connected]:
        mod = __import__(f"lab01_direct.servers.{MODULES[name]}", fromlist=["build_app"])
        app = mod.build_app()
        # ADK's to_a2a builds its card in the Starlette lifespan; ASGITransport does not run it.
        async with app.router.lifespan_context(app):
            c = await connect("http://testserver", httpx_client=asgi_client(app), **connect_kw)  # type: ignore[arg-type]
            try:
                yield c
            finally:
                await c.aclose()

    return make
