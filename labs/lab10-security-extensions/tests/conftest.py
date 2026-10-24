"""Fixtures: the mock IdP and the five agents on ephemeral ports, in fake mode."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import clear_fake_calls
from lab10_secure._testutil import Live, free_port

AGENTS = {
    "lab10-secure-agent": "secure_agent",
    "lab10-ext-agent": "ext_agent",
    "lab10-required-ext-agent": "required_ext_agent",
    "lab10-adk-secure": "adk_secure",
    "lab10-strands-secure": "strands_secure",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("compose"):
        return
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("DECISION_PROVIDER", "fake")
    monkeypatch.setenv("PUSH_ALLOWED_HOSTS", "127.0.0.1,localhost,webhook-receiver")
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)


@pytest.fixture
async def stack(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Live]]:
    """name -> running server. The IdP starts first: agents read its URL from the environment."""
    ports = {name: free_port() for name in ["lab10-mock-idp", *AGENTS]}
    for name, port in ports.items():
        key = name.upper().replace("-", "_")
        monkeypatch.setenv(f"{key}_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv(f"{key}_PORT", str(port))
    from lab10_secure.servers import mock_idp

    live: dict[str, Live] = {
        "lab10-mock-idp": await Live(mock_idp.build_app(), ports["lab10-mock-idp"]).start()
    }
    for name, module in AGENTS.items():
        app = __import__(f"lab10_secure.servers.{module}", fromlist=["build_app"]).build_app()
        live[name] = await Live(app, ports[name]).start()
    try:
        yield live
    finally:
        for lv in live.values():
            lv.stop()
