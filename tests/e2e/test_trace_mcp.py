"""One request, one trace across A2A and MCP hops (spec 05 §8.1, lab03 §5).

Needs the ``lab03`` profile in fake mode: ``LLM_PROVIDER=fake make up LAB=lab03``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from a2a_labs_common.testing import fetch_trace

pytestmark = pytest.mark.compose
FAKE = os.environ.get("LLM_PROVIDER") == "fake"


async def run_flow(mode: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("_trace_flow_mcp.py")),
        mode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "LLM_PROVIDER": "fake"},
    )
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()[-800:]
    return out.decode().strip().splitlines()[-1]


async def test_consumer_trace_reaches_bridge_specialist_and_tools_server() -> None:
    trace_id = await run_flow("consumer")
    want = {
        "mcp.call_tool ask_lab03_strands_specialist",
        "mcp.call_tool convert_units",
        "a2a.server.execute",
    }
    spans = await fetch_trace(trace_id, want, timeout=60)
    services = {svc for svc, _ in spans}
    names = {n for _, n in spans}
    assert want <= names, f"missing spans: {want - names}\nservices: {services}"
    assert {
        "notebook-e2e-mcp",
        "lab03-langgraph-mcp-consumer",
        "lab03-mcp-a2a-bridge",
        "lab03-strands-specialist",
        "lab03-tools-mcp",
    } <= services, services


async def test_in_process_strands_consumer_trace_is_connected_too() -> None:
    """Strands' MCPClient runs its own thread; the trace must survive that hop as well."""
    trace_id = await run_flow("strands")
    want = {"mcp.call_tool ask_lab03_strands_specialist", "mcp.call_tool convert_units"}
    spans = await fetch_trace(trace_id, want, timeout=60)
    services = {svc for svc, _ in spans}
    assert want <= {n for _, n in spans}, f"missing spans; services: {services}"
    assert {"notebook-e2e-mcp", "lab03-mcp-a2a-bridge", "lab03-tools-mcp"} <= services, services
