"""The Trip Planner graph (Pattern 3): parse -> search -> risk gate -> (approval) -> compose.

Every LangGraph concept here has an A2A counterpart on the wire (see the Lab 04 notebook):
a missing field and the approval step are ``interrupt()``s -> INPUT_REQUIRED, node starts are
WORKING events, END is the ``itinerary`` artifact. Implements: spec lab04 §2.1.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any

from a2a_labs_common.a2a.langgraph import message_text
from a2a_labs_common.models import get_decision_model, get_langchain_model
from a2a_labs_common.models.decision import DecisionModel, Question
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

from lab04_langgraph.agents.travel_data import (
    TripRequest,
    find_flights,
    find_hotels,
    parse_trip_text,
    plan_total,
)

RISK_LEVELS = ["low", "medium", "high"]
RISK_THRESHOLD = 1.5  # score >= 1.5 means "needs approval"


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    # A plain dict, not a pydantic object: LangGraph warns that unregistered types in a
    # checkpoint will stop deserialising in a future release.
    request: dict[str, Any] | None
    flights: list[dict[str, Any]]
    hotels: list[dict[str, Any]]
    total: int
    risk: float
    risk_detail: dict[str, Any]
    cheaper: bool
    loops: int
    approved: bool | None
    itinerary: str | None


def _step_delay() -> float:
    return float(os.environ.get("TRIP_STEP_DELAY", "0.4"))


def _last_user_text(state: State) -> str:
    users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    return message_text(users[-1]) if users else ""


def _reply_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("resume", value.get("answer", value)))
    return str(value)


def parse_request(state: State) -> dict[str, Any]:
    """Pure parsing, then ``interrupt()`` until nothing is missing.

    LangGraph re-runs a node from its first line on resume; because this node has no side effects
    before the interrupt, that replay is harmless: the interrupt just returns the stored reply.
    """
    existing = TripRequest(**state["request"]) if state.get("request") else None
    req = parse_trip_text(_last_user_text(state), existing)
    while req.missing():
        missing = req.missing()
        reply = interrupt(
            {
                "question": f"I still need: {', '.join(missing)}. Reply for example "
                "'from Chicago, 12-16 May, budget $2000, 2 travelers'.",
                "missing": missing,
            }
        )
        req = parse_trip_text(_reply_text(reply), req)
    return {"request": req.model_dump()}


async def search_flights(state: State) -> dict[str, Any]:
    await asyncio.sleep(
        _step_delay()
    )  # simulates a slow supplier (and gives cancel something to hit)
    return {"flights": find_flights(TripRequest(**state["request"]))}  # type: ignore[arg-type]


async def search_hotels(state: State) -> dict[str, Any]:
    await asyncio.sleep(_step_delay())
    req = TripRequest(**state["request"])  # type: ignore[arg-type]
    hotels = find_hotels(req, cheaper=bool(state.get("cheaper")))
    return {"hotels": hotels, "total": plan_total(req, state["flights"], hotels)}


def make_risk_gate(decision: DecisionModel | None) -> Any:
    async def risk_gate(state: State) -> dict[str, Any]:
        """The DECISION model scores how risky the plan is; a conditional edge reads the score."""
        req = TripRequest(**state["request"])  # type: ignore[arg-type]
        facts = (
            f"Trip plan total ${state['total']} against a traveller budget of ${req.budget}. "
            f"{req.travelers} travellers, {req.nights} nights."
        )
        model = decision or get_decision_model()
        result = await model.decide(
            facts,
            {
                "risk": Question(
                    type="score",
                    instructions=("How risky or expensive is this plan relative to the budget?"),
                    criteria=RISK_LEVELS,
                )
            },
        )
        ans = result.answers["risk"]
        return {
            "risk": float(ans.score or 0.0),
            "risk_detail": {
                "probabilities": ans.probabilities,
                "backend": result.backend,
                "facts": facts,
            },
        }

    return risk_gate


def route_after_risk(state: State) -> str:
    return "approval" if state.get("risk", 0.0) >= RISK_THRESHOLD else "compose"


def approval(state: State) -> dict[str, Any]:
    req = TripRequest(**state["request"])  # type: ignore[arg-type]
    answer = interrupt(
        {
            "question": f"Total is ${state['total']} (budget ${req.budget}). Approve? yes/no",
            "options": ["yes", "no"],
            "total": state["total"],
        }
    )
    yes = _reply_text(answer).strip().lower().startswith("y")
    if yes:
        return {"approved": True}
    return {"approved": False, "cheaper": True, "loops": state.get("loops", 0) + 1}


def route_after_approval(state: State) -> str:
    # "no" goes back to the hotels once with a cheaper preference; a second "no" ends in compose.
    return (
        "search_hotels"
        if state.get("approved") is False and state.get("loops", 0) <= 1
        else "compose"
    )


async def compose(state: State) -> dict[str, Any]:
    req = TripRequest(**state["request"])  # type: ignore[arg-type]
    facts = {
        "route": f"{req.origin} to {req.destination}",
        "dates": req.dates,
        "nights": req.nights,
        "travelers": req.travelers,
        "flight": state["flights"][0],
        "hotel": state["hotels"][0],
        "total": state["total"],
        "budget": req.budget,
    }
    prompt = (
        "Write the itinerary markdown for this trip in under 120 words: a heading, the flight, "
        f"the hotel and two things to do. Facts: {json.dumps(facts)}"
    )
    body = str((await get_langchain_model("reasoning").ainvoke([HumanMessage(prompt)])).content)
    note = (
        ""
        if state.get("approved") is not False
        else "\n\n_The cheaper plan was not approved either._"
    )
    # The footer is deterministic so downstream agents can rely on it regardless of the LLM.
    footer = f"\n\nTotal: ${state['total']} (budget ${req.budget})"
    text = body.strip() + note + footer
    return {"itinerary": text, "messages": [AIMessage(text)]}


def build_graph(checkpointer: Any = None, decision: DecisionModel | None = None) -> Any:
    """Compile the planner. ``checkpointer`` is REQUIRED for interrupts (None only for the Agent
    Server, which supplies its own). ``decision`` lets tests inject a scripted decision model."""
    g = StateGraph(State)
    g.add_node("parse_request", parse_request)
    g.add_node("search_flights", search_flights)
    g.add_node("search_hotels", search_hotels)
    g.add_node("risk_gate", make_risk_gate(decision))
    g.add_node("approval", approval)
    g.add_node("compose", compose)
    g.add_edge(START, "parse_request")
    g.add_edge("parse_request", "search_flights")
    g.add_edge("search_flights", "search_hotels")
    g.add_edge("search_hotels", "risk_gate")
    g.add_conditional_edges("risk_gate", route_after_risk, ["approval", "compose"])
    g.add_conditional_edges("approval", route_after_approval, ["search_hotels", "compose"])
    g.add_edge("compose", END)
    return g.compile(checkpointer=checkpointer)


# For `langgraph dev` / the Agent Server (langgraph.json): no checkpointer, the platform adds one.
graph = build_graph()
