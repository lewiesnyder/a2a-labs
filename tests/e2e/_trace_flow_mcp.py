"""Notebook-like flow for the MCP trace test (own process: OTel's provider is process-global).

argv[1] = ``consumer`` (A2A call to the LangGraph MCP consumer) or ``strands`` (in-process Strands
consumer). Prints the trace id on the last line.
"""

from __future__ import annotations

import asyncio
import sys

from a2a_labs_common import display
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.telemetry import init_telemetry

QUESTION = "How many feet in 3.2 km, and what time is it in Tokyo when it's 9am in Chicago?"


async def main(mode: str) -> None:
    tel = init_telemetry("notebook-e2e-mcp", framework="notebook", lab_id="lab03")
    with display.cell_span("e2e mcp trace") as span:
        if mode == "consumer":
            async with await connect("lab03-langgraph-mcp-consumer", streaming=False) as c:
                await send_text(c, QUESTION)
        else:
            from lab03_mcp.agents.consumers import build_strands_consumer

            agent, _client = build_strands_consumer()
            await agent.invoke_async(QUESTION)
        trace_id = f"{span.get_span_context().trace_id:032x}"
    tel.flush()
    print(trace_id)


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "consumer"))
