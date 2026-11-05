"""Each delegate alone: the three ways an agent can pause a task, and how a human resumes it."""

from __future__ import annotations

import pytest
from a2a.helpers import get_data_parts
from a2a.types import TaskState
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.hitl import Answer, answer_part, extract_prompt
from lab11_hitl.agents.payments import PaymentsExecutor, risk_score

INPUT, AUTH = TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED
DONE, REJ = TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_REJECTED


def reply(a: Answer) -> dict:
    return {"hitl": {"answer": a.model_dump(exclude_none=True)}}


async def test_flights_asks_for_a_seat_then_books_in_the_same_task(desk) -> None:
    async with await connect(desk["lab11-flights"].url, streaming=False) as c:
        first = await send_text(c, "Book a flight to Lisbon")
        assert first.state == INPUT
        prompt = extract_prompt(first.task.status.message.parts)
        assert prompt.kind == "choice" and [o.id for o in prompt.options] == ["aisle", "window"]
        done = await send_text(
            c,
            "answer",
            task_id=first.task_id,
            context_id=first.context_id,
            data=reply(Answer(choice="window")),
        )
    assert done.state == DONE and done.task_id == first.task_id and "(window)" in done.text


async def test_a_request_that_already_says_the_seat_needs_no_human(desk) -> None:
    async with await connect(desk["lab11-flights"].url, streaming=False) as c:
        res = await send_text(c, "Book a flight to Lisbon, aisle please")
    assert res.state == DONE


async def test_the_human_can_refuse_a_question(desk) -> None:
    async with await connect(desk["lab11-flights"].url, streaming=False) as c:
        first = await send_text(c, "Book a flight to Lisbon")
        res = await send_text(
            c,
            "no",
            task_id=first.task_id,
            context_id=first.context_id,
            data=reply(Answer(decision="reject")),
        )
    assert res.state == REJ


async def test_hotels_is_a_langgraph_interrupt(desk) -> None:
    async with await connect(desk["lab11-hotels"].url, streaming=False) as c:
        first = await send_text(c, "Book a hotel for 3 nights")
        assert first.state == INPUT
        prompt = extract_prompt(first.task.status.message.parts)
        assert prompt.prompt == "Which hotel for 3 nights?" and len(prompt.options) == 3
        done = await send_text(
            c,
            "answer",
            task_id=first.task_id,
            context_id=first.context_id,
            data=reply(Answer(choice="harbor")),
        )
    assert done.state == DONE and "Harbor Inn" in done.text
    assert get_data_parts(done.task.artifacts[-1].parts)[0] == {"total": 330, "hotel": "harbor"}


async def test_hotels_skips_the_question_when_the_request_names_one(desk) -> None:
    async with await connect(desk["lab11-hotels"].url, streaming=False) as c:
        res = await send_text(c, "Book a hotel for 2 nights, Blue Hostel")
    assert res.state == DONE and "Blue Hostel" in res.text


async def test_docs_asks_for_consent_with_auth_required_not_input_required(desk) -> None:
    async with await connect(desk["lab11-docs"].url, streaming=False) as c:
        first = await send_text(c, "Check my travel documents")
        assert first.state == AUTH  # consent is its own interrupted state
        assert extract_prompt(first.task.status.message.parts).kind == "consent"
        done = await send_text(
            c,
            "ok",
            task_id=first.task_id,
            context_id=first.context_id,
            data=reply(Answer(decision="approve")),
        )
        assert done.state == DONE
        again = await send_text(c, "Check my travel documents")
        denied = await send_text(
            c,
            "no",
            task_id=again.task_id,
            context_id=again.context_id,
            data=reply(Answer(decision="reject")),
        )
    assert denied.state == REJ


def pay(amount: float) -> dict:
    return {"payment": {"amount": amount}}


async def test_a_small_payment_is_auto_approved_by_the_risk_gate(desk) -> None:
    async with await connect(desk["lab11-payments"].url, streaming=False) as c:
        res = await send_text(c, "Pay for the trip", data=pay(600))
    assert res.state == DONE and "auto-approved" in res.text


async def test_a_large_payment_waits_for_approval_and_each_decision_is_honoured(desk) -> None:
    async with await connect(desk["lab11-payments"].url, streaming=False) as c:
        first = await send_text(c, "Pay for the trip", data=pay(1460))
        prompt = extract_prompt(first.task.status.message.parts)
        assert (
            first.state == INPUT and prompt.kind == "approval" and prompt.proposal["amount"] == 1460
        )
        ok = await send_text(
            c,
            "yes",
            task_id=first.task_id,
            context_id=first.context_id,
            data=reply(Answer(decision="approve")),
        )
        assert ok.state == DONE and "approved by a person" in ok.text
        second = await send_text(c, "Pay for the trip", data=pay(1460))
        no = await send_text(
            c,
            "no",
            task_id=second.task_id,
            context_id=second.context_id,
            data=reply(Answer(decision="reject", text="too expensive")),
        )
    assert no.state == REJ and "too expensive" in no.task.status.message.parts[0].text


async def test_editing_the_amount_re_scores_it_and_can_ask_again(desk) -> None:
    async with await connect(desk["lab11-payments"].url, streaming=False) as c:
        first = await send_text(c, "Pay for the trip", data=pay(1460))
        # still above the limit: the agent asks again, with the NEW proposal, in the same task
        again = await send_text(
            c,
            "edit",
            task_id=first.task_id,
            context_id=first.context_id,
            data=reply(Answer(decision="edit", edit={"amount": 1200})),
        )
        assert again.state == INPUT and again.task_id == first.task_id
        assert extract_prompt(again.task.status.message.parts).proposal["amount"] == 1200
        # low enough: no human needed any more, so it just pays
        done = await send_text(
            c,
            "edit",
            task_id=first.task_id,
            context_id=first.context_id,
            data=reply(Answer(decision="edit", edit={"amount": 800})),
        )
    assert done.state == DONE and "Paid $800" in done.text


async def test_the_risk_score_reads_the_decision_model() -> None:
    low, _ = await risk_score(500)
    high, _ = await risk_score(5000)
    assert low < 1.5 <= high


async def test_the_executor_names_a_lost_proposal() -> None:
    assert PaymentsExecutor().amounts == {}  # in memory: a restart loses it (documented limit)
    _ = answer_part  # re-exported helper stays importable
    _ = pytest
