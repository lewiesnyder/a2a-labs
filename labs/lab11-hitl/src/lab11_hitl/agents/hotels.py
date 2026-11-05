"""Hotels (LangGraph): ``interrupt()`` asks which hotel; the bridge turns it into INPUT_REQUIRED.

The interrupt value carries ``question`` (the bridge's status text) and the ``hitl`` prompt (the
bridge also sends the whole dict as a data part). The resume value is the answer data part.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from a2a.helpers import new_data_part, new_text_part
from a2a_labs_common.a2a.langgraph import message_text
from a2a_labs_common.hitl import Option, Prompt
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

from lab11_hitl.data import DEFAULT_NIGHTS, HOTELS


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    nights: int
    hotel: str | None
    declined: bool
    reason: str


def parse(state: State) -> dict[str, Any]:
    users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    text = message_text(users[-1]).lower() if users else ""
    m = re.search(r"(\d+)\s*night", text)
    hotel = next((h["id"] for h in HOTELS if h["id"] in text or h["label"].lower() in text), None)
    return {"nights": int(m.group(1)) if m else DEFAULT_NIGHTS, "hotel": hotel}


def ask(state: State) -> dict[str, Any]:
    if state.get("hotel"):  # already said in the request: no human needed
        return {}
    prompt = Prompt(
        kind="choice",
        prompt=f"Which hotel for {state['nights']} nights?",
        options=[
            Option(id=h["id"], label=f"{h['label']} (${h['per_night']}/night)") for h in HOTELS
        ],
    )
    # LangGraph: interrupt() pauses the graph and returns the human's answer on resume.
    reply = interrupt({"question": prompt.prompt, "hitl": prompt.model_dump(exclude_none=True)})
    answer = (reply or {}).get("hitl", {}).get("answer", {}) if isinstance(reply, dict) else {}
    if answer.get("decision") == "reject":
        return {"declined": True, "reason": answer.get("text") or "no reason"}
    return {"hotel": answer.get("choice") or HOTELS[0]["id"]}


def book(state: State) -> dict[str, Any]:
    if state.get("declined"):
        return {"messages": [AIMessage(f"Hotel booking declined: {state.get('reason')}")]}
    hotel = next(h for h in HOTELS if h["id"] == state["hotel"])
    total = hotel["per_night"] * state["nights"]
    return {
        "messages": [AIMessage(f"{hotel['label']}, {state['nights']} nights: ${total}")],
    }


def extra(values: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    if values.get("declined"):
        return [("result", [new_data_part({"declined": True})])]
    hotel = next(h for h in HOTELS if h["id"] == values["hotel"])
    total = hotel["per_night"] * values["nights"]
    return [
        (
            "result",
            [new_text_part(hotel["label"]), new_data_part({"total": total, "hotel": hotel["id"]})],
        )
    ]


def build_graph() -> Any:
    g = StateGraph(State)
    g.add_node("parse", parse)
    g.add_node("ask", ask)
    g.add_node("book", book)
    g.add_edge(START, "parse")
    g.add_edge("parse", "ask")
    g.add_edge("ask", "book")
    g.add_edge("book", END)
    return g.compile(checkpointer=InMemorySaver())
