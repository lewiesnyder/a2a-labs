"""LangChain tool that calls a remote A2A agent (spec lab02 §3.3).

# CUSTOM: thin wrapper. No official LangChain A2A tool exists, so this uses only a2a-sdk calls
# (through ``RemoteAgent``). The tool's name and description come from the remote Agent Card.
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from a2a_labs_common.a2a.remote import RemoteAgent, card_description, fetch_card_sync


class A2AToolInput(BaseModel):
    request: str = Field(description="What to ask the remote agent, in plain language.")
    task_id: str | None = Field(
        default=None,
        description="Only to continue a task that replied NEEDS_INPUT; copy it from that reply.",
    )


def a2a_agent_as_langchain_tool(
    service: str, *, name: str | None = None, description: str | None = None
) -> StructuredTool:
    """Builds a tool whose description is generated from the remote card's skills.

    Input is ``{"request": str, "task_id": str | None}``. ``RunnableConfig['configurable']
    ['thread_id']`` selects a stable remote context (uuid5 of thread and service). Returns the
    artifact text, ``NEEDS_INPUT: <question> [task_id=...]`` on INPUT_REQUIRED, or ``ERROR: ...``.
    The next call continues the same remote task, with or without an explicit ``task_id``.
    """
    remote = RemoteAgent(service)
    card = fetch_card_sync(service)  # at build time, so the tool schema reflects the live card
    tool_name = name or "ask_" + re.sub(r"[^a-z0-9]+", "_", card.name.lower()).strip("_")

    # The annotation must be exactly `RunnableConfig`: LangChain injects the run config only for
    # that type, and `RunnableConfig | None` silently disables injection, which would put every
    # conversation in one shared "default" remote context.
    async def run(request: str, config: RunnableConfig, task_id: str | None = None) -> str:
        thread = (config.get("configurable") or {}).get("thread_id", "default")
        return (await remote.ask(request, scope=str(thread), task_id=task_id)).for_llm()

    return StructuredTool.from_function(
        coroutine=run,
        name=tool_name,
        description=description or card_description(card),
        args_schema=A2AToolInput,
    )
