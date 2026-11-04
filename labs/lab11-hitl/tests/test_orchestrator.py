"""The orchestrator: parallel delegates, several open questions, answers routed correctly."""

from __future__ import annotations

import asyncio
import time

import pytest
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskState
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.hitl import Answer
from lab11_hitl.clients import answer, ledger, open_questions

INPUT, AUTH = TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED
DONE, REJ = TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_REJECTED
CANCELED, FAILED = TaskState.TASK_STATE_CANCELED, TaskState.TASK_STATE_FAILED
TRIP = "Book a trip to Lisbon for 4 nights"


async def book(desk, text: str = TRIP):
    conn = await connect(desk["lab11-orchestrator"].url, streaming=False)
    return conn, await send_text(conn, text)


def ids(res) -> dict[str, str]:
    return {q.agent.removeprefix("lab11-"): q.id for q in open_questions(res.task)}


async def test_two_delegates_ask_at_once_and_the_orchestrator_lists_both(desk) -> None:
    conn, res = await book(desk)
    async with conn:
        assert res.state == INPUT
        qs = open_questions(res.task)
        assert {q.agent for q in qs} == {"lab11-flights", "lab11-hotels"}
        assert {q.prompt.kind for q in qs} == {"choice"}
        assert all(q.task_id and q.task_id != res.task_id for q in qs)  # THEIR tasks, not ours
        assert "2 question(s)" in res.task.status.message.parts[0].text


async def test_answers_can_come_one_at_a_time_and_payment_needs_approval(desk) -> None:
    conn, res = await book(desk)
    async with conn:
        q = ids(res)
        one = await answer(conn, res, {q["flights"]: Answer(choice="aisle")})
        assert one.state == INPUT and one.task_id == res.task_id  # same orchestrator task
        assert [x.agent for x in open_questions(one.task)] == ["lab11-hotels"]  # one left
        two = await answer(conn, one, {q["hotels"]: Answer(choice="palace")})
        # stage 2: 420 + 4 * 260 = 1460, above the limit: payments asks for approval
        assert two.state == INPUT
        (pay,) = open_questions(two.task)
        assert pay.agent == "lab11-payments" and pay.prompt.kind == "approval"
        assert pay.prompt.proposal["amount"] == 1460
        done = await answer(conn, two, {pay.id: Answer(decision="approve")})
    assert done.state == DONE and done.task_id == res.task_id
    assert "Paid $1460" in done.text
    names = {a.name for a in done.task.artifacts}
    assert {"flights", "hotels", "payments", "itinerary", "ledger"} <= names


async def test_all_answers_in_one_message_and_a_cheap_total_needs_no_approval(desk) -> None:
    conn, res = await book(desk)
    async with conn:
        q = ids(res)
        done = await answer(
            conn, res, {q["flights"]: Answer(choice="window"), q["hotels"]: Answer(choice="hostel")}
        )
    # 420 + 4 * 45 = 600: the risk gate lets it through, so no third question
    assert done.state == DONE and "auto-approved" in done.text


async def test_questions_are_skipped_when_the_request_already_answers_them(desk) -> None:
    conn, res = await book(desk, TRIP + ", aisle seat, Harbor Inn")
    async with conn:
        assert res.state == DONE  # 420 + 440 = 860: no question at all


async def test_each_delegate_has_its_own_task_and_the_ledger_records_them(desk) -> None:
    conn, res = await book(desk)
    async with conn:
        q = ids(res)
        done = await answer(
            conn, res, {q["flights"]: Answer(choice="aisle"), q["hotels"]: Answer(choice="hostel")}
        )
        rows = ledger(res.task_id)
    by = {r["agent"]: r for r in rows}
    assert {r["state"] for r in rows} == {"COMPLETED"}
    assert (
        len({r["task_id"] for r in rows} | {done.task_id}) == 3 + 1
    )  # flights, hotels, payments, us
    async with await connect(desk["lab11-flights"].url, streaming=False) as c:
        t = await c.client.get_task(GetTaskRequest(id=by["lab11-flights"]["task_id"]))
    assert t.status.state == DONE


async def test_consent_from_a_delegate_is_relayed_as_auth_required(desk) -> None:
    conn, res = await book(desk, TRIP + ", check my documents")
    async with conn:
        assert res.state == INPUT  # flights + hotels ask for input, docs for consent
        by = {q.agent: q for q in open_questions(res.task)}
        assert (
            by["lab11-docs"].state == "AUTH_REQUIRED" and by["lab11-docs"].prompt.kind == "consent"
        )
        q = ids(res)
        step = await answer(
            conn, res, {q["flights"]: Answer(choice="aisle"), q["hotels"]: Answer(choice="hostel")}
        )
        # only the consent is left: the orchestrator is now AUTH_REQUIRED, not INPUT_REQUIRED
        assert step.state == AUTH
        done = await answer(conn, step, {q["docs"]: Answer(decision="approve")})
    assert done.state == DONE and "Passport read" in done.text


async def test_a_reject_at_payments_ends_the_trip_rejected(desk) -> None:
    conn, res = await book(desk)
    async with conn:
        q = ids(res)
        pay = await answer(
            conn, res, {q["flights"]: Answer(choice="aisle"), q["hotels"]: Answer(choice="palace")}
        )
        (pq,) = open_questions(pay.task)
        end = await answer(conn, pay, {pq.id: Answer(decision="reject", text="over budget")})
    assert end.state == REJ and "over budget" in end.task.status.message.parts[0].text


async def test_a_reject_cancels_the_delegates_that_are_still_waiting(desk) -> None:
    conn, res = await book(desk, TRIP + ", check my documents")
    async with conn:
        q = ids(res)
        end = await answer(conn, res, {q["hotels"]: Answer(decision="reject")})  # a "no" to one
        rows = ledger(res.task_id)
    assert end.state == REJ
    states = {r["agent"]: r["state"] for r in rows}
    assert states["lab11-hotels"] == "REJECTED"
    assert states["lab11-flights"] == states["lab11-docs"] == "CANCELED"  # nobody left waiting


async def test_cancelling_a_waiting_trip_cancels_its_delegates(desk) -> None:
    conn, res = await book(desk)
    async with conn:
        await conn.client.cancel_task(CancelTaskRequest(id=res.task_id))
        rows = ledger(res.task_id)
        final = await conn.client.get_task(GetTaskRequest(id=res.task_id))
    assert final.status.state == CANCELED
    assert {r["state"] for r in rows} == {"CANCELED"}


async def test_a_free_text_reply_is_read_by_the_decision_model(desk) -> None:
    conn, res = await book(desk, TRIP + ", aisle, palace")
    async with conn:
        assert res.state == INPUT  # only the payment question is open: plain text is enough
        (pay,) = open_questions(res.task)
        unclear = await answer(conn, res, text="purple monkey dishwasher")
        assert (
            unclear.state == INPUT and "could not read" in unclear.task.status.message.parts[0].text
        )
        done = await answer(conn, unclear, text="sure, go ahead")
    assert done.state == DONE and "approved by a person" in done.text and pay.id


async def test_several_open_questions_need_ids_so_text_alone_is_refused(desk) -> None:
    conn, res = await book(desk)
    async with conn:
        again = await answer(conn, res, text="aisle")
        assert again.state == INPUT and len(open_questions(again.task)) == 2
        assert "Several questions" in again.task.status.message.parts[0].text
        wrong = await answer(conn, again, {"nope:123": Answer(choice="aisle")})
    assert "Unknown question id" in wrong.task.status.message.parts[0].text


async def test_a_finished_trip_cannot_be_resumed(desk) -> None:
    conn, res = await book(desk, TRIP + ", aisle, harbor")
    async with conn:
        assert res.state == DONE
        with pytest.raises(Exception, match=r"(?i)terminal|completed|not.*(found|accept)|task"):
            await answer(conn, res, text="more")


async def test_an_unanswered_question_is_rejected_by_the_reaper_at_its_deadline(
    desk, monkeypatch
) -> None:
    ex = desk["lab11-orchestrator"].app.state.executor
    ex.deadline_s = 0.6  # the questions asked from now on expire quickly
    conn, res = await book(desk)
    async with conn:
        assert res.state == INPUT
        deadline = time.monotonic() + 15
        final = res
        while time.monotonic() < deadline:
            final = await conn.client.get_task(GetTaskRequest(id=res.task_id))
            if final.status.state != INPUT:
                break
            await asyncio.sleep(0.2)
        rows = ledger(res.task_id)
    state = final.status.state if hasattr(final, "status") else final.state
    assert state == REJ
    assert "timed out" in final.status.message.parts[0].text
    assert {r["state"] for r in rows} <= {"REJECTED", "CANCELED"}
