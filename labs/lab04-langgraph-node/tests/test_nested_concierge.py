"""A2A -> LangGraph -> A2A -> LangGraph: a remote interrupt becomes a local one."""

from __future__ import annotations

import pytest
from a2a.types import ListTasksRequest, TaskState
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.testing import asgi_client
from lab04_langgraph.servers import concierge, trip_planner

FULL = "Plan a trip from Chicago to Lisbon, 12-16 May, budget $2000, 2 travelers"
S = TaskState


@pytest.fixture
async def stack(monkeypatch, serve, high_risk, pick_port):  # type: ignore[no-untyped-def]
    """Trip Planner live (scripted high risk) behind an in-process Concierge."""
    port = pick_port()
    monkeypatch.setenv("LAB04_LANGGRAPH_TRAVEL_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("LAB04_LANGGRAPH_TRAVEL_PORT", str(port))
    await serve(trip_planner.build_app(high_risk), port)
    app = concierge.build_app()
    c = await connect("http://testserver", httpx_client=asgi_client(app), streaming=False)
    yield app, c
    await c.aclose()


async def remote_tasks():  # type: ignore[no-untyped-def]
    async with await connect("lab04-langgraph-travel", streaming=False) as p:
        return (await p.client.list_tasks(ListTasksRequest())).tasks


async def test_two_interrupt_hops_resolve_with_one_client_reply_each(stack) -> None:  # type: ignore[no-untyped-def]
    _app, c = stack
    first = await send_text(c, FULL)  # the planner's approval question surfaces here
    assert first.state == S.TASK_STATE_INPUT_REQUIRED
    parts = first.task.status.message.parts
    assert "Approve?" in parts[0].text and "$2200" in parts[0].text
    done = await send_text(c, "yes", task_id=first.task_id, context_id=first.context_id)
    assert done.task_id == first.task_id and done.state == S.TASK_STATE_COMPLETED
    assert "Budget check: $2200 is OVER the $2000 budget by $200." in done.text
    tasks = await remote_tasks()
    assert len(tasks) == 1 and tasks[0].status.state == S.TASK_STATE_COMPLETED  # ONE remote task


async def test_concierge_state_records_the_remote_task_per_remote(stack) -> None:  # type: ignore[no-untyped-def]
    app, c = stack
    first = await send_text(c, FULL)
    await send_text(c, "yes", task_id=first.task_id, context_id=first.context_id)
    state = (
        await app.state.graph.aget_state({"configurable": {"thread_id": first.context_id}})
    ).values
    tracked = state["remote_tasks"]["lab04-langgraph-travel"]
    (remote,) = await remote_tasks()
    assert tracked["task_id"] == remote.id and tracked["state"] == "COMPLETED"
    assert any("[lab04-langgraph-travel]" in line for line in state["progress"])  # remote progress


async def test_replay_never_resends_the_request_across_several_answers(stack) -> None:  # type: ignore[no-untyped-def]
    """Missing fields, then approval 'no', then approval again, then 'yes': four client replies,
    LangGraph re-runs the remote node four times, the remote still sees exactly one task."""
    _app, c = stack
    t = await send_text(c, "Plan a trip to Lisbon")
    assert "I still need" in t.task.status.message.parts[0].text
    t = await send_text(
        c,
        "from Chicago, 12-16 May, budget $2000, 2 travelers",
        task_id=t.task_id,
        context_id=t.context_id,
    )
    assert "$2200" in t.task.status.message.parts[0].text
    t = await send_text(c, "no", task_id=t.task_id, context_id=t.context_id)
    assert "$1940" in t.task.status.message.parts[0].text  # the cheaper plan, asked again
    done = await send_text(c, "yes", task_id=t.task_id, context_id=t.context_id)
    assert done.state == S.TASK_STATE_COMPLETED
    assert "within the $2000 budget" in done.text
    tasks = await remote_tasks()
    assert len(tasks) == 1, f"the node re-sent the request: {len(tasks)} remote tasks"


async def test_remote_failure_routes_to_the_failed_node(monkeypatch, serve, pick_port) -> None:  # type: ignore[no-untyped-def]
    dead = pick_port()  # nothing listens here
    monkeypatch.setenv("LAB04_LANGGRAPH_TRAVEL_URL", f"http://127.0.0.1:{dead}")
    app = concierge.build_app()
    c = await connect("http://testserver", httpx_client=asgi_client(app), streaming=False)
    r = await send_text(c, FULL)
    assert r.state == S.TASK_STATE_COMPLETED  # the concierge itself handled the failure
    assert "could not help" in r.text and "lab04-langgraph-travel" in r.text
    await c.aclose()
