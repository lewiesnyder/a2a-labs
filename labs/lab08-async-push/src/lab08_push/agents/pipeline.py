"""The pipeline graph: extract -> transform -> validate -> load, with a sleep per node."""

from __future__ import annotations

import asyncio
import itertools
from typing import Annotated, Any

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from lab08_push.agents.data import PIPELINE, seconds


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    log: list[str]


def _node(name: str) -> Any:
    async def run(state: State) -> dict[str, Any]:
        await asyncio.sleep(seconds("NODE_SECONDS", 2))
        log = [*state.get("log", []), f"{name}: ok"]
        out: dict[str, Any] = {"log": log}
        if name == PIPELINE[-1]:
            out["messages"] = [AIMessage("Pipeline finished: " + "; ".join(log))]
        return out

    return run


def build_graph() -> Any:
    g = StateGraph(State)
    for name in PIPELINE:
        g.add_node(name, _node(name))
    g.add_edge(START, PIPELINE[0])
    for a, b in itertools.pairwise(PIPELINE):
        g.add_edge(a, b)
    g.add_edge(PIPELINE[-1], END)
    return g.compile(checkpointer=InMemorySaver())
