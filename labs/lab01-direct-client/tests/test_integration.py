"""Integration tests against the running ``lab01`` profile (``make e2e LAB=lab01``).

Pass in fake mode (``LLM_PROVIDER=fake``, used by CI) and with real models; the memory check can
only be asserted in fake mode, where the reply states how many user turns the model saw.
"""

from __future__ import annotations

import os
import re

import pytest
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.cards import validate_card
from a2a_labs_common.a2a.client import connect, send_text, stream_text, wait_for_cards

pytestmark = pytest.mark.compose
ECHO = "lab01-sdk-echo"
FRAMEWORK_AGENTS = ["lab01-adk-agent", "lab01-strands-agent", "lab01-langgraph-agent"]
FAKE = os.environ.get("LLM_PROVIDER") == "fake"


@pytest.fixture(scope="module", autouse=True)
async def _services_up() -> None:
    await wait_for_cards([ECHO, *FRAMEWORK_AGENTS], timeout=90)


@pytest.mark.parametrize("name", [ECHO, *FRAMEWORK_AGENTS])
async def test_cards_are_valid(name: str) -> None:
    async with await connect(name) as c:
        assert validate_card(c.card) == []


async def test_echo_replies_with_a_message_and_closes_the_stream() -> None:
    async with await connect(ECHO, streaming=True) as c:
        evs = [e async for e in stream_text(c, "hello")]
        assert [events.kind(e) for e in evs] == ["message"]
    async with await connect(ECHO, streaming=False) as c:
        assert (await send_text(c, "hi")).kind == "message"


@pytest.mark.parametrize("name", FRAMEWORK_AGENTS)
async def test_framework_agents_return_a_task_with_an_artifact(name: str) -> None:
    async with await connect(name, streaming=False) as c:
        r = await send_text(c, "Give me a tip for Lisbon")
        assert r.kind == "task" and len(r.task.artifacts) >= 1 and r.text


@pytest.mark.parametrize("name", FRAMEWORK_AGENTS)
async def test_streaming_has_at_least_two_events_task_first(name: str) -> None:
    async with await connect(name, streaming=True) as c:
        evs = [e async for e in stream_text(c, "Give me a tip for Tokyo")]
        assert len(evs) >= 2 and events.kind(evs[0]) == "task"


@pytest.mark.skipif(not FAKE, reason="memory is only deterministic with fake models")
@pytest.mark.parametrize("name", FRAMEWORK_AGENTS)
async def test_second_turn_in_same_context_sees_the_first(name: str) -> None:
    async with await connect(name, streaming=False) as c:
        first = await send_text(c, "My name is Ada; tip for Paris")
        second = await send_text(c, "What is my name?", context_id=first.context_id)
        assert re.search(r"turn=2", second.text), second.text
        fresh = await send_text(c, "What is my name?")
        assert re.search(r"turn=1", fresh.text), fresh.text
