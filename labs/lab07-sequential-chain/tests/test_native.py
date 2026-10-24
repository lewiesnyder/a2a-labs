"""The framework-native chains reach the same end with implicit, text-only hand-off."""

from __future__ import annotations

import uuid
from typing import Any

from a2a.types import ListTasksRequest
from a2a_labs_common.a2a.client import connect
from lab07_chain import native

TOPIC = "Explain how vaccines work in three paragraphs"


async def test_langgraph_chain_passes_text_between_remote_nodes(hops: Any) -> None:
    graph = native.build_langgraph_chain()
    out = await graph.ainvoke({"topic": TOPIC}, {"configurable": {"thread_id": str(uuid.uuid4())}})
    assert out["final"].startswith("# Draft") and not out.get("error")
    assert set(out["remote_tasks"]) == set(native.HOPS)


async def test_strands_graph_feeds_each_node_the_previous_output(hops: Any) -> None:
    final, sent = await native.run_strands_chain(TOPIC, scope=str(uuid.uuid4()))
    assert sent["planner"] == TOPIC and sent["writer"].startswith("Write the article")
    assert "Title:" in sent["writer"]  # the outline arrived as TEXT, not as a DataPart
    assert final.startswith("# Draft")


async def test_adk_sequential_agent_sends_each_remote_only_the_users_message(hops: Any) -> None:
    events = await native.run_adk_chain(TOPIC)
    assert [a for a, _ in events] == ["planner", "writer", "editor"]
    async with await connect("lab07-strands-writer", streaming=False) as w:
        tasks = (await w.client.list_tasks(ListTasksRequest())).tasks
    first = tasks[0].history[0]
    assert [p.text for p in first.parts] == [TOPIC]  # implicit hand-off: no outline Part
    assert not first.reference_task_ids  # and no provenance link: that is the orchestrator's job
