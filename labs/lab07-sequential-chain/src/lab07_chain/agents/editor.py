"""The editor graph: edit -> review. LangGraph on the quick model.

``final`` is the edited text; ``review`` is {issues[], summary}. The review falls back to
deterministic checks when the small model does not return JSON, so the chain never depends on it.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from a2a_labs_common.a2a.langgraph import message_text
from a2a_labs_common.models import get_langchain_model
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    draft: str
    final: str
    review: dict[str, Any]


def edit(state: State) -> dict[str, Any]:
    draft = message_text(state["messages"][-1])
    prompt = (
        "Edit this draft for clarity. Keep the markdown. Reply with the text only.\n"
        f"Draft:\n{draft}"
    )
    final = message_text(get_langchain_model("quick").invoke([HumanMessage(prompt)])).strip()
    return {"draft": draft, "final": final or draft}


def checks(text: str) -> list[str]:
    """Deterministic review: the same rules every time."""
    issues = []
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if any(len(s.split()) > 30 for s in sentences):
        issues.append("Some sentences are longer than 30 words.")
    if not re.search(r"^#{1,3} ", text, re.M):
        issues.append("The article has no headings.")
    if len(text.split()) < 40:
        issues.append("The article is very short.")
    return issues


def review(state: State) -> dict[str, Any]:
    text = state["final"]
    prompt = (
        'Review this article. Reply ONLY JSON: {"issues": [str], "summary": str}.\n'
        f"Article:\n{text}"
    )
    raw = message_text(get_langchain_model("quick").invoke([HumanMessage(prompt)]))
    try:
        data = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
        issues = [str(i) for i in data.get("issues", [])]
        summary = str(data.get("summary", ""))
    except ValueError:
        issues, summary = [], ""
    issues = issues or checks(text)
    return {"review": {"issues": issues, "summary": summary or f"{len(issues)} issue(s) found."}}


def build_graph() -> Any:
    g = StateGraph(State)
    g.add_node("edit", edit)
    g.add_node("review", review)
    g.add_edge(START, "edit")
    g.add_edge("edit", "review")
    g.add_edge("review", END)
    return g.compile(checkpointer=InMemorySaver())
