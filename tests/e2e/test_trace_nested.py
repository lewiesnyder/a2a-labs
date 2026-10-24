"""One trace across the A2A -> LangGraph -> A2A -> LangGraph nesting (lab04 §3 step 11).

Needs the ``lab04`` profile: ``LLM_PROVIDER=fake DECISION_PROVIDER=fake make up LAB=lab04``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from a2a_labs_common.testing import fetch_trace

pytestmark = pytest.mark.compose


async def test_nested_run_is_one_trace_with_state_change_events() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("_trace_flow_nested.py")),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "LLM_PROVIDER": "fake"},
    )
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()[-800:]
    trace_id = out.decode().strip().splitlines()[-1]
    want = {"a2a.client SendMessage", "a2a.server.execute"}
    spans = await fetch_trace(trace_id, want, timeout=60)
    services = {svc for svc, _ in spans}
    assert {
        "notebook-e2e-nested",
        "lab04-langgraph-orchestrator",
        "lab04-langgraph-travel",
    } <= services, services
    servers = [(svc, n) for svc, n in spans if n == "a2a.server.execute"]
    assert {s for s, _ in servers} >= {"lab04-langgraph-orchestrator", "lab04-langgraph-travel"}
