"""Fixtures: fake models, a scripted decision model, and servers on real ephemeral ports."""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import uvicorn
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.models.decision import FakeDecisionModel
from a2a_labs_common.testing import clear_fake_calls


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
    monkeypatch.setenv("TRIP_STEP_DELAY", "0.01")
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)


@pytest.fixture
def low_risk() -> FakeDecisionModel:
    return FakeDecisionModel({"risk": 0.0})


@pytest.fixture
def high_risk() -> FakeDecisionModel:
    return FakeDecisionModel({"risk": 2.0})


@pytest.fixture
async def serve() -> AsyncIterator[Callable[..., Any]]:
    started: list[Live] = []

    async def make(app: Any, port: int | None = None) -> Live:
        live = await Live(app, port or free_port()).start()
        started.append(live)
        return live

    yield make
    for lv in started:
        lv.stop()


@pytest.fixture
def pick_port() -> Callable[[], int]:
    return free_port
