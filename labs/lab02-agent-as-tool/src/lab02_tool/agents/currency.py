"""Currency specialist: a LangGraph graph that asks the user when the target is missing.

Opaque to controllers: they see only its Agent Card and the artifacts it returns.
Implements: spec lab02 §2 (INPUT_REQUIRED via ``interrupt()``).
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from a2a_labs_common.models import get_langchain_model
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

from lab02_tool.agents.data import PER_USD, RATE_DATE, get_exchange_rate

_CODE = re.compile(r"\b([A-Za-z]{3})\b")
_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)")


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    amount: float
    source: str
    target: str
    fact: str


def _codes(text: str) -> list[str]:
    return [c.upper() for c in _CODE.findall(text) if c.upper() in PER_USD]


def extract(state: State) -> dict[str, Any]:
    """Deterministic parsing (no LLM), so INPUT_REQUIRED never depends on a small model."""
    text = str(state["messages"][-1].content)
    out: dict[str, Any] = {}
    if m := _AMOUNT.search(text):
        out["amount"] = float(m.group(1).replace(",", "."))
    codes = _codes(text)
    if codes:
        out["source"] = codes[0]
    if len(codes) > 1:
        out["target"] = codes[1]
    return out


def ask_missing(state: State) -> dict[str, Any]:
    """Pause with ``interrupt()`` until we know the amount and both currencies."""
    amount, source, target = state.get("amount"), state.get("source"), state.get("target")
    while amount is None:
        reply = str(interrupt("How much do you want to convert?"))
        amount = float(m.group(1).replace(",", ".")) if (m := _AMOUNT.search(reply)) else None
    while source is None:
        source = next(iter(_codes(str(interrupt("Convert from which currency?")))), None)
    while target is None:
        # The reply may restate the whole request ("100 USD to EUR"): pick the code that is not
        # the source, otherwise a restating controller (or user) converts USD to USD.
        reply_codes = _codes(str(interrupt("Convert to which currency?")))
        target = next((c for c in reply_codes if c != source), None)
    return {"amount": amount, "source": source, "target": target}


def convert(state: State) -> dict[str, Any]:
    # `get_exchange_rate` is this agent's private tool; callers never see it.
    rate = get_exchange_rate(state["source"], state["target"], RATE_DATE)
    total = state["amount"] * rate
    fact = (
        f"{state['amount']:g} {state['source']} = {total:,.2f} {state['target']} "
        f"(rate {rate} on {RATE_DATE})"
    )
    return {"fact": fact}


def answer(state: State) -> dict[str, Any]:
    llm = get_langchain_model("quick")
    prompt = f"Rewrite this result as one short sentence, keeping every number: {state['fact']}"
    reply = llm.invoke([HumanMessage(prompt)])
    return {"messages": [AIMessage(str(reply.content).strip())]}


def build_graph() -> Any:
    g = StateGraph(State)
    for name, fn in (
        ("extract", extract),
        ("ask_missing", ask_missing),
        ("convert", convert),
        ("answer", answer),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "extract")
    g.add_edge("extract", "ask_missing")
    g.add_edge("ask_missing", "convert")
    g.add_edge("convert", "answer")
    g.add_edge("answer", END)
    return g.compile(checkpointer=InMemorySaver())
