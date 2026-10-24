"""Fixtures: three gateways and six squads on ephemeral ports (2 s TTL, fast announcing)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import clear_fake_calls
from lab09_agp._testutil import Live, free_port
from lab09_agp.clients import table
from lab09_agp.servers import (
    gateway,
    squad_compliance,
    squad_flights,
    squad_fx,
    squad_hotels,
    squad_payments,
    squad_visa,
)

GATEWAYS = ["lab09-root-gateway", "lab09-finance-gateway", "lab09-travel-gateway"]
SQUADS = {
    "lab09-squad-payments": squad_payments,
    "lab09-squad-fx": squad_fx,
    "lab09-squad-compliance": squad_compliance,
    "lab09-squad-flights": squad_flights,
    "lab09-squad-hotels": squad_hotels,
    "lab09-squad-visa": squad_visa,
}
TTL, EVERY = 2.0, 0.2


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("compose"):
        return
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("DECISION_PROVIDER", "fake")
    monkeypatch.setenv("AGP_TTL_S", str(TTL))
    monkeypatch.setenv("AGP_REANNOUNCE_S", str(EVERY))
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)


class Mesh:
    def __init__(self, live: dict[str, Live], ports: dict[str, int]) -> None:
        self.live, self.ports = live, ports

    def url(self, name: str) -> str:
        return self.live[name].url

    async def stop(self, name: str) -> None:
        self.live[name].stop()

    async def start_squad(self, name: str) -> None:
        self.live[name] = await Live(SQUADS[name].build_app(), self.ports[name]).start()

    async def converged(self, routes: int = 7, within: float = 10) -> None:
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            caps = table("lab09-root-gateway")["capabilities"]
            if sum(len(v) for v in caps.values()) >= routes and all(
                not r["stale"] for v in caps.values() for r in v
            ):
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"root table did not reach {routes} fresh routes")


@pytest.fixture
async def mesh(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Mesh]:
    names = [*GATEWAYS, *SQUADS]
    ports = {n: free_port() for n in names}
    for n, port in ports.items():
        key = n.upper().replace("-", "_")
        monkeypatch.setenv(f"{key}_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv(f"{key}_PORT", str(port))
    live: dict[str, Live] = {}
    for g in GATEWAYS:
        live[g] = await Live(gateway.build_app(g), ports[g]).start()
    for s, mod in SQUADS.items():
        live[s] = await Live(mod.build_app(), ports[s]).start()
    m = Mesh(live, ports)
    await m.converged()
    try:
        yield m
    finally:
        for lv in live.values():
            lv.stop()
