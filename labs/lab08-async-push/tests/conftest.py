"""Fixtures: the four agents on ephemeral ports with ~0 s durations, and an embedded receiver."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a2a_labs_common.a2a import client as client_mod
from a2a_labs_common.models import settings as model_settings
from a2a_labs_common.testing import clear_fake_calls
from lab08_push._testutil import Live, free_port
from lab08_push.receivers import EmbeddedReceiver

AGENTS = {
    "lab08-sdk-report-agent": "sdk_report_agent",
    "lab08-strands-batch-agent": "strands_batch_agent",
    "lab08-adk-research-agent": "adk_research_agent",
    "lab08-langgraph-pipeline-agent": "langgraph_pipeline_agent",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, tmp_path: Path) -> None:
    if request.node.get_closest_marker("compose"):
        return
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("DECISION_PROVIDER", "fake")
    for name in ("PHASE_SECONDS", "DOC_SECONDS", "TOOL_SECONDS", "NODE_SECONDS"):
        monkeypatch.setenv(name, "0.05")
    monkeypatch.setenv("LAB08_DB_DIR", str(tmp_path / "db"))
    # the compose receiver name is an allowed host; tests use loopback receivers on the allowlist
    monkeypatch.setenv("PUSH_ALLOWED_HOSTS", "127.0.0.1,localhost,webhook-receiver")
    model_settings.reset_cache()
    clear_fake_calls()
    monkeypatch.setattr(client_mod, "bridge_card_urls", lambda card: None)


@pytest.fixture
async def agents(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Live]]:
    ports = {svc: free_port() for svc in AGENTS}
    for svc, port in ports.items():
        key = svc.upper().replace("-", "_")
        monkeypatch.setenv(f"{key}_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv(f"{key}_PORT", str(port))
    live: dict[str, Live] = {}
    for svc, mod in AGENTS.items():
        kw = {"durable": True} if svc == "lab08-sdk-report-agent" else {}
        app = __import__(f"lab08_push.servers.{mod}", fromlist=["build_app"]).build_app(
            backoff_s=0.01, **kw
        )
        live[svc] = await Live(app, ports[svc]).start()
    try:
        yield live
    finally:
        for lv in live.values():
            lv.stop()


@pytest.fixture
async def receiver() -> AsyncIterator[EmbeddedReceiver]:
    r = EmbeddedReceiver(agent_host="127.0.0.1")
    await r.start()
    try:
        yield r
    finally:
        r.stop()
