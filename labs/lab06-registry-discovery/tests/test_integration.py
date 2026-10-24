"""Integration tests against the running ``lab06`` profile (``make e2e LAB=lab06``)."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pytest
from a2a.types import TaskState
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.config import host_url
from lab06_registry import keys
from lab06_registry.agents.delegator import Delegator
from langchain_core.messages import HumanMessage

pytestmark = pytest.mark.compose
FAKE = os.environ.get("DECISION_PROVIDER") == "fake"
REGISTRY, ROGUE = host_url("lab06-registry"), host_url("lab06-agent-rogue")
JAPANESE = "Translate 'good morning, how are you?' to Portuguese and Japanese"


@pytest.fixture(scope="module", autouse=True)
async def _services_up() -> None:
    await wait_for_cards(
        ["lab06-agent-a", "lab06-agent-b", "lab06-agent-rogue", "lab06-delegator"], timeout=180
    )


async def get(url: str, **params: Any) -> Any:
    async with httpx.AsyncClient(timeout=15) as http:
        return (await http.get(url, params=params)).json()


async def test_the_registry_lists_only_the_verified_translators() -> None:
    rows = await get(f"{REGISTRY}/agents", skill="translate")
    assert sorted(r["card"]["name"] for r in rows) == ["Translator A", "Translator B"]
    assert all(r["verified"] for r in rows)


@pytest.mark.parametrize("mode", ["unsigned", "untrusted_key", "tampered", "name_spoof"])
async def test_the_rogue_is_quarantined_in_every_mode(mode: str) -> None:
    async with httpx.AsyncClient(timeout=15) as http:
        await http.post(f"{ROGUE}/_rogue/mode", json={"mode": mode})
        entry = (await http.post(f"{REGISTRY}/agents", json={"url": "lab06-agent-rogue"})).json()
        hidden = await get(f"{REGISTRY}/agents", skill="translate")
    assert entry["verified"] is False and entry["verification_report"]["reasons"]
    assert "Translator A" not in [r["card"]["name"] for r in hidden if not r["verified"]]
    assert len(hidden) == 2


@pytest.mark.skipif(not FAKE, reason="the exact choice needs the scripted decision model")
async def test_the_delegator_picks_the_capable_agent_and_never_calls_the_rogue() -> None:
    before = (await get(f"{ROGUE}/_rogue/requests"))["rpc_calls"]
    async with await connect("lab06-delegator", streaming=False) as c:
        res = await send_text(c, JAPANESE, context_id=str(uuid.uuid4()))
    assert res.state == TaskState.TASK_STATE_COMPLETED and "Japanese" in res.text
    assert (await get(f"{ROGUE}/_rogue/requests"))["rpc_calls"] == before


async def test_the_injected_card_path_works_with_the_registry_down() -> None:
    path = keys.registry_dir() / "injected" / "translator-b.json"
    d = Delegator(
        keys.load_trust_store(), registry_url="http://127.0.0.1:9", injected_cards=[str(path)]
    )
    out = await d.build().ainvoke({"messages": [HumanMessage(JAPANESE)]})
    assert out["source"] == "injected" and out["chosen"] == "Translator B" and out["result"]
