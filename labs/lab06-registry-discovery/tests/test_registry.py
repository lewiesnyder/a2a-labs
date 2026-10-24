"""The curated registry: verified agents are listed, anything else is quarantined with reasons."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

# mode -> the first check that fails (spec lab06 §6 step 6)
ROGUE_FAILS = {
    "unsigned": "signature_present",
    "untrusted_key": "key_trusted",
    "tampered": "signature_valid",
    "name_spoof": "key_trusted",
}


async def names(registry: Any, **params: Any) -> list[str]:
    async with httpx.AsyncClient() as http:
        rows = (await http.get(f"{registry.url}/agents", params=params)).json()
    return sorted(r["card"]["name"] for r in rows)


async def test_by_default_only_the_verified_agents_are_returned(registry: Any) -> None:
    assert await names(registry) == ["Translator A", "Translator B"]
    assert await names(registry, skill="translate", tag="asian-languages") == ["Translator B"]
    assert await names(registry, q="spanish french") == ["Translator A", "Translator B"]
    assert await names(registry, skill="nothing") == []


@pytest.mark.parametrize("mode", ROGUE_FAILS)
async def test_the_rogue_is_quarantined_in_every_mode(
    registry: Any, agents: dict[str, Any], mode: str
) -> None:
    async with httpx.AsyncClient() as http:
        await http.post(f"{agents['lab06-agent-rogue'].url}/_rogue/mode", json={"mode": mode})
        entry = (
            await http.post(f"{registry.url}/agents", json={"url": agents["lab06-agent-rogue"].url})
        ).json()
        assert entry["verified"] is False
        report = entry["verification_report"]
        first = next(c for c in report["checks"] if not report["checks"][c])
        assert first == ROGUE_FAILS[mode] and report["reasons"]
        assert await names(registry) == ["Translator A", "Translator B"]  # still hidden
        everything = (
            await http.get(f"{registry.url}/agents", params={"include_unverified": True})
        ).json()
    assert {e["verified"] for e in everything if e["url"] == entry["url"]} == {False}
    assert len(everything) == 3


async def test_name_spoof_is_validly_signed_but_by_the_wrong_key(
    registry: Any, agents: dict[str, Any]
) -> None:
    async with httpx.AsyncClient() as http:
        await http.post(
            f"{agents['lab06-agent-rogue'].url}/_rogue/mode", json={"mode": "name_spoof"}
        )
        e = (
            await http.post(f"{registry.url}/agents", json={"url": agents["lab06-agent-rogue"].url})
        ).json()
    checks = e["verification_report"]["checks"]
    assert e["card"]["name"] == "Translator A" and e["verification_report"]["kid"] == "rogue"
    assert checks["signature_present"] and not checks["key_trusted"] and not checks["name_bound"]


async def test_refresh_detects_a_changed_card_and_reverifies(
    registry: Any, agents: dict[str, Any]
) -> None:
    async with httpx.AsyncClient() as http:
        rogue = next(
            e
            for e in (
                await http.get(f"{registry.url}/agents", params={"include_unverified": True})
            ).json()
            if e["url"] == agents["lab06-agent-rogue"].url
        )
        same = (await http.post(f"{registry.url}/agents/{rogue['id']}/refresh")).json()
        assert same["changed"] is False and same["etag"] == rogue["etag"]
        await http.post(f"{agents['lab06-agent-rogue'].url}/_rogue/mode", json={"mode": "unsigned"})
        changed = (await http.post(f"{registry.url}/agents/{rogue['id']}/refresh")).json()
        assert changed["changed"] is True and changed["etag"] != rogue["etag"]
        assert changed["verification_report"]["checks"]["signature_present"] is False


async def test_an_unreachable_agent_is_marked_unhealthy_and_delete_removes_it(
    registry: Any, agents: dict[str, Any]
) -> None:
    async with httpx.AsyncClient() as http:
        rows = (await http.get(f"{registry.url}/agents", params={"skill": "translate"})).json()
        a = next(r for r in rows if r["card"]["name"] == "Translator A")
        agents["lab06-agent-a"].stop()
        refreshed = (await http.post(f"{registry.url}/agents/{a['id']}/refresh")).json()
        assert refreshed["healthy"] is False
        assert await names(registry) == ["Translator B"]
        assert (await http.delete(f"{registry.url}/agents/{a['id']}")).json() == {"deleted": True}
        assert (await http.get(f"{registry.url}/agents/{a['id']}")).status_code == 404
