"""The two specialists. Each USES MCP tools internally and is exposed over A2A.

"MCP inside, A2A outside": the tools server is an implementation detail that no A2A client can
see (look at their cards: nothing mentions MCP). Implements: spec lab03 §2.
"""

from __future__ import annotations

from typing import Any

from a2a_labs_common.config import endpoint
from a2a_labs_common.models import get_adk_model, get_strands_model

TOOLS_MCP = "lab03-tools-mcp"

ENGINEER_INSTRUCTION = (
    "You are an engineering helper with two tools. Use convert_units for unit questions. Use "
    "calculate for arithmetic, passing a plain expression such as 10498.69 * 0.12. For 'X% of "
    "that', first convert, then call calculate with the CONVERTED number times X/100. Never do "
    "maths in your head. Report the tool results in one or two sentences."
)
CLOCK_INSTRUCTION = (
    "You are a travel clock. Steps: (1) call lookup_timezone once for the source city and once "
    "for the destination city. (2) hours_ahead = destination utc_offset_hours minus source "
    "utc_offset_hours (for example 9 minus -5 is 14). (3) Add hours_ahead to the given time on a "
    "24-hour clock; past 24:00 subtract 24 and say 'the next day'. Answer in one sentence."
)


def build_engineer_agent(context_id: str) -> Any:
    """Strands agent whose tools come from an ``MCPClient`` (streamable HTTP)."""
    from mcp.client.streamable_http import streamablehttp_client
    from strands import Agent
    from strands.tools.mcp import MCPClient

    url = endpoint(TOOLS_MCP)
    mcp_client = MCPClient(
        lambda: streamablehttp_client(url),
        tool_filters={"allowed": ["convert_units", "calculate"]},  # <=3 tools for a 3B model
    )
    return Agent(
        name="engineering_helper",
        description="Unit conversions and arithmetic.",
        model=get_strands_model("quick"),
        tools=[mcp_client],
        system_prompt=ENGINEER_INSTRUCTION,
        callback_handler=None,
    )


def build_clock_agent() -> Any:
    """ADK agent whose tools come from an ``McpToolset`` (streamable HTTP)."""
    from google.adk.agents import Agent
    from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

    return Agent(
        name="travel_clock",
        model=get_adk_model("quick"),
        description="Compares local times between cities.",
        instruction=CLOCK_INSTRUCTION,
        tools=[
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(url=endpoint(TOOLS_MCP)),
                tool_filter=["lookup_timezone", "calculate"],
            )
        ],
    )
