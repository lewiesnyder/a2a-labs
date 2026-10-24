"""The delegator: search_registry -> verify_each -> select -> delegate (pattern 5).

Delegate only after verification. The registry is a convenience, not an authority: every
candidate is re-verified HERE against the local trust store, and the connection is built from the
verified card OBJECT, so nothing is fetched (and nothing can change) between check and use.
Implements: spec lab06 §5.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Any

import httpx
from a2a_labs_common.a2a.client import bridge_card_urls, connect_with_card, send_text
from a2a_labs_common.a2a.langgraph import message_text
from a2a_labs_common.a2a.trust import CardVerifier, TrustStore, card_from_json
from a2a_labs_common.config import endpoint
from a2a_labs_common.models import get_decision_model
from a2a_labs_common.models.decision import DecisionModel, Question
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from opentelemetry import trace
from typing_extensions import TypedDict

_tracer = trace.get_tracer("lab06_registry")
NONE = "none"


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    request: str
    source: str  # "registry" or "injected"
    candidates: list[dict[str, Any]]
    verified: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    decision: dict[str, Any]
    chosen: str | None
    result: str


def _describe(card: dict[str, Any]) -> str:
    skills = "; ".join(f"{s['name']}: {s['description']}" for s in card.get("skills", []))
    return f"{card.get('description', '')} Skills: {skills}"


class Delegator:
    def __init__(
        self,
        store: TrustStore,
        *,
        registry_url: str | None = None,
        injected_cards: list[str] | None = None,
        decision: DecisionModel | None = None,
        skill: str = "translate",
    ) -> None:
        self.verifier = CardVerifier(store)
        self.registry_url = (registry_url or endpoint("lab06-registry")).rstrip("/")
        env = os.environ.get("DELEGATOR_INJECTED_CARDS", "")
        self.injected = injected_cards or [p for p in env.split(",") if p.strip()]
        self.decision = decision
        self.skill = skill

    async def search_registry(self, state: State) -> dict[str, Any]:
        users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        request = message_text(users[-1]) if users else ""
        if self.injected:  # direct configuration: trusted cards from files, no registry at all
            found = []
            for path in self.injected:
                data = json.loads(await asyncio.to_thread(Path(path).read_text))
                url = data["supportedInterfaces"][0]["url"]
                found.append({"url": url, "card": data})
            return {"request": request, "source": "injected", "candidates": found}
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(f"{self.registry_url}/agents", params={"skill": self.skill})
            r.raise_for_status()
        return {"request": request, "source": "registry", "candidates": r.json()}

    async def verify_each(self, state: State) -> dict[str, Any]:
        verified, rejected = [], []
        for cand in state["candidates"]:
            card = card_from_json(cand["card"])
            report = self.verifier.verify(card, cand["url"])  # never trust the registry's verdict
            row = {"name": card.name, "url": cand["url"], "report": report.model_dump()}
            (verified if report.ok else rejected).append({**row, "card": cand["card"]})
        return {"verified": verified, "rejected": rejected}

    async def select(self, state: State) -> dict[str, Any]:
        verified = state["verified"]
        if not verified:
            return {"chosen": None, "decision": {"reason": "no verified candidate"}}
        options = {v["name"]: _describe(v["card"]) for v in verified}
        options[NONE] = "None of these can do the job."
        model = self.decision or get_decision_model()
        with _tracer.start_as_current_span("delegator.select"):
            res = await model.decide(
                f"Request: {state['request']}",
                {
                    "best_candidate": Question(
                        type="choice",
                        instructions="Which translator can handle every language in the request?",
                        criteria=options,
                    )
                },
            )
        ans = res.answers["best_candidate"]
        chosen = ans.choice if ans.choice in options and ans.choice != NONE else None
        return {
            "chosen": chosen,
            "decision": {
                "probabilities": ans.probabilities,
                "backend": res.backend,
                "options": list(options),
            },
        }

    async def delegate(self, state: State) -> dict[str, Any]:
        if state.get("chosen") is None:
            return {"result": "No trusted translator could be found for this request."}
        pick = next(v for v in state["verified"] if v["name"] == state["chosen"])
        card = card_from_json(pick["card"])
        bridge_card_urls(card)  # after verification: host runs rewrite docker names to localhost
        async with await connect_with_card(card, streaming=False) as conn:
            result = await send_text(conn, state["request"])
        return {"result": result.text}

    @staticmethod
    async def answer(state: State) -> dict[str, Any]:
        return {"messages": [AIMessage(state["result"])]}

    def build(self, checkpointer: Any = None) -> Any:
        g = StateGraph(State)
        for name, fn in (
            ("search_registry", self.search_registry),
            ("verify_each", self.verify_each),
            ("select", self.select),
            ("delegate", self.delegate),
            ("answer", self.answer),
        ):
            g.add_node(name, fn)
        g.add_edge(START, "search_registry")
        g.add_edge("search_registry", "verify_each")
        g.add_edge("verify_each", "select")
        g.add_edge("select", "delegate")
        g.add_edge("delegate", "answer")
        g.add_edge("answer", END)
        return g.compile(checkpointer=checkpointer)
