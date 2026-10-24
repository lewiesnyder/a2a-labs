"""Behaviour of each Lab 01 server: Message vs Task, streaming, multi-turn memory (fake models)."""

from __future__ import annotations

import pytest
from a2a.types import GetTaskRequest, TaskState
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import send_text, stream_text
from a2a_labs_common.testing import FAKE_CALLS

FRAMEWORKS = ["adk", "strands", "langgraph"]


async def test_sdk_echo_always_answers_with_a_single_message(server) -> None:  # type: ignore[no-untyped-def]
    async with server("sdk-echo", streaming=True) as c:
        evs = [e async for e in stream_text(c, "hello")]
        assert [events.kind(e) for e in evs] == ["message"]  # one event, then the stream closes
        assert "[echo] hello" in events.event_text(evs[0])
    async with server("sdk-echo", streaming=False) as c:
        r = await send_text(c, "hi")
        assert r.kind == "message" and r.task is None and r.text == "[echo] hi"


@pytest.mark.parametrize("name", FRAMEWORKS)
async def test_framework_agents_answer_with_a_task_and_an_artifact(server, name) -> None:  # type: ignore[no-untyped-def]
    async with server(name, streaming=False) as c:
        r = await send_text(c, "Give me a tip for Lisbon")
        assert r.kind == "task" and r.state == TaskState.TASK_STATE_COMPLETED
        assert len(r.task.artifacts) >= 1 and r.text  # results live in Artifacts


@pytest.mark.parametrize("name", FRAMEWORKS)
async def test_a_reply_is_one_artifact_not_duplicated(server, name) -> None:  # type: ignore[no-untyped-def]
    """Risk register: ADK #7247 produced duplicate replies; force_new_version avoids it."""
    async with server(name, streaming=False) as c:
        r = await send_text(c, "Give me a tip for Paris")
        assert len(r.task.artifacts) == 1
        text = r.text
        assert text and text.count("Give me a tip for Paris") == 1


@pytest.mark.parametrize("name", FRAMEWORKS)
async def test_streaming_yields_task_first_and_at_least_two_events(server, name) -> None:  # type: ignore[no-untyped-def]
    async with server(name, streaming=True) as c:
        evs = [e async for e in stream_text(c, "Give me a tip for Tokyo")]
        assert len(evs) >= 2 and events.kind(evs[0]) == "task"
        assert events.state_of(evs[-1]) == "COMPLETED"


@pytest.mark.parametrize("name", FRAMEWORKS)
async def test_multi_turn_memory_follows_context_id(server, name) -> None:  # type: ignore[no-untyped-def]
    async with server(name, streaming=False) as c:
        first = await send_text(c, "My name is Ada; tip for Paris")
        FAKE_CALLS.clear()
        await send_text(c, "What is my name?", context_id=first.context_id)
        assert "Ada" in FAKE_CALLS[-1]["conversation"]  # turn 2 saw turn 1
        FAKE_CALLS.clear()
        await send_text(c, "What is my name?")  # a brand-new context
        assert "Ada" not in FAKE_CALLS[-1]["conversation"]


@pytest.mark.parametrize("name", FRAMEWORKS)
async def test_get_task_afterwards_has_bounded_history(server, name) -> None:  # type: ignore[no-untyped-def]
    async with server(name, streaming=False) as c:
        r = await send_text(c, "Give me a tip for Lisbon")
        t = await c.client.get_task(GetTaskRequest(id=r.task_id, history_length=5))
        assert 1 <= len(t.history) <= 5
