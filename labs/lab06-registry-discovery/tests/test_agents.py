"""The honest agents: signed cards, a JWKS nobody should rely on, a guarded extended card."""

from __future__ import annotations

from typing import Any

import httpx
from a2a.client import A2ACardResolver
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.a2a.trust import CardVerifier, fetch_raw_card
from lab06_registry import keys


async def test_both_honest_cards_verify_against_the_trust_store(agents: dict[str, Any]) -> None:
    verifier = CardVerifier(keys.load_trust_store())
    for svc, name in (("lab06-agent-a", "Translator A"), ("lab06-agent-b", "Translator B")):
        card, base = await fetch_raw_card(agents[svc].url)
        report = verifier.verify(card, base)
        assert card.name == name and report.ok, report.reasons
        assert report.kid == {"Translator A": "lab06-a", "Translator B": "lab06-b"}[name]


async def test_each_agent_serves_a_jwks_with_its_public_key(agents: dict[str, Any]) -> None:
    async with httpx.AsyncClient() as http:
        jwks = (await http.get(f"{agents['lab06-agent-a'].url}/.well-known/jwks.json")).json()
    assert jwks["keys"][0]["kid"] == "lab06-a" and "d" not in jwks["keys"][0]  # public only


async def test_translators_answer_over_a2a(agents: dict[str, Any]) -> None:
    async with await connect(agents["lab06-agent-a"].url, streaming=False) as c:
        res = await send_text(c, "Translate 'good morning' to Spanish")
    assert "Buenos" in res.text


async def test_the_extended_card_needs_the_bearer_token(agents: dict[str, Any]) -> None:
    from a2a.types import GetExtendedAgentCardRequest

    base = agents["lab06-agent-b"].url
    async with httpx.AsyncClient() as http:
        public = await A2ACardResolver(http, base).get_agent_card()
    assert public.capabilities.extended_agent_card
    assert [s.id for s in public.skills] == ["translate"]
    async with httpx.AsyncClient(headers={"Authorization": "Bearer lab06-b-token"}) as http:
        card = await A2ACardResolver(http, base).get_agent_card()
        async with await connect(base, headers={"Authorization": "Bearer lab06-b-token"}) as c:
            ext = await c.client.get_extended_agent_card(GetExtendedAgentCardRequest())
    assert card.name == "Translator B"
    assert "legal_translation" in [s.id for s in ext.skills]
