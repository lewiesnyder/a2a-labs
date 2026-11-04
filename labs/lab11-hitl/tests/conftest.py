"""Fixtures: four delegates, the orchestrator and the inbox on ephemeral ports (fake models)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import clear_fake_calls
from lab11_hitl._testutil import Live, free_port
from lab11_hitl.inbox import create_app as inbox_app
from lab11_hitl.servers import docs, flights, hotels, orchestrator, payments

DELEGATES = {
    "lab11-flights": flights,
    "lab11-hotels": hotels,
    "lab11-docs": docs,
    "lab11-payments": payments,
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("compose"):
        return
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("DECISION_PROVIDER", "fake")
    monkeypatch.setenv("PUSH_ALLOWED_HOSTS", "127.0.0.1,localhost")
    monkeypatch.setenv("HITL_DEADLINE_S", "300")
    monkeypatch.setenv("HITL_REAPER_S", "0.2")
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)


@pytest.fixture
async def desk(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Live]]:
    """name -> running server (the orchestrator fetches delegate cards lazily)."""
    names = ["lab11-orchestrator", "lab11-inbox", *DELEGATES]
    ports = {n: free_port() for n in names}
    for n, port in ports.items():
        key = n.upper().replace("-", "_")
        monkeypatch.setenv(f"{key}_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv(f"{key}_PORT", str(port))
    live = {n: await Live(mod.build_app(), ports[n]).start() for n, mod in DELEGATES.items()}
    live["lab11-inbox"] = await Live(inbox_app(), ports["lab11-inbox"]).start()
    live["lab11-orchestrator"] = await Live(
        orchestrator.build_app(), ports["lab11-orchestrator"]
    ).start()
    try:
        yield live
    finally:
        for lv in live.values():
            lv.stop()
