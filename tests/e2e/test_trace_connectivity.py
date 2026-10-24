"""One notebook cell -> one connected trace across every service it touches (spec 05 §8.1).

Needs the ``lab00`` compose profile and lgtm (``make up LAB=lab00``).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from a2a_labs_common.testing import fetch_trace

pytestmark = pytest.mark.compose


async def test_notebook_cell_trace_spans_client_and_server() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("_trace_flow.py")),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    assert proc.returncode == 0, err.decode()[-800:]
    trace_id = out.decode().strip().splitlines()[-1]

    want = {"a2a.client SendStreamingMessage", "a2a.client SubscribeToTask", "a2a.server.execute"}
    spans = await fetch_trace(trace_id, want)
    names = {s for _, s in spans}
    services = {svc for svc, _ in spans}
    assert want <= names, f"missing spans: {want - names}"
    assert {"notebook-e2e", "lab00-protocol-agent"} <= services, services
    assert "notebook.cell e2e trace connectivity" in names
