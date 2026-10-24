"""Hop by hop, by hand: Parts travel as DataParts, provenance as reference_task_ids."""

from __future__ import annotations

from typing import Any

from a2a.types import GetTaskRequest, TaskState
from a2a_labs_common.a2a.client import connect, send_text
from google.protobuf.json_format import MessageToDict
from lab07_chain.executors.chain_orchestrator import artifact_data, artifact_text

TOPIC = "Explain how vaccines work in three paragraphs"


async def test_planner_returns_an_outline_datapart_and_a_text_rendering(hops: Any) -> None:
    async with await connect("lab07-adk-planner", streaming=False) as c:
        res = await send_text(c, TOPIC)
    assert res.state == TaskState.TASK_STATE_COMPLETED
    outline = artifact_data(res, "outline")
    assert outline and outline["sections"] and outline["title"]
    assert "Title:" in artifact_text(res, "outline")


async def test_writer_reads_the_outline_datapart_and_records_its_provenance(hops: Any) -> None:
    async with await connect("lab07-adk-planner", streaming=False) as p:
        plan = await send_text(p, TOPIC)
    outline = artifact_data(plan, "outline")
    async with await connect("lab07-strands-writer", streaming=False) as w:
        res = await send_text(
            w,
            "Write the article from the attached outline.",
            data=outline,
            reference_task_ids=[plan.task_id],
        )
        assert res.state == TaskState.TASK_STATE_COMPLETED
        draft = artifact_text(res, "draft")
        assert draft.startswith("# Draft") and outline["sections"][0]["heading"] in draft
        task = await w.client.get_task(GetTaskRequest(id=res.task_id, history_length=5))
    assert list(task.history[0].reference_task_ids) == [plan.task_id]  # the provenance link


async def test_editor_returns_final_text_and_a_review_datapart(hops: Any) -> None:
    async with await connect("lab07-langgraph-editor", streaming=False) as e:
        res = await send_text(e, "# Draft\n\nShort text.")
    assert res.state == TaskState.TASK_STATE_COMPLETED
    assert artifact_text(res, "final").startswith("# Draft")
    review = artifact_data(res, "review")
    assert review and review["issues"] and review["summary"]


async def test_a_vague_topic_makes_the_planner_ask_and_the_answer_continues_that_task(
    hops: Any,
) -> None:
    async with await connect("lab07-adk-planner", streaming=False) as p:
        first = await send_text(p, "Write about it")
        assert first.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert first.task.status.message.parts[0].text == "Who is the audience?"
        second = await send_text(
            p,
            "ten-year-olds learning about volcanoes",
            task_id=first.task_id,
            context_id=first.context_id,
        )
    assert second.task_id == first.task_id and second.state == TaskState.TASK_STATE_COMPLETED
    assert "volcanoes" in MessageToDict(second.task.artifacts[0].parts[0].data)["title"]
