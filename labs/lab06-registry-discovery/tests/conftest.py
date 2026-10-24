"""Fixtures: fresh dev keys in a tmp dir, the three agents and the registry on ephemeral ports."""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import clear_fake_calls
from lab06_registry import keys

AGENTS = {
    "lab06-agent-a": "agent_a",
    "lab06-agent-b": "agent_b",
    "lab06-agent-rogue": "agent_rogue",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Live:
    def __init__(self, app: Any, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
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
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = keys.generate(
        tmp_path / "registry", hosts={"lab06-a": "127.0.0.1", "lab06-b": "127.0.0.1"}
    )
    monkeypatch.setenv("LAB06_REGISTRY_DIR", str(root))
    return root


@pytest.fixture
async def agents(
    registry_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, Live]]:
    ports = {svc: free_port() for svc in AGENTS}
    for svc, port in ports.items():  # every service URL (cards, rogue's fetch of A) is overridden
        key = svc.upper().replace("-", "_")
        monkeypatch.setenv(f"{key}_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv(f"{key}_PORT", str(port))
    live: dict[str, Live] = {}
    for svc, mod in AGENTS.items():
        app = __import__(f"lab06_registry.servers.{mod}", fromlist=["build_app"]).build_app()
        live[svc] = await Live(app, ports[svc]).start()
    try:
        yield live
    finally:
        for lv in live.values():
            lv.stop()


@pytest.fixture
async def registry(agents: dict[str, Live]) -> AsyncIterator[Live]:
    from lab06_registry.registry import Registry, create_app

    seed = [agents[s].url for s in ("lab06-agent-a", "lab06-agent-b", "lab06-agent-rogue")]
    app = create_app(Registry(keys.load_trust_store()), seed, reverify_s=3600)
    async with app.router.lifespan_context(app):
        lv = await Live(app, free_port()).start()
        for _ in range(100):  # wait for the seed to be registered
            if len(app.state.registry.agents if hasattr(app.state, "registry") else []) == 3:
                break
            await asyncio.sleep(0.05)
        try:
            yield lv
        finally:
            lv.stop()
