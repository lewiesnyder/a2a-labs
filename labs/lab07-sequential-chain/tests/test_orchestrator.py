"""The orchestrator: provenance, the quality loop, INPUT_REQUIRED, failure and cancel."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from a2a.types import CancelTaskRequest, GetTaskRequest, TaskState
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.models.decision import FakeDecisionModel
from google.protobuf.json_format import MessageToDict
from lab07_chain._testutil import Live, free_port
from lab07_chain.executors.chain_orchestrator import artifact_data, artifact_text
from lab07_chain.servers.orchestrator import build_app

TOPIC = "Explain how vaccines work in three paragraphs"


@pytest.fixture
async def orchestrator(hops: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    started: list[Live] = []

    async def make(**kw: Any) -> Live:
        port = free_port()  # the card advertises this URL, so it must be set before the build
        monkeypatch.setenv("LAB07_CHAIN_ORCHESTRATOR_URL", f"http://127.0.0.1:{port}")
        lv = await Live(build_app(**kw), port).start()
        started.append(lv)
        return lv

    yield make
    for lv in started:
        lv.stop()


async def test_a_chain_produces_final_and_provenance_with_linked_hops(
    orchestrator: Any, hops: Any
) -> None:
    orch = await orchestrator()
    async with await connect(orch.url, streaming=False) as c:
        res = await send_text(c, TOPIC)
        assert res.state == TaskState.TASK_STATE_COMPLETED
        assert artifact_text(res, "final").startswith("# Draft")
        prov = artifact_data(res, "provenance")
    assert prov and [h["agent"] for h in prov["hops"]] == ["planner", "writer", "editor"]
    assert {h["state"] for h in prov["hops"]} == {"COMPLETED"} and prov["loops"] == 0
    # each hop owns its own context and task
    assert len({h["context_id"] for h in prov["hops"]}) == 3
    assert len({h["task_id"] for h in prov["hops"]}) == 3
    # verify the chain on the hops themselves with GetTask
    by = {h["agent"]: h for h in prov["hops"]}
    for hop, upstream in (("writer", "planner"), ("editor", "writer")):
        async with await connect(by[hop]["service"], streaming=False) as hc:
            task = await hc.client.get_task(GetTaskRequest(id=by[hop]["task_id"], history_length=5))
        assert list(task.history[0].reference_task_ids) == [by[upstream]["task_id"]]
        assert task.status.state == TaskState.TASK_STATE_COMPLETED


async def test_progress_is_streamed_from_the_single_top_level_task(orchestrator: Any) -> None:
    from a2a_labs_common.a2a.client import stream_text

    orch = await orchestrator()
    async with await connect(orch.url, streaming=True) as c:
        said = [
            ev.status_update.status.message.parts[0].text
            async for ev in stream_text(c, TOPIC)
            if ev.WhichOneof("payload") == "status_update"
            and ev.status_update.status.HasField("message")
        ]
    assert [s for s in said if s[0].isdigit()] == ["1/3 planning", "2/3 writing", "3/3 editing"]


async def test_a_low_score_loops_back_to_the_writer_at_most_max_loops_times(
    orchestrator: Any,
) -> None:
    orch = await orchestrator(decision=FakeDecisionModel({"quality": 1.0}), max_loops=2)
    async with await connect(orch.url, streaming=False) as c:
        res = await send_text(c, TOPIC)
    prov = artifact_data(res, "provenance")
    assert res.state == TaskState.TASK_STATE_COMPLETED and prov
    assert prov["loops"] == 2 and prov["scores"] == [1.0, 1.0, 1.0]
    agents = [h["agent"] for h in prov["hops"]]
    assert agents == ["planner", "writer", "editor", "writer", "editor", "writer", "editor"]
    writers = [h for h in prov["hops"] if h["agent"] == "writer"]
    assert len({w["context_id"] for w in writers}) == 1  # the writer remembers earlier drafts
    assert len({w["task_id"] for w in writers}) == 3  # ...but each revision is a NEW task
    assert writers[1]["references"] == [writers[0]["task_id"], prov["hops"][2]["task_id"]]
    assert "Revised" in artifact_text(res, "final")


async def test_a_good_score_does_not_loop(orchestrator: Any) -> None:
    orch = await orchestrator(decision=FakeDecisionModel({"quality": 3.0}))
    async with await connect(orch.url, streaming=False) as c:
        res = await send_text(c, TOPIC)
    assert artifact_data(res, "provenance")["loops"] == 0  # type: ignore[index]


async def test_a_planner_question_surfaces_and_the_answer_continues_the_chain(
    orchestrator: Any,
) -> None:
    orch = await orchestrator()
    async with await connect(orch.url, streaming=False) as c:
        first = await send_text(c, "Write about it")
        assert first.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert first.task.status.message.parts[0].text == "Who is the audience?"
        done = await send_text(
            c,
            "ten-year-olds learning about volcanoes",
            task_id=first.task_id,
            context_id=first.context_id,
        )
    assert done.task_id == first.task_id and done.state == TaskState.TASK_STATE_COMPLETED
    prov = artifact_data(done, "provenance")
    assert [h["state"] for h in prov["hops"]][:2] == ["INPUT_REQUIRED", "COMPLETED"]  # type: ignore[index]
    assert prov["hops"][0]["task_id"] == prov["hops"][1]["task_id"]  # type: ignore[index]


async def test_a_failed_hop_fails_the_chain_and_names_the_hop(orchestrator: Any, hops: Any) -> None:
    orch = await orchestrator()
    hops.stop("lab07-strands-writer")
    async with await connect(orch.url, streaming=False) as c:
        res = await send_text(c, TOPIC)
    assert res.state == TaskState.TASK_STATE_FAILED
    assert res.task.status.message.parts[0].text.startswith("hop writer failed")


def slow_agent(url: str) -> Any:
    """A hop that never finishes by itself: lets a test cancel the chain mid-hop."""
    from a2a.helpers import new_task_from_user_message
    from a2a.server.agent_execution import AgentExecutor
    from a2a.server.tasks import TaskUpdater
    from a2a_labs_common.a2a.cards import build_card, skill
    from a2a_labs_common.a2a.server import build_a2a_app

    class Slow(AgentExecutor):
        async def execute(self, context: Any, event_queue: Any) -> None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
            u = TaskUpdater(event_queue, context.task_id, context.context_id)
            await u.start_work()
            await asyncio.sleep(60)

        async def cancel(self, context: Any, event_queue: Any) -> None:
            await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

    card = build_card(
        service="lab07-strands-writer",
        name="Slow writer",
        description="slow",
        skills=[skill("w", "w", "w", ["w"], ["w"])],
    )
    for iface in card.supported_interfaces:
        iface.url = url  # the card must point at THIS server, not the real writer
    return build_a2a_app(card=card, executor=Slow())


async def test_cancelling_the_chain_cancels_the_hop_that_is_running(
    orchestrator: Any, hops: Any
) -> None:
    port = free_port()
    slow = await Live(slow_agent(f"http://127.0.0.1:{port}"), port).start()
    try:
        orch = await orchestrator(
            services={
                "planner": "lab07-adk-planner",
                "writer": slow.url,
                "editor": "lab07-langgraph-editor",
            }
        )
        async with await connect(orch.url, streaming=False) as c:
            run = await send_text(c, TOPIC, return_immediately=True)
            hop_task = None
            async with await connect(slow.url, streaming=False) as sc:
                for _ in range(100):  # wait until the writer hop has really started
                    from a2a.types import ListTasksRequest

                    tasks = (await sc.client.list_tasks(ListTasksRequest())).tasks
                    if tasks:
                        hop_task = tasks[0].id
                        break
                    await asyncio.sleep(0.1)
                assert hop_task
                canceled = await c.client.cancel_task(CancelTaskRequest(id=run.task_id))
                assert canceled.status.state == TaskState.TASK_STATE_CANCELED
                for _ in range(50):
                    state = (await sc.client.get_task(GetTaskRequest(id=hop_task))).status.state
                    if state == TaskState.TASK_STATE_CANCELED:
                        break
                    await asyncio.sleep(0.1)
                assert state == TaskState.TASK_STATE_CANCELED
    finally:
        slow.stop()
    _ = MessageToDict
