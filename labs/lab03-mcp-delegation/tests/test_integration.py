"""Integration tests against the running ``lab03`` profile (``make e2e LAB=lab03``).

Exact answers are asserted in fake mode (``LLM_PROVIDER=fake``, CI); with real models only the
protocol-level outcome is, since a small LLM may skip or rephrase tool calls.
"""

from __future__ import annotations

import json
import os

import pytest
from a2a.types import TaskState
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.config import endpoint
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

pytestmark = pytest.mark.compose
FAKE = os.environ.get("LLM_PROVIDER") == "fake"
QUESTION = "How many feet in 3.2 km, and what time is it in Tokyo when it's 9am in Chicago?"
A2A_SERVICES = [
    "lab03-strands-specialist",
    "lab03-adk-specialist",
    "lab03-langgraph-mcp-consumer",
    "lab03-adk-mcp-consumer",
]


@pytest.fixture(scope="module", autouse=True)
async def _services_up() -> None:
    await wait_for_cards(A2A_SERVICES, timeout=120)


async def test_tools_mcp_lists_three_tools_and_rejects_unsafe_input() -> None:
    async with (
        streamablehttp_client(endpoint("lab03-tools-mcp")) as (r, w, _),
        ClientSession(r, w) as s,
    ):
        await s.initialize()
        assert sorted(t.name for t in (await s.list_tools()).tools) == [
            "calculate",
            "convert_units",
            "lookup_timezone",
        ]
        assert (await s.call_tool("calculate", {"expression": "__import__('os')"})).isError


async def test_bridge_generates_one_ask_tool_per_configured_agent() -> None:
    async with (
        streamablehttp_client(endpoint("lab03-mcp-a2a-bridge")) as (r, w, _),
        ClientSession(r, w) as s,
    ):
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        assert {"ask_lab03_strands_specialist", "ask_lab03_adk_specialist"} <= names
        card = json.loads(
            (await s.read_resource("a2a://cards/lab03_adk_specialist")).contents[0].text
        )
        assert card["name"] == "Travel Clock"


@pytest.mark.skipif(not FAKE, reason="the exact tool results need scripted models")
async def test_specialists_use_mcp_tools_inside() -> None:
    async with await connect("lab03-strands-specialist", streaming=False) as c:
        r = await send_text(c, "How many feet in 3.2 km, and what is 12% of that?")
        assert "10498.6877" in r.text
    async with await connect("lab03-adk-specialist", streaming=False) as c:
        r = await send_text(c, "What time is it in Tokyo when it is 9am in Chicago?")
        assert "Asia/Tokyo" in r.text and "America/Chicago" in r.text


@pytest.mark.parametrize("consumer", ["lab03-langgraph-mcp-consumer", "lab03-adk-mcp-consumer"])
async def test_consumers_answer_the_composite_question(consumer: str) -> None:
    async with await connect(consumer, streaming=False) as c:
        r = await send_text(c, QUESTION)
        assert r.state == TaskState.TASK_STATE_COMPLETED and r.text
        if FAKE:
            assert "10498.6877" in r.text and "Asia/Tokyo" in r.text, r.text
