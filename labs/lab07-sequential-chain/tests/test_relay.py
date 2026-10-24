"""Relay mode: the client talks to the first hop; the chain completes behind it."""

from __future__ import annotations

from typing import Any

from a2a.types import GetTaskRequest, ListTasksRequest, TaskState
from a2a_labs_common.a2a.client import connect, send_text
from lab07_chain.executors.chain_orchestrator import artifact_text

TOPIC = "Explain how vaccines work in three paragraphs"
ROUTE = ["lab07-strands-writer", "lab07-langgraph-editor"]


def relay(route: list[str]) -> dict[str, Any]:
    return {"relay": {"route": route, "origin_task": "", "hop": 0}}


async def test_the_first_hop_holds_the_downstream_final_artifact(hops: Any) -> None:
    async with await connect("lab07-adk-planner", streaming=True) as c:
        res = await send_text(c, TOPIC, metadata=relay(ROUTE))
    assert res.state == TaskState.TASK_STATE_COMPLETED
    names = [a.name for a in res.task.artifacts]
    assert names == ["outline", "downstream-final"]
    assert artifact_text(res, "downstream-final").startswith("# Draft")  # the editor's final text


async def test_every_hop_references_the_task_before_it(hops: Any) -> None:
    async with await connect("lab07-adk-planner", streaming=True) as c:
        first = await send_text(c, TOPIC, metadata=relay(ROUTE))
    async with await connect("lab07-strands-writer", streaming=False) as w:
        wt = (await w.client.list_tasks(ListTasksRequest())).tasks[0]
        wt = await w.client.get_task(GetTaskRequest(id=wt.id, history_length=5))
    assert list(wt.history[0].reference_task_ids) == [first.task_id]
    assert wt.history[0].metadata["relay"]["hop"] == 1
    assert [a.name for a in wt.artifacts] == ["draft", "downstream-final"]
    async with await connect("lab07-langgraph-editor", streaming=False) as e:
        et = (await e.client.list_tasks(ListTasksRequest())).tasks[0]
        et = await e.client.get_task(GetTaskRequest(id=et.id, history_length=5))
    assert list(et.history[0].reference_task_ids) == [wt.id]
    assert [a.name for a in et.artifacts] == ["final", "review"]  # last hop: nothing downstream


async def test_without_a_route_a_hop_behaves_normally(hops: Any) -> None:
    async with await connect("lab07-adk-planner", streaming=False) as c:
        res = await send_text(c, TOPIC)
    assert [a.name for a in res.task.artifacts] == ["outline"]


async def test_a_dead_downstream_hop_fails_the_first_task_naming_it(hops: Any) -> None:
    hops.stop("lab07-langgraph-editor")
    async with await connect("lab07-adk-planner", streaming=True) as c:
        res = await send_text(c, TOPIC, metadata=relay(ROUTE))
    assert res.state == TaskState.TASK_STATE_FAILED
    assert "lab07-strands-writer" in res.task.status.message.parts[0].text  # its next hop failed
