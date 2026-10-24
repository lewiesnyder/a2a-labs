"""One tiny agent per squad, built with the squad's framework, wrapping a deterministic handler."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from a2a_labs_common.a2a.langgraph import message_text
from a2a_labs_common.models import get_adk_model, get_strands_model
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from lab09_agp.squads import HANDLERS, SQUADS

INSTRUCTION = (
    "You are the {name} squad. Call the tool exactly once with the user's request, unchanged, "
    "then reply with the tool's result and nothing else."
)


def _tool(service: str) -> Callable[[str], str]:
    handler = HANDLERS[service]
    name = "handle_" + service.removeprefix("lab09-squad-")

    def tool(request: str) -> str:
        """Handle the squad's request.

        Args:
            request: The user's request, unchanged.
        """
        return handler(request)

    tool.__name__ = name
    return tool


def build_adk(service: str) -> Any:
    from google.adk.agents import Agent

    squad = SQUADS[service]
    return Agent(
        name=service.replace("-", "_"),
        model=get_adk_model("quick"),
        description=squad.description,
        instruction=INSTRUCTION.format(name=squad.name),
        tools=[_tool(service)],
    )


def build_strands(service: str) -> Callable[[str], Any]:
    from strands import Agent, tool

    squad = SQUADS[service]
    wrapped = tool(_tool(service))

    def factory(context_id: str = "") -> Any:
        return Agent(
            name=service,
            model=get_strands_model("quick"),
            system_prompt=INSTRUCTION.format(name=squad.name),
            tools=[wrapped],
            callback_handler=None,
        )

    return factory


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]


def build_langgraph(service: str) -> Any:
    """Deterministic one-node graph (no LLM): the framework is the point, not the model."""
    handler = HANDLERS[service]

    def run(state: State) -> dict[str, Any]:
        users = [m for m in state["messages"] if isinstance(m, HumanMessage)]
        return {"messages": [AIMessage(handler(message_text(users[-1]) if users else ""))]}

    g = StateGraph(State)
    g.add_node("answer", run)
    g.add_edge(START, "answer")
    g.add_edge("answer", END)
    return g.compile(checkpointer=InMemorySaver())
