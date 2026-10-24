"""Fixtures: the three hops on real ephemeral ports (service URLs overridden), fake models."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import clear_fake_calls
from lab07_chain._testutil import Live, free_port

HOPS = {
    "lab07-adk-planner": "adk_planner",
    "lab07-strands-writer": "strands_writer",
    "lab07-langgraph-editor": "langgraph_editor",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("compose"):
        return
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("DECISION_PROVIDER", "fake")
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)


class Hops:
    def __init__(self) -> None:
        self.live: dict[str, Live] = {}

    def stop(self, service: str) -> None:
        self.live[service].stop()


@pytest.fixture
async def hops(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Hops]:
    h = Hops()
    ports = {svc: free_port() for svc in HOPS}
    for svc, port in ports.items():
        key = svc.upper().replace("-", "_")
        monkeypatch.setenv(f"{key}_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv(f"{key}_PORT", str(port))
    for svc, mod in HOPS.items():
        app = __import__(f"lab07_chain.servers.{mod}", fromlist=["build_app"]).build_app()
        h.live[svc] = await Live(app, ports[svc]).start()
    try:
        yield h
    finally:
        for lv in h.live.values():
            lv.stop()
