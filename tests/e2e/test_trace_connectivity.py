"""One notebook cell -> one connected trace across every service it touches (spec 05 §8.1).

Needs the ``lab00`` compose profile and lgtm (``make up LAB=lab00``).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.compose
TEMPO = "http://localhost:3200"


def span_index(trace_json: dict[str, Any]) -> list[tuple[str, str]]:
    """(service.name, span name) for every span in a Tempo trace."""
    out = []
    for batch in trace_json.get("batches", []):
        attrs = {a["key"]: a["value"].get("stringValue") for a in batch["resource"]["attributes"]}
        for scope in batch.get("scopeSpans", []):
            out += [(attrs.get("service.name", ""), s["name"]) for s in scope["spans"]]
    return out


async def fetch_trace(
    trace_id: str,
    want: set[str],
    timeout: float = 40,  # noqa: ASYNC109 - plain deadline, not a cancel scope
) -> list[tuple[str, str]]:
    deadline = time.monotonic() + timeout
    spans: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=10) as http:
        while time.monotonic() < deadline:
            resp = await http.get(f"{TEMPO}/api/traces/{trace_id}")
            if resp.status_code == 200:
                spans = span_index(resp.json())
                if want <= {s for _, s in spans}:
                    return spans
            await asyncio.sleep(2)
    return spans


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
