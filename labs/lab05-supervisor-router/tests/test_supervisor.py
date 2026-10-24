"""The LangGraph supervisor end to end against three live specialists, all in fake mode."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from a2a.types import TaskState
from a2a_labs_common.a2a.client import send_text
from a2a_labs_common.models.decision import FakeDecisionModel
from a2a_labs_common.testing import asgi_client
from lab05_supervisor.agents.supervisor import Supervisor
from lab05_supervisor.prompts import CURRENCY, FACTS, ROUTING_TABLE, WEATHER


async def state_of(app: Any, ctx: str) -> dict[str, Any]:
    async with asgi_client(app) as http:
        return (await http.get(f"/debug/state/{ctx}")).json()["values"]  # type: ignore[no-any-return]


def status_text(result: Any) -> str:
    return " ".join(p.text for p in result.task.status.message.parts if p.text)


async def ledger_of(app: Any, ctx: str) -> dict[str, Any]:
    async with asgi_client(app) as http:
        return (await http.get(f"/debug/ledger/{ctx}")).json()  # type: ignore[no-any-return]


@pytest.mark.parametrize("case", ROUTING_TABLE, ids=lambda c: c.prompt[:28])
async def test_the_routing_table(serve_supervisor: Any, case: Any) -> None:
    async with serve_supervisor("langgraph") as c:
        ctx = str(uuid.uuid4())
        res = await send_text(c, case.prompt, context_id=ctx)
        assert res.state == TaskState.TASK_STATE_COMPLETED
        assert res.text
        values = await state_of(c.app, ctx)
        delegated = set(values.get("ledger", {}))
        assert delegated == set(case.langgraph)


async def test_a_single_intent_delegates_once_and_the_ledger_has_one_entry(
    serve_supervisor: Any,
) -> None:
    async with serve_supervisor("langgraph") as c:
        ctx = str(uuid.uuid4())
        res = await send_text(c, "Convert 300 CAD to EUR", context_id=ctx)
        assert "219" in res.text or "EUR" in res.text  # the specialist's deterministic answer
        led = (await state_of(c.app, ctx))["ledger"]
        assert list(led) == [CURRENCY]
        entry = led[CURRENCY]
        assert entry["last_state"] == "COMPLETED" and entry["active_task_id"] is None
        assert len(entry["task_ids"]) == 1 and entry["remote_context_id"] != ctx
        route = next(
            t for t in (await state_of(c.app, ctx))["decision_trace"] if t["question"] == "route"
        )
        assert route["choice"] == CURRENCY and route["backend"] == "fake"


async def test_none_is_answered_directly_with_no_delegation(serve_supervisor: Any) -> None:
    async with serve_supervisor("langgraph") as c:
        ctx = str(uuid.uuid4())
        res = await send_text(c, "Tell me a joke about routers.", context_id=ctx)
        assert res.state == TaskState.TASK_STATE_COMPLETED
        assert (await state_of(c.app, ctx)).get("ledger") in (None, {})


async def test_multi_intent_fans_out_to_three_specialists_in_parallel(
    serve_supervisor: Any,
) -> None:
    async with serve_supervisor("langgraph") as c:
        ctx = str(uuid.uuid4())
        res = await send_text(c, ROUTING_TABLE[4].prompt, context_id=ctx)
        values = await state_of(c.app, ctx)
        assert set(values["ledger"]) == {WEATHER, CURRENCY, FACTS}
        assert set(values["results"]) == {WEATHER, CURRENCY, FACTS}
        # each remote has its OWN context and its OWN task: ids are never shared
        ctxs = {e["remote_context_id"] for e in values["ledger"].values()}
        tasks = [t for e in values["ledger"].values() for t in e["task_ids"]]
        assert len(ctxs) == 3 and len(set(tasks)) == 3
        for name in (WEATHER, CURRENCY, FACTS):
            assert name in res.text  # the merged answer names every specialist


async def test_a_follow_up_reuses_the_remote_context_and_starts_a_new_task(
    serve_supervisor: Any,
) -> None:
    async with serve_supervisor("langgraph") as c:
        ctx = str(uuid.uuid4())
        await send_text(c, "Convert 300 CAD to EUR", context_id=ctx)
        first = (await state_of(c.app, ctx))["ledger"][CURRENCY]
        await send_text(c, "How much is 250 GBP in JPY?", context_id=ctx)
        second = (await state_of(c.app, ctx))["ledger"][CURRENCY]
        assert second["remote_context_id"] == first["remote_context_id"]
        assert len(second["task_ids"]) == 2 and second["task_ids"][0] == first["task_ids"][0]
        assert (await ledger_of(c.app, ctx))[CURRENCY]["task_ids"] == second["task_ids"]


async def test_a_remote_input_required_pauses_the_supervisor_and_continues_the_same_task(
    serve_supervisor: Any,
) -> None:
    async with serve_supervisor(
        "langgraph",
        supervisor=Supervisor(
            decision=FakeDecisionModel(
                {"route": "currency", "unclear_topic": 0.1, "multi": 0.1, "complete": 0.9}
            )
        ),
    ) as c:
        ctx = str(uuid.uuid4())
        paused = await send_text(c, "Convert 100 USD", context_id=ctx)  # no target currency
        assert paused.state == TaskState.TASK_STATE_INPUT_REQUIRED
        live = (await ledger_of(c.app, ctx))[CURRENCY]
        assert live["last_state"] == "INPUT_REQUIRED" and live["active_task_id"]
        done = await send_text(c, "JPY", context_id=ctx, task_id=paused.task_id)
        assert done.state == TaskState.TASK_STATE_COMPLETED and "JPY" in done.text
        final = (await ledger_of(c.app, ctx))[CURRENCY]
        assert final["task_ids"] == [live["active_task_id"]]  # the SAME remote task, continued
        assert final["last_state"] == "COMPLETED" and final["active_task_id"] is None


async def test_an_ambiguous_request_asks_the_user_before_routing(serve_supervisor: Any) -> None:
    async with serve_supervisor("langgraph") as c:
        ctx = str(uuid.uuid4())
        paused = await send_text(c, "Help me with Rome", context_id=ctx)
        assert paused.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert "What would you like" in status_text(paused)
        assert (await state_of(c.app, ctx)).get("ledger") in (None, {})  # nothing delegated yet


async def test_a_specialist_that_is_down_gives_a_degraded_answer_not_a_failure(
    serve_supervisor: Any, specialists: Any
) -> None:
    async with serve_supervisor("langgraph") as c:
        await c.app.state.supervisor.directory.discover()  # the cards are known BEFORE the outage
        specialists.live["lab05-weather"].stop()
        ctx = str(uuid.uuid4())
        res = await send_text(c, "What is the weather in Tokyo?", context_id=ctx)
        assert res.state == TaskState.TASK_STATE_COMPLETED  # NOT failed
        assert "unavailable" in res.text.lower() and WEATHER in res.text
        entry = (await state_of(c.app, ctx))["ledger"][WEATHER]
        assert entry["last_state"] == "UNREACHABLE" and entry["task_ids"] == []


async def test_a_specialist_registered_at_runtime_becomes_a_routing_option(
    serve_supervisor: Any, specialists: Any
) -> None:
    async with serve_supervisor("langgraph") as c:
        async with asgi_client(c.app) as http:
            # drop the facts specialist, then add it back: no code change, only the directory
            await http.request("DELETE", "/specialists", json={"service": "lab05-facts"})
            names = [r["name"] for r in (await http.get("/specialists")).json()]
            assert FACTS not in names
            rows = (await http.post("/specialists", json={"service": "lab05-facts"})).json()
            assert FACTS in [r["name"] for r in rows]
        res = await send_text(c, "Tell me some facts about Lisbon.", context_id=str(uuid.uuid4()))
        assert res.state == TaskState.TASK_STATE_COMPLETED and "Lisbon" in res.text
