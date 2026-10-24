"""LangGraph agent: LangChain 1.x ``create_agent`` with an in-memory checkpointer."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_langchain_model

from lab01_direct.agents.common import INSTRUCTION, local_time_data


def build_graph() -> Any:
    from langchain.agents import create_agent
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import InMemorySaver

    @tool
    def local_time(city: str) -> str:
        """Return the current local time for a city (for example "Lisbon")."""
        return local_time_data(city)

    return create_agent(
        get_langchain_model("quick"),
        tools=[local_time],
        system_prompt=INSTRUCTION,
        # A2A context_id -> thread_id: the checkpointer is what gives multi-turn memory.
        checkpointer=InMemorySaver(),
        name="trip_tip_langgraph",
    )
