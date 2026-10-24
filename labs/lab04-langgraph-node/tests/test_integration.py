"""Integration tests against the running ``lab04`` profile (``make e2e LAB=lab04``).

Exact paths are asserted in fake mode (``LLM_PROVIDER=fake DECISION_PROVIDER=fake``: the scripted
decision score is 2, so every plan needs approval). With real models only the protocol-level
outcome is, because Nimble decides whether approval is needed.
"""

from __future__ import annotations

import os

import pytest
from a2a.types import ListTasksRequest, TaskState
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import connect, send_text, stream_text, wait_for_cards

pytestmark = pytest.mark.compose
FAKE = os.environ.get("DECISION_PROVIDER") == "fake"
PLANNER, CONCIERGE = "lab04-langgraph-travel", "lab04-langgraph-orchestrator"
FULL = "Plan a trip from Chicago to Lisbon, 12-16 May, budget $2000, 2 travelers"
S = TaskState


@pytest.fixture(scope="module", autouse=True)
async def _services_up() -> None:
    await wait_for_cards([PLANNER, CONCIERGE], timeout=120)


async def test_planner_streams_node_events_and_ends_in_an_itinerary_or_an_approval() -> None:
    async with await connect(PLANNER, streaming=True) as c:
        evs = [e async for e in stream_text(c, FULL)]
        texts = [
            e.status_update.status.message.parts[0].text
            for e in evs
            if events.kind(e) == "status_update" and e.status_update.status.HasField("message")
        ]
        assert "▶ node: parse_request" in texts and "▶ node: risk_gate" in texts
        assert events.state_of(evs[-1]) in ("COMPLETED", "INPUT_REQUIRED")


async def test_missing_fields_interrupt_then_resume_on_the_same_task() -> None:
    async with await connect(PLANNER, streaming=False) as c:
        first = await send_text(c, "Plan a trip to Lisbon")
        assert first.state == S.TASK_STATE_INPUT_REQUIRED
        nxt = await send_text(
            c,
            FULL.replace("Plan a trip", "from Chicago"),
            task_id=first.task_id,
            context_id=first.context_id,
        )
        assert nxt.task_id == first.task_id
        assert nxt.state in (S.TASK_STATE_COMPLETED, S.TASK_STATE_INPUT_REQUIRED)


@pytest.mark.skipif(not FAKE, reason="the exact approval path needs the scripted decision model")
async def test_nested_interrupt_propagates_through_the_concierge() -> None:
    async with await connect(PLANNER, streaming=False) as p:
        before = len((await p.client.list_tasks(ListTasksRequest())).tasks)
    async with await connect(CONCIERGE, streaming=False) as c:
        first = await send_text(c, FULL)
        assert first.state == S.TASK_STATE_INPUT_REQUIRED
        assert "Approve?" in first.task.status.message.parts[0].text
        done = await send_text(c, "yes", task_id=first.task_id, context_id=first.context_id)
        assert done.task_id == first.task_id and done.state == S.TASK_STATE_COMPLETED
        assert "Budget check" in done.text
    async with await connect(PLANNER, streaming=False) as p:
        tasks = (await p.client.list_tasks(ListTasksRequest())).tasks
        assert len(tasks) == before + 1  # the whole nested exchange used ONE remote task
        assert any(t.status.state == S.TASK_STATE_COMPLETED for t in tasks)
