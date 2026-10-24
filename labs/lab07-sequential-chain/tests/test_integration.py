"""Integration tests against the running ``lab07`` profile (``make e2e LAB=lab07``)."""

from __future__ import annotations

import uuid

import pytest
from a2a.types import TaskState
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from lab07_chain.executors.chain_orchestrator import artifact_data, artifact_text

pytestmark = pytest.mark.compose
ORCH = "lab07-chain-orchestrator"
TOPIC = "Explain how vaccines work in three paragraphs"


@pytest.fixture(scope="module", autouse=True)
async def _services_up() -> None:
    await wait_for_cards(
        ["lab07-adk-planner", "lab07-strands-writer", "lab07-langgraph-editor", ORCH], timeout=180
    )


async def test_the_orchestrated_chain_completes_with_provenance() -> None:
    async with await connect(ORCH, streaming=False) as c:
        res = await send_text(c, TOPIC, context_id=str(uuid.uuid4()))
    assert res.state == TaskState.TASK_STATE_COMPLETED and artifact_text(res, "final")
    prov = artifact_data(res, "provenance")
    assert prov and {h["state"] for h in prov["hops"]} == {"COMPLETED"}
    assert [h["agent"] for h in prov["hops"]][:3] == ["planner", "writer", "editor"]


async def test_a_vague_topic_pauses_the_chain_and_an_answer_resumes_it() -> None:
    async with await connect(ORCH, streaming=False) as c:
        first = await send_text(c, "Write about it")
        assert first.state == TaskState.TASK_STATE_INPUT_REQUIRED
        done = await send_text(
            c,
            "ten-year-olds learning about volcanoes",
            task_id=first.task_id,
            context_id=first.context_id,
        )
    assert done.task_id == first.task_id and done.state == TaskState.TASK_STATE_COMPLETED


async def test_relay_mode_leaves_the_result_on_the_first_hops_task() -> None:
    route = ["lab07-strands-writer", "lab07-langgraph-editor"]
    meta = {"relay": {"route": route, "origin_task": "", "hop": 0}}
    async with await connect("lab07-adk-planner", streaming=True) as c:
        res = await send_text(c, TOPIC, metadata=meta)
    assert res.state == TaskState.TASK_STATE_COMPLETED
    assert [a.name for a in res.task.artifacts] == ["outline", "downstream-final"]
