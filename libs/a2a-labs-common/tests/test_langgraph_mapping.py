"""Pattern 3: how LangGraph concepts map onto A2A (node events, interrupts, snapshots)."""

from __future__ import annotations

from typing import Annotated, Any

from a2a.types import TaskState
from a2a_labs_common.a2a import cards, events
from a2a_labs_common.a2a.client import collect, connect, send_text, stream_text
from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor
from a2a_labs_common.a2a.server import build_a2a_app
from a2a_labs_common.testing import asgi_client
from google.protobuf.json_format import MessageToDict
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel
from typing_extensions import TypedDict


class Plan(BaseModel):
    city: str
    nights: int


class State(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    plan: Plan
    approved: str


def graph_with_approval() -> Any:
    def make_plan(state: State) -> dict[str, Any]:
        return {"plan": Plan(city="Lisbon", nights=4)}

    def approve(state: State) -> dict[str, Any]:
        answer = interrupt({"question": "Approve the plan? yes/no", "options": ["yes", "no"]})
        return {"approved": str(answer)}

    def compose(state: State) -> dict[str, Any]:
        return {"messages": [AIMessage(f"# Itinerary\napproved={state['approved']}")]}

    g = StateGraph(State)
    for name, fn in (("make_plan", make_plan), ("approve", approve), ("compose", compose)):
        g.add_node(name, fn)
    g.add_edge(START, "make_plan")
    g.add_edge("make_plan", "approve")
    g.add_edge("approve", "compose")
    g.add_edge("compose", END)
    return g.compile(checkpointer=InMemorySaver())


async def _client(graph: Any, **executor_kw: Any):  # type: ignore[no-untyped-def]
    card = cards.build_card(
        service="lab04-langgraph-travel",
        name="LG",
        description="LangGraph mapping test",
        skills=[cards.skill("s", "S", "d", ["t"], [])],
    )
    for i in card.supported_interfaces:
        i.url = "http://testserver"
    app = build_a2a_app(
        card=card, executor=LangGraphA2AExecutor(graph, agent_name="t", **executor_kw)
    )
    return await connect("http://testserver", httpx_client=asgi_client(app), streaming=True)


async def test_node_start_events_carry_langgraph_node_metadata() -> None:
    c = await _client(graph_with_approval(), emit_node_updates=True)
    evs = [e async for e in stream_text(c, "go")]
    working = [
        (e.status_update.status.message.parts[0].text, MessageToDict(e.status_update.metadata))
        for e in evs
        if events.kind(e) == "status_update" and e.status_update.status.HasField("message")
    ]
    assert ("▶ node: make_plan", {"langgraph_node": "make_plan"}) in working
    assert ("▶ node: approve", {"langgraph_node": "approve"}) in working
    await c.aclose()


async def test_interrupt_payload_becomes_text_plus_data_part_and_resumes_by_text() -> None:
    c = await _client(graph_with_approval(), artifact_name="itinerary")
    first = await send_text(c, "go")
    assert first.state == TaskState.TASK_STATE_INPUT_REQUIRED
    parts = first.task.status.message.parts
    assert parts[0].text == "Approve the plan? yes/no"
    assert MessageToDict(parts[1].data) == {
        "question": "Approve the plan? yes/no",
        "options": ["yes", "no"],
    }
    done = await send_text(c, "yes", task_id=first.task_id, context_id=first.context_id)
    assert done.state == TaskState.TASK_STATE_COMPLETED and done.task_id == first.task_id
    assert done.task.artifacts[0].name == "itinerary" and "approved=yes" in done.text
    await c.aclose()


async def test_interrupt_resumes_by_data_part_with_a_resume_key() -> None:
    c = await _client(graph_with_approval())
    first = await send_text(c, "go")
    done = await send_text(
        c, "ignored text", data={"resume": "no"}, task_id=first.task_id, context_id=first.context_id
    )
    assert "approved=no" in done.text  # the DataPart's `resume` value won over the text
    await c.aclose()


async def test_state_snapshot_artifact_is_replaced_not_appended() -> None:
    c = await _client(graph_with_approval(), snapshot_artifact=True)
    first = await send_text(c, "go")
    await send_text(c, "yes", task_id=first.task_id, context_id=first.context_id)
    evs = [e async for e in stream_text(c, "again")]  # a second run in a fresh context
    snaps = [
        e.artifact_update
        for e in evs
        if events.kind(e) == "artifact_update"
        and e.artifact_update.artifact.name == "state-snapshot"
    ]
    assert snaps and all(not s.append for s in snaps)  # append=False: each replaces the last
    final = MessageToDict(snaps[-1].artifact.parts[0].data)
    assert final["plan"] == {"city": "Lisbon", "nights": 4}  # pydantic state became plain JSON
    assert "messages" not in final
    assert collect(evs).task is not None
    await c.aclose()
