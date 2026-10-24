"""The LangGraph supervisor: discover -> triage -> route -> delegate (fan-out) -> synthesize.

Pattern 4: the supervisor routes work to remote specialists and tracks each one separately.
Routing is a DECISION-MODEL call (Nimble ``choice``) inside a conditional edge; the options are
built from the discovered Agent Cards, so a new specialist needs no code change.
Implements: spec lab05 §3.

    START -> discover -> triage --(ambiguous)--> clarify (interrupt) --+
                           |                                           |
                           +-------------------------------------------v
                         route --none--> answer_directly --> END
                           |--single--+
                           |--multi--> split --+
                                               v
                                      delegate x N (Send, parallel) -> check_complete
                                               ^----(retry once)------------+ |
                                                                            v
                                                        synthesize -> END
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Annotated, Any

from a2a_labs_common.a2a.ids import derived_context_id
from a2a_labs_common.a2a.langgraph import RemoteA2ANode, message_text
from a2a_labs_common.models import get_decision_model, get_langchain_model
from a2a_labs_common.models.decision import (
    DecisionModel,
    DecisionResult,
    LLMFallbackDecisionModel,
    Question,
)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt
from opentelemetry import trace
from typing_extensions import TypedDict

from lab05_supervisor.discovery import Directory, Specialist
from lab05_supervisor.ledger import Ledger, LedgerEntry

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("lab05_supervisor")

NONE = "none"
AMBIGUOUS_ABOVE = 0.6  # triage: P(ambiguous) above this asks the user to clarify
MULTI_ABOVE = 0.5  # route: P(needs several specialists) above this fans out
COMPLETE_BELOW = 0.5  # check_complete: P(fully answered) below this retries once
RESET = "__reset__"


def merge_dicts(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer for parallel branches. ``{"__reset__": True, ...}`` starts the dict afresh."""
    new = new or {}
    if new.get(RESET):
        return {k: v for k, v in new.items() if k != RESET}
    return {**(old or {}), **new}


class Route(TypedDict):
    agent: str  # card name
    service: str
    sub_request: str


class SupervisorState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    request: str
    catalog: list[dict[str, Any]]  # the discovery table (one row per specialist)
    plan: list[Route]
    multi: bool
    results: Annotated[dict[str, str], merge_dicts]
    failures: Annotated[dict[str, str], merge_dicts]
    ledger: Annotated[dict[str, LedgerEntry], merge_dicts]  # per-remote tracking, serialisable
    decision_trace: list[dict[str, Any]]  # the routing probabilities, for display
    retries: int
    retry: bool
    ambiguous: bool


class DelegateInput(TypedDict):
    agent: str
    service: str
    sub_request: str
    prior: LedgerEntry | None


def _evidence(request: str, previous: dict[str, str] | None = None) -> str:
    text = f"User request: {request}"
    if previous:
        text += "\nAlready answered:\n" + "\n".join(f"- {a}: {r}" for a, r in previous.items())
    return text


def _answer_summary(result: DecisionResult, name: str) -> dict[str, Any]:
    a = result.answers[name]
    return {
        "question": name,
        "choice": a.choice,
        "p": a.noul,
        "probabilities": a.probabilities,
        "backend": result.backend,
        "model": result.model,
        "latency_ms": round(result.latency_ms, 1),
    }


def _router_model(mode: str, decision: DecisionModel | None) -> DecisionModel:
    """ROUTER_MODE=llm swaps Nimble for the reasoning model answering the same questions."""
    if decision is not None:
        return decision
    if mode == "llm":
        return LLMFallbackDecisionModel("reasoning")
    return get_decision_model()


class Supervisor:
    def __init__(
        self,
        *,
        decision: DecisionModel | None = None,
        directory: Directory | None = None,
        ledger: Ledger | None = None,
        router_mode: str | None = None,
    ) -> None:
        self.router_mode = router_mode or os.environ.get("ROUTER_MODE", "nimble")
        self.model = _router_model(self.router_mode, decision)
        self.directory = directory or Directory()
        self.ledger = ledger or Ledger()
        self._nodes: dict[str, RemoteA2ANode] = {}
        self._agent_of: dict[str, str] = {}  # service -> card name, for the ledger callback

    # ── nodes ──────────────────────────────────────────────────────────────────────────────
    async def discover(self, state: SupervisorState) -> dict[str, Any]:
        specialists = await self.directory.discover()
        users = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
        return {
            "request": message_text(users[-1]) if users else "",
            "catalog": [s.row() for s in specialists],
            "plan": [],
            "multi": False,
            "retry": False,
            "retries": 0,
            "decision_trace": [],
            "results": {RESET: True},
            "failures": {RESET: True},
        }

    async def triage(self, state: SupervisorState) -> dict[str, Any]:
        q = Question(
            type="noul",
            # Worded about the TOPIC being unclear, not about missing details: "Convert 100 USD"
            # has a clear topic (the currency specialist asks for the missing target itself).
            instructions=(
                "Is the topic of this request (money, weather, city facts, or something else) "
                "completely unclear?"
            ),
        )
        res = await self.model.decide(_evidence(state["request"]), {"unclear_topic": q})
        info = _answer_summary(res, "unclear_topic")
        return {
            "ambiguous": (info["p"] or 0.0) > AMBIGUOUS_ABOVE,
            "decision_trace": [*state["decision_trace"], info],
        }

    def clarify(self, state: SupervisorState) -> dict[str, Any]:
        answer = interrupt(
            {
                "kind": "clarify",
                "question": "I can convert currencies, report the weather and give city facts. "
                "What would you like?",
            }
        )
        text = answer if isinstance(answer, str) else str(answer)
        return {"request": f"{state['request']}\n{text}", "ambiguous": False}

    async def route(self, state: SupervisorState) -> dict[str, Any]:
        catalog = state["catalog"]
        options = {c["name"]: c["criteria"] for c in catalog}
        options[NONE] = "No specialist fits this request; answer it directly."
        questions = {
            "route": Question(
                type="choice",
                instructions="Which specialist should handle this request?",
                criteria=options,
            ),
            "multi": Question(
                type="noul",
                instructions="Does the request contain several distinct tasks for different "
                "specialists?",
            ),
        }
        with _tracer.start_as_current_span(
            "supervisor.route", attributes={"supervisor.router": self.router_mode}
        ) as span:
            res = await self.model.decide(
                _evidence(state["request"], state.get("results") or None), questions
            )
            choice = res.answers["route"].choice or NONE
            multi_p = res.answers["multi"].noul or 0.0
            span.set_attribute("supervisor.route.choice", choice)
            span.set_attribute("supervisor.route.multi_p", multi_p)
        trace_entry = {
            **_answer_summary(res, "route"),
            "multi_p": multi_p,
            "options": list(options),
        }
        out: dict[str, Any] = {
            "decision_trace": [*state["decision_trace"], trace_entry],
            "multi": False,
            "plan": [],
        }
        # Multi-intent first: no single specialist fits "weather AND currency", so a real
        # decision model answers `none` to the choice and a high P(multi) to the second question.
        if multi_p > MULTI_ABOVE:
            return {**out, "multi": True}
        if choice == NONE:
            return out
        service = next((c["service"] for c in catalog if c["name"] == choice), "")
        return {**out, "plan": [Route(agent=choice, service=service, sub_request=state["request"])]}

    async def split(self, state: SupervisorState) -> dict[str, Any]:
        """One sub-request per specialist: the quick model first, a clause split as fallback."""
        catalog = state["catalog"]
        names = {c["name"]: c["service"] for c in catalog}
        listing = "\n".join(f"- {c['name']}: {c['description']}" for c in catalog)
        prompt = (
            "Split the user request into one sub-request per specialist. Each sub_request must be "
            "a complete question in the user's own words that repeats every city and currency "
            'it needs, e.g. "What is the weather in Tokyo?". Reply with ONLY a JSON '
            'list of {"agent": <exact specialist name>, "sub_request": <text>}.\n'
            f"Specialists:\n{listing}\nRequest: {state['request']}"
        )
        routes: list[Route] = []
        try:
            reply = message_text(await get_langchain_model("quick").ainvoke(prompt))
            raw = json.loads(reply[reply.index("[") : reply.rindex("]") + 1])
            routes = [
                Route(agent=r["agent"], service=names[r["agent"]], sub_request=r["sub_request"])
                for r in raw
                if r.get("agent") in names and r.get("sub_request")
            ]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("split: the quick model did not return usable JSON (%s)", exc)
        routes = self._one_route_per_agent(routes)
        source = "quick-model JSON"
        if len(routes) < 2:
            routes, source = await self._split_by_clauses(state, names), "clause split + decision"
        return {
            "plan": routes,
            "decision_trace": [
                *state["decision_trace"],
                {"question": "split", "source": source, "agents": [r["agent"] for r in routes]},
            ],
        }

    @staticmethod
    def _one_route_per_agent(routes: list[Route]) -> list[Route]:
        """Two parallel branches for one agent would share its remote context and collide (a
        small model sometimes repeats its list), so merge them into one sub-request."""
        merged: dict[str, Route] = {}
        for r in routes:
            if r["agent"] not in merged:
                merged[r["agent"]] = Route(**r)
            elif r["sub_request"] not in merged[r["agent"]]["sub_request"]:
                merged[r["agent"]]["sub_request"] += f"; {r['sub_request']}"
        return list(merged.values())

    async def _split_by_clauses(self, state: SupervisorState, names: dict[str, str]) -> list[Route]:
        clauses = [
            c.strip(" .")
            for c in re.split(r",?\s+(?:and|plus|also)\s+|[;,]\s+", state["request"])
            if c.strip(" .")
        ]
        options = {n: n for n in names}
        merged: dict[str, list[str]] = {}
        for clause in clauses:
            res = await self.model.decide(
                _evidence(clause),
                {"route": Question(type="choice", instructions="Which?", criteria=options)},
            )
            merged.setdefault(res.answers["route"].choice or next(iter(options)), []).append(clause)
        return [
            Route(agent=a, service=names[a], sub_request="; ".join(c)) for a, c in merged.items()
        ]

    def _node(self, service: str) -> RemoteA2ANode:
        if service not in self._nodes:
            self._nodes[service] = RemoteA2ANode(
                service,
                input_fn=lambda s: s["sub_request"],
                output_key="text",
                on_update=self._on_update,
            )
        return self._nodes[service]

    def _on_update(
        self, thread: str, service: str, context_id: str, task_id: str | None, state: str
    ) -> None:
        self.ledger.record(thread, self._agent_of[service], service, context_id, task_id, state)

    async def delegate(self, payload: DelegateInput, config: RunnableConfig) -> dict[str, Any]:
        """Send one sub-request to one remote agent and keep its ledger entry up to date.

        RemoteA2ANode already does the hard parts: one derived remote context per
        (supervisor context, agent), a remote INPUT_REQUIRED surfaced as a local ``interrupt()``,
        the answer forwarded to the SAME remote task, and no re-send when LangGraph re-runs the
        node after the interrupt.
        """
        agent, service = payload["agent"], payload["service"]
        thread = str((config.get("configurable") or {}).get("thread_id", "default"))
        self._agent_of[service] = agent
        self.ledger.seed(thread, payload.get("prior"))
        before = self.ledger.updates(thread, agent)
        with _tracer.start_as_current_span(
            "supervisor.delegate",
            attributes={"supervisor.agent": agent, "supervisor.service": service},
        ) as span:
            out = await self._node(service)({"sub_request": payload["sub_request"]}, config)
            if out.get("error") and self.ledger.updates(thread, agent) == before:
                # No reply ever arrived (connection refused, timeout): record it as unreachable.
                self.ledger.unreachable(thread, agent, service, derived_context_id(thread, service))
            entry = self.ledger.entry(thread, agent)
            span.set_attribute("supervisor.remote_state", entry["last_state"] if entry else "")
        update: dict[str, Any] = {"ledger": {agent: entry}}
        if out.get("error"):
            update["failures"] = {agent: out["error"]}
        else:
            update["results"] = {agent: out["text"]}
        return update

    async def check_complete(self, state: SupervisorState) -> dict[str, Any]:
        results = state.get("results") or {}
        if not results or state.get("failures"):
            return {"retry": False}  # nothing to judge, or retrying would hit the same outage
        q = Question(
            type="noul",
            instructions="Does every part of the request have an answer in the results?",
        )
        evidence = _evidence(state["request"], results)
        res = await self.model.decide(evidence, {"answered": q})
        info = _answer_summary(res, "answered")
        retry = (info["p"] or 0.0) < COMPLETE_BELOW and state.get("retries", 0) < 1
        return {
            "retry": retry,
            "retries": state.get("retries", 0) + (1 if retry else 0),
            "decision_trace": [*state["decision_trace"], info],
        }

    async def synthesize(self, state: SupervisorState) -> dict[str, Any]:
        results, failures = state.get("results") or {}, state.get("failures") or {}
        if not results:  # every specialist failed: nothing to synthesize, say so plainly
            text = "I could not get an answer: " + "; ".join(
                f"{a} is unavailable ({e})" for a, e in failures.items()
            )
        elif len(results) == 1 and not failures:
            text = next(iter(results.values()))  # one answer: pass it through untouched
        else:
            lines = [f"- {a}: {r}" for a, r in results.items()]
            lines += [f"- {a}: UNAVAILABLE ({e})" for a, e in failures.items()]
            prompt = (
                "Synthesize the final answer to the user's request from the specialist results, "
                "keeping every number. Mention any specialist that was unavailable.\n"
                f"Request: {state['request']}\nResults:\n" + "\n".join(lines)
            )
            text = message_text(await get_langchain_model("reasoning").ainvoke(prompt))
        return {"messages": [AIMessage(text)]}

    async def answer_directly(self, state: SupervisorState) -> dict[str, Any]:
        prompt = (
            "You are a travel-desk supervisor. No specialist (currency, weather, city facts) "
            f"fits this request, so answer it briefly yourself.\n{state['request']}"
        )
        return {
            "messages": [
                AIMessage(message_text(await get_langchain_model("quick").ainvoke(prompt)))
            ]
        }

    # ── edges ──────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def after_triage(state: SupervisorState) -> str:
        return "clarify" if state.get("ambiguous") else "route"

    @staticmethod
    def _sends(state: SupervisorState) -> list[Send]:
        ledger = state.get("ledger") or {}
        return [
            Send(
                "delegate",
                DelegateInput(
                    agent=r["agent"],
                    service=r["service"],
                    sub_request=r["sub_request"],
                    prior=ledger.get(r["agent"]),
                ),
            )
            for r in state["plan"]
        ]

    def after_route(self, state: SupervisorState) -> list[Send] | str:
        if state.get("multi"):
            return "split"
        if not state.get("plan"):
            return "answer_directly"
        return self._sends(state)

    def after_split(self, state: SupervisorState) -> list[Send]:
        return self._sends(state)

    @staticmethod
    def after_check(state: SupervisorState) -> str:
        return "route" if state.get("retry") else "synthesize"

    def build(self, checkpointer: Any = None) -> Any:
        g = StateGraph(SupervisorState)
        g.add_node("discover", self.discover)
        g.add_node("triage", self.triage)
        g.add_node("clarify", self.clarify)
        g.add_node("route", self.route)
        g.add_node("split", self.split)
        g.add_node("delegate", self.delegate, input_schema=DelegateInput)  # type: ignore[arg-type]
        g.add_node("check_complete", self.check_complete)
        g.add_node("synthesize", self.synthesize)
        g.add_node("answer_directly", self.answer_directly)
        g.add_edge(START, "discover")
        g.add_edge("discover", "triage")
        g.add_conditional_edges("triage", self.after_triage, ["clarify", "route"])
        g.add_edge("clarify", "route")
        g.add_conditional_edges("route", self.after_route, ["split", "answer_directly", "delegate"])
        g.add_conditional_edges("split", self.after_split, ["delegate"])
        g.add_edge("delegate", "check_complete")
        g.add_conditional_edges("check_complete", self.after_check, ["route", "synthesize"])
        g.add_edge("synthesize", END)
        g.add_edge("answer_directly", END)
        return g.compile(checkpointer=checkpointer)


def build_graph(checkpointer: Any = None, **kw: Any) -> Any:
    return Supervisor(**kw).build(checkpointer)


__all__ = ["Specialist", "Supervisor", "build_graph", "merge_dicts"]
