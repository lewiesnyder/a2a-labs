"""Against the running compose profile (`make e2e LAB=lab11`)."""

from __future__ import annotations

import asyncio
import time

import pytest
from a2a.types import TaskPushNotificationConfig, TaskState
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.hitl import Answer
from lab11_hitl.clients import answer, inbox, inbox_respond, open_questions

pytestmark = pytest.mark.compose
ORCH = "lab11-orchestrator"
TRIP = "Book a trip to Lisbon for 4 nights"
INPUT, DONE = TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_COMPLETED


async def test_parallel_questions_are_relayed_and_routed() -> None:
    async with await connect(ORCH, streaming=False, timeout=120) as c:
        res = await send_text(c, TRIP)
        assert res.state == INPUT
        q = {x.agent.removeprefix("lab11-"): x.id for x in open_questions(res.task)}
        assert set(q) == {"flights", "hotels"}
        pay = await answer(
            c, res, {q["flights"]: Answer(choice="aisle"), q["hotels"]: Answer(choice="palace")}
        )
        (pq,) = open_questions(pay.task)
        done = await answer(c, pay, {pq.id: Answer(decision="approve")})
    assert done.state == DONE and "Paid $1460" in done.text


async def test_consent_is_relayed_as_auth_required() -> None:
    async with await connect(ORCH, streaming=False, timeout=120) as c:
        res = await send_text(c, TRIP + ", aisle, hostel, check my documents")
        assert res.state == TaskState.TASK_STATE_AUTH_REQUIRED
        (q,) = open_questions(res.task)
        done = await answer(c, res, {q.id: Answer(decision="approve")})
    assert done.state == DONE


async def test_async_hitl_through_the_inbox() -> None:
    hook = TaskPushNotificationConfig(
        url="http://lab11-inbox:11106/webhooks/a2a", token="lab11-inbox-token"
    )
    async with await connect(ORCH, streaming=False, push_config=hook) as c:
        task_id = (await send_text(c, TRIP + ", aisle, palace", return_immediately=True)).task_id
    deadline = time.monotonic() + 30
    mine: list[dict] = []
    while time.monotonic() < deadline and not mine:
        mine = [i for i in inbox(status="open") if i["task_id"] == task_id]
        await asyncio.sleep(0.5)
    assert len(mine) == 1 and mine[0]["agent"] == "lab11-payments"
    out = inbox_respond(mine[0]["id"], {"text": "yes, go ahead", "by": "integration test"})
    assert out.status_code == 200 and out.json()["task_state"] == "COMPLETED"
