"""Pattern 3 on the wire: node events, interrupts as INPUT_REQUIRED, resume, cancel."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskState
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import collect, connect, send_text, stream_text
from a2a_labs_common.testing import asgi_client
from google.protobuf.json_format import MessageToDict
from lab04_langgraph.servers import trip_planner

FULL = "Plan a trip from Chicago to Lisbon, 12-16 May, budget $2000, 2 travelers"
S = TaskState


async def client_for(app, **kw):  # type: ignore[no-untyped-def]
    return await connect("http://testserver", httpx_client=asgi_client(app), **kw)


async def test_streaming_shows_one_started_event_per_node_with_metadata(low_risk) -> None:  # type: ignore[no-untyped-def]
    c = await client_for(trip_planner.build_app(low_risk), streaming=True)
    evs = [e async for e in stream_text(c, FULL)]
    starts = [
        (e.status_update.status.message.parts[0].text, MessageToDict(e.status_update.metadata))
        for e in evs
        if events.kind(e) == "status_update"
        and e.status_update.status.HasField("message")
        and e.status_update.status.message.parts[0].text.startswith("▶")
    ]
    nodes = [m["langgraph_node"] for _, m in starts]
    assert nodes == ["parse_request", "search_flights", "search_hotels", "risk_gate", "compose"]
    assert starts[0][0] == "▶ node: parse_request"
    done = collect(evs)
    assert done.state == S.TASK_STATE_COMPLETED
    assert [a.name for a in done.task.artifacts] == ["itinerary"]  # END -> the itinerary artifact
    assert "Total: $2200 (budget $2000)" in done.text
    await c.aclose()


async def test_missing_fields_are_input_required_with_structured_data_then_resume(low_risk) -> None:  # type: ignore[no-untyped-def]
    c = await client_for(trip_planner.build_app(low_risk), streaming=False)
    first = await send_text(c, "Plan a trip to Lisbon")
    assert first.state == S.TASK_STATE_INPUT_REQUIRED
    parts = first.task.status.message.parts
    assert "I still need" in parts[0].text
    assert set(MessageToDict(parts[1].data)["missing"]) == {
        "origin",
        "dates",
        "budget",
        "travelers",
    }
    done = await send_text(
        c,
        "from Chicago, 12-16 May, budget $5000, 2 travelers",
        task_id=first.task_id,
        context_id=first.context_id,
    )
    assert done.task_id == first.task_id and done.state == S.TASK_STATE_COMPLETED
    await c.aclose()


async def test_thread_id_is_the_context_id(high_risk) -> None:  # type: ignore[no-untyped-def]
    app = trip_planner.build_app(high_risk)
    c = await client_for(app, streaming=False)
    first = await send_text(c, FULL)  # pauses at the approval interrupt
    snap = await app.state.graph.aget_state({"configurable": {"thread_id": first.context_id}})
    assert snap.values["request"]["destination"] == "Lisbon" and snap.next == ("approval",)
    async with asgi_client(app) as http:  # the optional CUSTOM debug route shows the same
        body = (await http.get(f"/debug/state/{first.context_id}")).json()
    assert body["thread_id"] == first.context_id and body["next"] == ["approval"]
    assert body["values"]["request"]["destination"] == "Lisbon" and body["values"]["total"] == 2200
    await c.aclose()


async def test_over_budget_plan_asks_approval_then_no_loops_then_yes_completes(high_risk) -> None:  # type: ignore[no-untyped-def]
    c = await client_for(trip_planner.build_app(high_risk), streaming=False)
    a = await send_text(c, FULL)
    q1 = a.task.status.message.parts
    assert a.state == S.TASK_STATE_INPUT_REQUIRED and "$2200" in q1[0].text
    assert MessageToDict(q1[1].data)["options"] == ["yes", "no"]
    b = await send_text(c, "no", task_id=a.task_id, context_id=a.context_id)
    assert b.state == S.TASK_STATE_INPUT_REQUIRED and "$1940" in b.task.status.message.parts[0].text
    done = await send_text(c, "yes", task_id=a.task_id, context_id=a.context_id)
    assert done.state == S.TASK_STATE_COMPLETED and "Total: $1940" in done.text
    await c.aclose()


async def test_exception_in_a_node_is_failed(monkeypatch, low_risk) -> None:  # type: ignore[no-untyped-def]
    from lab04_langgraph.agents import trip_planner as tp

    async def boom(state):  # type: ignore[no-untyped-def]
        raise RuntimeError("supplier down")

    monkeypatch.setattr(tp, "search_flights", boom)
    c = await client_for(trip_planner.build_app(low_risk), streaming=False)
    r = await send_text(c, FULL)
    assert r.state == S.TASK_STATE_FAILED and "supplier down" in r.task.status.message.parts[0].text
    await c.aclose()


async def test_cancel_mid_graph_leaves_the_checkpoint_at_the_last_superstep(  # type: ignore[no-untyped-def]
    monkeypatch, serve, low_risk, pick_port
) -> None:
    monkeypatch.setenv("TRIP_STEP_DELAY", "0.6")
    port = pick_port()  # the card must advertise the port we really serve on
    monkeypatch.setenv("LAB04_LANGGRAPH_TRAVEL_URL", f"http://127.0.0.1:{port}")
    app = trip_planner.build_app(low_risk)
    live = await serve(app, port)
    c = await connect(f"http://127.0.0.1:{live.port}", streaming=False)
    r = await send_text(c, FULL, return_immediately=True)
    await asyncio.sleep(0.9)  # flights done (0.6 s); hotels still sleeping
    canceled = await c.client.cancel_task(CancelTaskRequest(id=r.task_id))
    assert canceled.status.state == S.TASK_STATE_CANCELED
    after = await c.client.get_task(GetTaskRequest(id=r.task_id))
    assert after.status.state == S.TASK_STATE_CANCELED
    async with httpx.AsyncClient() as http:
        state = (await http.get(f"http://127.0.0.1:{live.port}/debug/state/{r.context_id}")).json()
    assert state["values"]["flights"] and "hotels" not in state["values"]  # partial state kept
    assert state["next"] == ["search_hotels"]  # where a resume would pick up
    await c.aclose()


@pytest.mark.parametrize("kind", ["thread_missing"])
async def test_unknown_context_has_no_checkpoint(kind: str, low_risk) -> None:  # type: ignore[no-untyped-def]
    app = trip_planner.build_app(low_risk)
    async with asgi_client(app) as http:
        body = (await http.get("/debug/state/not-a-context")).json()
    assert body["values"] == {} and body["next"] == []
