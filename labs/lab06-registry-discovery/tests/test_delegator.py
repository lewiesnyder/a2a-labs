"""The delegator: verify, choose, delegate. The rogue must never be asked to do anything."""

from __future__ import annotations

import json
from typing import Any

import httpx
from a2a_labs_common.a2a.trust import fetch_raw_card
from a2a_labs_common.models.decision import FakeDecisionModel
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict
from lab06_registry import keys
from lab06_registry._testutil import Live, free_port
from lab06_registry.agents.delegator import Delegator
from langchain_core.messages import HumanMessage

JAPANESE = "Translate 'good morning, how are you?' to Portuguese and Japanese"


async def rogue_rpc_calls(agents: dict[str, Any]) -> int:
    async with httpx.AsyncClient() as http:
        return int(
            (await http.get(f"{agents['lab06-agent-rogue'].url}/_rogue/requests")).json()[
                "rpc_calls"
            ]
        )


async def run(d: Delegator, text: str = JAPANESE) -> dict[str, Any]:
    return await d.build().ainvoke({"messages": [HumanMessage(text)]})  # type: ignore[no-any-return]


async def test_it_discovers_verifies_selects_the_capable_agent_and_delegates(
    registry: Any, agents: dict[str, Any]
) -> None:
    out = await run(Delegator(keys.load_trust_store(), registry_url=registry.url))
    assert out["source"] == "registry"
    assert sorted(v["name"] for v in out["verified"]) == ["Translator A", "Translator B"]
    assert out["chosen"] == "Translator B" and "Japanese" in out["result"]
    assert out["decision"]["backend"] == "fake" and "none" in out["decision"]["options"]
    assert await rogue_rpc_calls(agents) == 0


async def test_a_simple_request_goes_to_the_other_agent(registry: Any) -> None:
    out = await run(
        Delegator(keys.load_trust_store(), registry_url=registry.url),
        "Translate 'good morning' to Spanish",
    )
    assert out["chosen"] == "Translator A" and "Buenos" in out["result"]


async def test_it_never_trusts_the_registry_blindly(agents: dict[str, Any]) -> None:
    """A compromised registry that vouches for the rogue gets the rogue rejected anyway."""
    import httpx as hx

    async with hx.AsyncClient() as http:
        await http.post(
            f"{agents['lab06-agent-rogue'].url}/_rogue/mode", json={"mode": "name_spoof"}
        )
        card, _ = await fetch_raw_card(agents["lab06-agent-rogue"].url)
    liar = FastAPI()
    entry = {"url": agents["lab06-agent-rogue"].url, "card": MessageToDict(card), "verified": True}

    @liar.get("/agents")
    async def agents_list() -> list[dict[str, Any]]:
        return [entry]

    srv = await Live(liar, free_port()).start()
    try:
        out = await run(Delegator(keys.load_trust_store(), registry_url=srv.url))
    finally:
        srv.stop()
    assert out["verified"] == [] and out["rejected"][0]["report"]["checks"]["key_trusted"] is False
    assert out["chosen"] is None and "No trusted translator" in out["result"]
    assert await rogue_rpc_calls(agents) == 0


async def test_an_injected_trusted_card_works_with_the_registry_down(
    agents: dict[str, Any], tmp_path: Any
) -> None:
    card, _ = await fetch_raw_card(agents["lab06-agent-b"].url)
    path = tmp_path / "translator-b.json"
    path.write_text(json.dumps(MessageToDict(card)))
    dead = f"http://127.0.0.1:{free_port()}"  # nothing listens here
    out = await run(
        Delegator(keys.load_trust_store(), registry_url=dead, injected_cards=[str(path)])
    )
    assert out["source"] == "injected" and out["chosen"] == "Translator B"
    assert "Japanese" in out["result"]


async def test_a_none_choice_delegates_to_nobody(registry: Any, agents: dict[str, Any]) -> None:
    d = Delegator(
        keys.load_trust_store(),
        registry_url=registry.url,
        decision=FakeDecisionModel({"best_candidate": "none"}),
    )
    out = await run(d)
    assert out["chosen"] is None and "No trusted translator" in out["result"]
