"""The A2A->MCP bridge against a scripted fake A2A agent (what survives, what is lost)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from a2a_labs_common.a2a import cards
from a2a_labs_common.a2a.server import build_a2a_app
from lab03_mcp.servers import mcp_a2a_bridge

AGENT = "lab03-strands-specialist"  # reuse a real service name so the tool is ask_lab03_...
TOOL = "ask_lab03_strands_specialist"


class FakeAgent(AgentExecutor):
    """ask -> INPUT_REQUIRED (then resumes), fail -> FAILED, work -> status updates + artifact,
    slow -> runs until cancelled."""

    def __init__(self) -> None:
        self.cancel_flags: dict[str, asyncio.Event] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input().strip()
        task = context.current_task or new_task_from_user_message(context.message)  # type: ignore[arg-type]
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        u = TaskUpdater(event_queue, task.id, task.context_id)
        if (
            context.current_task
            and context.current_task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        ):
            await u.add_artifact([new_text_part(f"you chose {text}")], name="answer")
            await u.complete()
            return
        cmd = text.split(" ", 1)[0]
        await u.start_work()
        if cmd == "ask":
            await u.requires_input(u.new_agent_message([new_text_part("Which colour?")]))
        elif cmd == "fail":
            await u.failed(u.new_agent_message([new_text_part("kaput")]))
        elif cmd == "slow":
            flag = self.cancel_flags.setdefault(task.id, asyncio.Event())
            try:
                await asyncio.wait_for(flag.wait(), timeout=30)
            except TimeoutError:
                await u.complete()
        else:  # work
            for i in (1, 2):
                await u.update_status(
                    TaskState.TASK_STATE_WORKING, u.new_agent_message([new_text_part(f"step {i}")])
                )
            await u.add_artifact([new_text_part(f"done: {text}")], name="answer")
            await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        if context.task_id in self.cancel_flags:
            self.cancel_flags[context.task_id].set()
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


@pytest.fixture
async def bridge_url(monkeypatch, serve, pick_port) -> str:  # type: ignore[no-untyped-def]
    """A fake A2A agent (under a real service name) behind a live bridge; returns the MCP URL."""
    agent_port = pick_port()
    monkeypatch.setenv("LAB03_STRANDS_SPECIALIST_URL", f"http://127.0.0.1:{agent_port}")
    monkeypatch.setenv("LAB03_STRANDS_SPECIALIST_PORT", str(agent_port))
    card = cards.build_card(
        service=AGENT,
        name="Fake Specialist",
        description="A scripted agent for bridge tests.",
        skills=[
            cards.skill("fake", "Fake skill", "Does scripted things.", ["test"], ["work hard"])
        ],
    )
    await serve(build_a2a_app(card=card, executor=FakeAgent()), agent_port)
    bridge = mcp_a2a_bridge.build_server(mcp_a2a_bridge.resolve_agents([AGENT], wait_s=5))
    live = await serve(bridge.streamable_http_app())
    return f"http://127.0.0.1:{live.port}/mcp"


async def call(s: Any, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await s.call_tool(tool, args)
    assert not result.isError, result.content
    return json.loads(result.content[0].text)  # type: ignore[no-any-return]


async def test_one_ask_tool_per_agent_with_a_card_derived_description(bridge_url, session) -> None:  # type: ignore[no-untyped-def]
    async with session(bridge_url) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert set(tools) == {TOOL, "list_agents", "send_message", "get_task", "cancel_task"}
        desc = tools[TOOL].description
        assert "Fake Specialist" in desc and "A scripted agent for bridge tests." in desc
        assert "work hard" in desc  # the card's skill example
        assert "input_required" in desc  # how to continue is part of the tool contract
        agents = await call(s, "list_agents", {})  # a list result arrives as one item per element
        assert agents["tool"] == TOOL and agents["name"] == "Fake Specialist"


async def test_completed_maps_text_artifacts_and_ids(bridge_url, session) -> None:  # type: ignore[no-untyped-def]
    async with session(bridge_url) as s:
        r = await call(s, TOOL, {"request": "work hello"})
        assert r["status"] == "completed" and r["text"] == "done: work hello"
        assert r["task_id"] and r["context_id"]  # ids survive so the client CAN continue or poll
        assert r["artifacts"] == [{"name": "answer", "text": "done: work hello"}]


async def test_input_required_is_reported_and_the_same_task_continues(bridge_url, session) -> None:  # type: ignore[no-untyped-def]
    async with session(bridge_url) as s:
        first = await call(s, TOOL, {"request": "ask"})
        assert first["status"] == "input_required" and first["text"] == "Which colour?"
        second = await call(
            s,
            TOOL,
            {"request": "green", "task_id": first["task_id"], "context_id": first["context_id"]},
        )
        assert second["status"] == "completed" and second["text"] == "you chose green"
        assert second["task_id"] == first["task_id"]


async def test_failed_maps_to_status_and_message(bridge_url, session) -> None:  # type: ignore[no-untyped-def]
    async with session(bridge_url) as s:
        r = await call(s, TOOL, {"request": "fail now"})
        assert r["status"] == "failed" and r["text"] == "kaput"


async def test_unknown_agent_is_an_error_result_not_an_exception(bridge_url, session) -> None:  # type: ignore[no-untyped-def]
    async with session(bridge_url) as s:
        r = await call(s, "send_message", {"agent": "nobody", "request": "hi"})
        assert r["status"] == "error" and "unknown agent" in r["text"]


async def test_long_task_needs_return_immediately_get_task_and_cancel(bridge_url, session) -> None:  # type: ignore[no-untyped-def]
    """What MCP loses: a blocking call hides the task. The extra tools win it back."""
    async with session(bridge_url) as s:
        started = await call(
            s, "send_message", {"agent": AGENT, "request": "slow", "return_immediately": True}
        )
        assert started["status"] in ("submitted", "working") and started["task_id"]
        state = await call(s, "get_task", {"agent": AGENT, "task_id": started["task_id"]})
        assert state["status"] == "working"
        canceled = await call(s, "cancel_task", {"agent": AGENT, "task_id": started["task_id"]})
        assert canceled["status"] == "canceled"


async def test_card_is_an_mcp_resource(bridge_url, session) -> None:  # type: ignore[no-untyped-def]
    async with session(bridge_url) as s:
        res = await s.read_resource("a2a://cards/lab03_strands_specialist")
        card = json.loads(res.contents[0].text)
        assert card["name"] == "Fake Specialist" and card["skills"][0]["id"] == "fake"


async def test_status_updates_are_forwarded_as_mcp_log_messages(bridge_url, session) -> None:  # type: ignore[no-untyped-def]
    logs: list[str] = []

    async def on_log(params: Any) -> None:
        logs.append(str(params.data))

    async with session(bridge_url, logging_callback=on_log) as s:
        await call(s, TOOL, {"request": "work hi"})
        await asyncio.sleep(0.2)
    assert any("step 1" in m for m in logs) and any("step 2" in m for m in logs), logs
