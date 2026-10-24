"""The Trip Planner graph on its own (no A2A): the baseline the notebook starts from."""

from __future__ import annotations

from typing import Any

import pytest
from lab04_langgraph.agents import trip_planner as tp
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

FULL = "Plan a trip from Chicago to Lisbon, 12-16 May, budget $2000, 2 travelers"


def planner(decision: Any) -> Any:
    return tp.build_graph(InMemorySaver(), decision)


def cfg(thread: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread}}


async def run(graph: Any, thread: str, first: Any) -> dict[str, Any]:
    return await graph.ainvoke(first, cfg(thread))  # type: ignore[no-any-return]


def interrupt_of(out: dict[str, Any]) -> dict[str, Any]:
    return dict(out["__interrupt__"][0].value)


async def test_a_complete_low_risk_request_runs_straight_to_the_itinerary(low_risk) -> None:  # type: ignore[no-untyped-def]
    g = planner(low_risk)
    out = await run(g, "t1", {"messages": [("user", FULL)]})
    assert "__interrupt__" not in out
    assert out["total"] == 2200 and out["risk"] == 0.0
    assert out["itinerary"].endswith("Total: $2200 (budget $2000)")


async def test_a_high_risk_plan_asks_for_approval_with_options(high_risk) -> None:  # type: ignore[no-untyped-def]
    g = planner(high_risk)
    out = await run(g, "t2", {"messages": [("user", FULL)]})
    payload = interrupt_of(out)
    assert payload["options"] == ["yes", "no"] and "$2200" in payload["question"]
    done = await run(g, "t2", Command(resume="yes"))
    assert done["approved"] is True and "Total: $2200" in done["itinerary"]


@pytest.mark.parametrize(
    ("score", "route"), [(1.49, "compose"), (1.5, "approval"), (2.0, "approval")]
)
def test_risk_gate_routes_on_the_decision_score(score: float, route: str) -> None:
    assert tp.route_after_risk({"risk": score}) == route  # type: ignore[typeddict-item]


async def test_missing_fields_interrupt_until_everything_is_known(low_risk) -> None:  # type: ignore[no-untyped-def]
    g = planner(low_risk)
    out = await run(g, "t3", {"messages": [("user", "Plan a trip to Lisbon")]})
    assert set(interrupt_of(out)["missing"]) == {"origin", "dates", "budget", "travelers"}
    out = await run(g, "t3", Command(resume="from Chicago, 12-16 May"))  # still missing two
    assert set(interrupt_of(out)["missing"]) == {"budget", "travelers"}
    done = await run(g, "t3", Command(resume="budget $5000, 2 travelers"))
    assert done["request"]["origin"] == "Chicago" and done["total"] == 2200 and "itinerary" in done


async def test_no_goes_back_to_cheaper_hotels_once_then_a_second_no_ends(high_risk) -> None:  # type: ignore[no-untyped-def]
    g = planner(high_risk)
    await run(g, "t4", {"messages": [("user", FULL)]})
    again = await run(g, "t4", Command(resume="no"))
    assert "$1940" in interrupt_of(again)["question"]  # cheaper hotel: one loop back
    done = await run(g, "t4", Command(resume="no"))
    assert done["approved"] is False and "not approved" in done["itinerary"]  # max one loop


async def test_the_decision_probabilities_are_kept_in_state(high_risk) -> None:  # type: ignore[no-untyped-def]
    g = planner(high_risk)
    await run(g, "t5", {"messages": [("user", FULL)]})
    state = (await g.aget_state(cfg("t5"))).values
    assert state["risk"] == 2.0 and state["risk_detail"]["backend"] == "fake"
    assert "budget of $2000" in state["risk_detail"]["facts"]


async def test_compose_uses_the_reasoning_model_and_a_deterministic_footer(low_risk) -> None:  # type: ignore[no-untyped-def]
    from a2a_labs_common.testing import FAKE_CALLS

    g = planner(low_risk)
    out = await run(g, "t6", {"messages": [("user", FULL)]})
    assert [c["role"] for c in FAKE_CALLS] == ["reasoning"]  # only compose calls an LLM
    assert out["itinerary"].startswith("# Itinerary")
