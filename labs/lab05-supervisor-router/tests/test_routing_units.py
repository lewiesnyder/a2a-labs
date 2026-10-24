"""Unit tests for the supervisor's pieces that do not need the specialists over the wire."""

from __future__ import annotations

from typing import Any

from a2a.types import AgentCard
from a2a_labs_common.models.decision import FakeDecisionModel, LLMFallbackDecisionModel
from lab05_supervisor.agents.supervisor import Supervisor, merge_dicts
from lab05_supervisor.cards import supervisor_card
from lab05_supervisor.discovery import Directory


def test_merge_dicts_merges_parallel_branches_and_resets() -> None:
    assert merge_dicts({"a": "1"}, {"b": "2"}) == {"a": "1", "b": "2"}
    assert merge_dicts({"a": "1"}, {"__reset__": True}) == {}
    assert merge_dicts(None, None) == {}


def test_router_mode_llm_swaps_nimble_for_the_reasoning_model(monkeypatch: Any) -> None:
    monkeypatch.setenv("ROUTER_MODE", "llm")
    sup = Supervisor(directory=Directory([]))
    assert isinstance(sup.model, LLMFallbackDecisionModel)
    assert sup.model.fallback_role == "reasoning"
    # an injected decision model always wins (tests, notebooks)
    fake = FakeDecisionModel()
    assert Supervisor(decision=fake, directory=Directory([])).model is fake


async def test_the_directory_builds_options_from_cards_and_keeps_a_stale_card_on_error() -> None:
    cards: dict[str, AgentCard | Exception] = {"svc-a": supervisor_card()}

    def fetch(service: str) -> AgentCard:
        got = cards[service]
        if isinstance(got, Exception):
            raise got
        return got

    d = Directory(["svc-a", "svc-b"], ttl=0, fetch=fetch)
    cards["svc-b"] = ConnectionError("down")  # never seen: skipped
    first = await d.discover()
    assert [s.service for s in first] == ["svc-a"]
    assert first[0].criteria == (
        f"{supervisor_card().description}. Skills: {supervisor_card().skills[0].name}"
    )
    cards["svc-a"] = ConnectionError("down now")  # seen before: kept
    assert [s.service for s in await d.discover()] == ["svc-a"]
    d.unregister("svc-a")
    assert await d.discover() == []


async def test_the_directory_caches_cards_within_the_ttl() -> None:
    d = Directory(["svc-a"], ttl=60, fetch=lambda s: supervisor_card())
    await d.discover()
    await d.discover()
    assert d.fetches == 1
    d.register("svc-b")  # registering forces a fresh round
    await d.discover()
    assert d.fetches == 2


async def test_split_falls_back_to_clauses_when_the_model_returns_no_json() -> None:
    """The quick model's reply is not JSON for this request, so clauses + decision are used."""
    request = "weather in Paris and convert 10 USD to EUR"
    sup = Supervisor(
        decision=FakeDecisionModel(
            script=[
                {"question": "route", "match": "weather", "answer": "Weather"},
                {"question": "route", "answer": "Currency"},
            ]
        ),
        directory=Directory([]),
    )
    state: Any = {
        "request": request,
        "decision_trace": [],
        "catalog": [
            {"name": "Currency Converter", "service": "c", "description": "money"},
            {"name": "Weather Reporter", "service": "w", "description": "rain"},
        ],
    }
    out = await sup.split(state)
    plan = {r["agent"]: r["sub_request"] for r in out["plan"]}
    assert plan == {
        "Weather Reporter": "weather in Paris",
        "Currency Converter": "convert 10 USD to EUR",
    }
    assert out["decision_trace"][-1]["source"] == "clause split + decision"


def test_duplicate_routes_for_one_agent_are_merged() -> None:
    """A small model that repeats its JSON list must not start two parallel tasks per agent."""
    r = Supervisor._one_route_per_agent
    a = {"agent": "W", "service": "w", "sub_request": "weather in Tokyo"}
    b = {"agent": "C", "service": "c", "sub_request": "convert 5 USD to EUR"}
    c = {"agent": "W", "service": "w", "sub_request": "and in Rome"}
    out = r([a, b, dict(a), c])  # type: ignore[list-item]
    assert [x["agent"] for x in out] == ["W", "C"]
    assert out[0]["sub_request"] == "weather in Tokyo; and in Rome"


def test_single_specialist_supervisors_may_give_up_on_the_compound_prompt() -> None:
    from lab05_supervisor.prompts import CURRENCY, ROUTING_TABLE, WEATHER

    compound, plain = ROUTING_TABLE[4], ROUTING_TABLE[0]
    assert compound.accepts_single({WEATHER}) and compound.accepts_single(set())
    assert not compound.accepts_single({CURRENCY})
    assert plain.accepts_single({CURRENCY}) and not plain.accepts_single(set())
