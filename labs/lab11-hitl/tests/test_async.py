"""Asynchronous HITL: the client leaves, the inbox holds the questions, a person answers later."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from a2a.types import GetTaskRequest, TaskPushNotificationConfig, TaskState
from a2a_labs_common.a2a.client import connect, send_text
from lab11_hitl.clients import inbox, inbox_respond

DONE, REJ = TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_REJECTED
TOKEN = "lab11-inbox-token"
TRIP = "Book a trip to Lisbon for 4 nights"


async def until(check: Callable[[], Any], within: float = 15) -> Any:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        got = check()
        if got:
            return got
        await asyncio.sleep(0.1)
    raise TimeoutError("condition never held")


async def submit(desk, text: str = TRIP) -> str:
    """Fire and forget: the connection is closed before anyone answers."""
    hook = TaskPushNotificationConfig(url=f"{desk['lab11-inbox'].url}/webhooks/a2a", token=TOKEN)
    async with await connect(
        desk["lab11-orchestrator"].url, streaming=False, push_config=hook
    ) as conn:
        res = await send_text(conn, text, return_immediately=True)
    return res.task_id


def open_items(agent: str | None = None) -> list[dict]:
    rows = inbox(status="open")
    return [r for r in rows if agent is None or r["agent"] == f"lab11-{agent}"]


async def state_of(desk, task_id: str) -> TaskState:
    async with await connect(desk["lab11-orchestrator"].url, streaming=False) as c:
        return (await c.client.get_task(GetTaskRequest(id=task_id))).status.state


async def test_the_inbox_collects_the_questions_while_no_client_is_connected(desk) -> None:
    task_id = await submit(desk)
    items = await until(lambda: len(open_items()) == 2 and open_items())
    assert {i["agent"] for i in items} == {"lab11-flights", "lab11-hotels"}
    assert all(i["task_id"] == task_id and i["prompt"]["prompt"] for i in items)
    assert await state_of(desk, task_id) == TaskState.TASK_STATE_INPUT_REQUIRED


async def test_a_person_answers_later_through_the_inbox_and_the_task_completes(desk) -> None:
    task_id = await submit(desk)
    await until(lambda: len(open_items()) == 2)
    flights = open_items("flights")[0]
    hotels = open_items("hotels")[0]
    # a structured answer for one, a free-text answer (read by the decision model) not allowed for
    # a choice that names no option: the inbox refuses it with 422
    assert inbox_respond(flights["id"], {"text": "whatever"}).status_code == 422
    assert (
        inbox_respond(flights["id"], {"answer": {"choice": "window"}, "by": "ana"}).status_code
        == 200
    )
    assert inbox_respond(hotels["id"], {"text": "the palace please"}).status_code == 200
    # stage 2: a NEW question appears (the payment), pushed to the same inbox
    pay = (await until(lambda: open_items("payments")))[0]
    assert pay["prompt"]["proposal"]["amount"] == 1460
    done = inbox_respond(pay["id"], {"text": "yes, go ahead", "by": "ana"})
    assert done.status_code == 200 and done.json()["task_state"] == "COMPLETED"
    assert await state_of(desk, task_id) == DONE
    # the completion push closed whatever was left, and answered items remember who answered
    items = {i["id"]: i for i in inbox("/items")}
    assert items[flights["id"]]["answered_by"] == "ana" and not open_items()
    states = [e["state"] for e in inbox("/events") if e["task_id"] == task_id]
    assert states.count("INPUT_REQUIRED") >= 2 and states[-1] == "COMPLETED"


async def test_an_answered_or_unknown_item_cannot_be_answered_again(desk) -> None:
    await submit(desk, TRIP + ", aisle")
    item = (await until(lambda: open_items("hotels")))[0]
    assert inbox_respond(item["id"], {"answer": {"choice": "hostel"}}).status_code == 200
    assert inbox_respond(item["id"], {"answer": {"choice": "hostel"}}).status_code == 409
    assert inbox_respond("nope:000000", {"text": "x"}).status_code == 404


async def test_the_webhook_refuses_a_notification_without_the_right_token(desk) -> None:
    url = f"{desk['lab11-inbox'].url}/webhooks/a2a"
    async with httpx.AsyncClient() as http:
        r = await http.post(
            url, json={"statusUpdate": {}}, headers={"X-A2A-Notification-Token": "wrong"}
        )
    assert r.status_code == 401


async def test_nobody_answers_so_the_deadline_rejects_and_the_inbox_closes_the_items(desk) -> None:
    desk["lab11-orchestrator"].app.state.executor.deadline_s = 0.8
    task_id = await submit(desk)
    await until(lambda: len(inbox(status="open")) == 2)
    await until(
        lambda: not inbox(status="open"), within=20
    )  # the reaper acted, the push closed them
    assert await state_of(desk, task_id) == REJ
    assert {i["status"] for i in inbox("/items")} == {"closed"}
    assert [e["state"] for e in inbox("/events") if e["task_id"] == task_id][-1] == "REJECTED"


async def test_a_second_trip_does_not_disturb_the_first(desk) -> None:
    first = await submit(desk)
    second = await submit(desk, TRIP + ", aisle, palace")
    pay = (await until(lambda: open_items("payments")))[0]
    assert pay["task_id"] == second
    assert inbox_respond(pay["id"], {"answer": {"decision": "approve"}}).status_code == 200
    assert await state_of(desk, second) == DONE
    assert await state_of(desk, first) == TaskState.TASK_STATE_INPUT_REQUIRED  # still waiting
    _ = pytest


async def test_a_request_can_set_its_own_deadline(desk) -> None:
    task_id = await submit(desk, TRIP + ", deadline 1s")  # the executor default is 300 s
    await until(lambda: len(open_items()) == 2)
    await until(lambda: not open_items(), within=20)
    assert await state_of(desk, task_id) == REJ
