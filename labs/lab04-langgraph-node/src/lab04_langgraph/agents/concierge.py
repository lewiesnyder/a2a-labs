"""The Concierge graph: a LOCAL graph that contains a REMOTE A2A agent as a node.

understand -> plan (RemoteA2ANode -> Trip Planner) -> budget_check -> END

Nesting is A2A -> LangGraph -> A2A -> LangGraph. When the remote planner interrupts, the node
calls a local ``interrupt()``, so the pause surfaces to the end client too.
Implements: spec lab04 §2.2.
"""

from __future__ import annotations

import operator
import re
from typing import Annotated, Any

from a2a_labs_common.a2a.langgraph import RemoteA2ANode, message_text
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

TRAVEL = "lab04-langgraph-travel"


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    request_text: str
    plan: str | None
    progress: Annotated[list[str], operator.add]
    remote_tasks: dict[str, Any]
    error: str | None
    verdict: str


def understand(state: State) -> dict[str, Any]:
    users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    return {"request_text": message_text(users[-1]) if users else ""}


def budget_check(state: State) -> dict[str, Any]:
    """A LOCAL node: parse the planner's deterministic 'Total: $X (budget $Y)' footer."""
    plan = state.get("plan") or ""
    m = re.search(r"Total: \$(\d+) \(budget \$(\d+)\)", plan)
    if not m:
        verdict = "Budget check: could not read the total."
    else:
        total, budget = int(m.group(1)), int(m.group(2))
        verdict = (
            f"Budget check: ${total} is within the ${budget} budget."
            if total <= budget
            else f"Budget check: ${total} is OVER the ${budget} budget by ${total - budget}."
        )
    return {"verdict": verdict, "messages": [AIMessage(f"{plan}\n\n{verdict}")]}


def failed(state: State) -> dict[str, Any]:
    return {"messages": [AIMessage(f"The travel planner could not help: {state.get('error')}")]}


def route_after_plan(state: State) -> str:
    return "failed" if state.get("error") else "budget_check"


def build_graph(checkpointer: Any) -> Any:
    g = StateGraph(State)
    g.add_node("understand", understand)
    g.add_node(
        "plan",
        RemoteA2ANode(TRAVEL, input_fn=lambda s: s["request_text"], output_key="plan"),
    )
    g.add_node("budget_check", budget_check)
    g.add_node("failed", failed)
    g.add_edge(START, "understand")
    g.add_edge("understand", "plan")
    g.add_conditional_edges("plan", route_after_plan, ["budget_check", "failed"])
    g.add_edge("budget_check", END)
    g.add_edge("failed", END)
    return g.compile(checkpointer=checkpointer)
