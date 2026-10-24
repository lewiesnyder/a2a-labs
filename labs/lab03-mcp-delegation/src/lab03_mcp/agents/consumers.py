"""Coordinators that see ONLY MCP tools (from the bridge) yet end up calling A2A agents.

The coordinator has no idea A2A exists: it sees ``ask_<agent>`` tools. Implements: spec lab03 §2.
"""

from __future__ import annotations

from typing import Any

from a2a_labs_common.config import endpoint
from a2a_labs_common.models import get_adk_model, get_langchain_model

BRIDGE = "lab03-mcp-a2a-bridge"
ASK_TOOLS = ["ask_lab03_strands_specialist", "ask_lab03_adk_specialist"]

INSTRUCTION = (
    "You coordinate specialist agents that are available as tools named ask_*. Split the user's "
    "question into parts, call the right ask_* tool for each part with a self-contained request, "
    "then write ONE answer with every result. If a tool returns status input_required, ask the "
    "user that question. If it returns failed or error, say so."
)


async def build_langgraph_consumer() -> Any:
    """LangGraph agent whose tools come from ``langchain-mcp-adapters`` (async, so lazily built)."""
    from langchain.agents import create_agent
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langgraph.checkpoint.memory import InMemorySaver

    client = MultiServerMCPClient(
        {"bridge": {"url": endpoint(BRIDGE), "transport": "streamable_http"}}
    )
    tools = [t for t in await client.get_tools() if t.name in ASK_TOOLS]
    return create_agent(
        get_langchain_model("reasoning"),
        tools=tools,
        system_prompt=INSTRUCTION,
        checkpointer=InMemorySaver(),
        name="mcp_consumer_langgraph",
    )


def build_adk_consumer() -> Any:
    """ADK agent whose tools come from an ``McpToolset`` pointing at the bridge."""
    from google.adk.agents import Agent
    from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

    return Agent(
        name="mcp_consumer_adk",
        model=get_adk_model("reasoning"),
        description="Coordinator that delegates through MCP tools.",
        instruction=INSTRUCTION,
        tools=[
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(url=endpoint(BRIDGE)),
                tool_filter=ASK_TOOLS,
            )
        ],
    )


def build_strands_consumer() -> tuple[Any, Any]:
    """Strands agent plus its ``MCPClient``; returned so a notebook can show the raw session."""
    from a2a_labs_common.models import get_strands_model
    from mcp.client.streamable_http import streamablehttp_client
    from strands import Agent
    from strands.tools.mcp import MCPClient

    url = endpoint(BRIDGE)
    client = MCPClient(lambda: streamablehttp_client(url), tool_filters={"allowed": ASK_TOOLS})
    agent = Agent(
        name="mcp_consumer_strands",
        model=get_strands_model("reasoning"),
        tools=[client],
        system_prompt=INSTRUCTION,
        callback_handler=None,
    )
    return agent, client
