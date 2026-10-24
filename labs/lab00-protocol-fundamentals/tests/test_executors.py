"""Every playground command, driven in-process through the real SDK client (no network)."""

from __future__ import annotations

import pytest
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    TaskState,
)
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import collect, send_text, stream_text
from a2a_labs_common.a2a.states import state_name

S = TaskState


async def states_of(c, text, **kw):  # type: ignore[no-untyped-def]
    evs = [e async for e in stream_text(c, text, **kw)]
    seq = [events.state_of(e) for e in evs if events.state_of(e)]
    return evs, seq


async def test_echo_is_a_single_message(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        r = await send_text(c, "echo hello", metadata={"k": "v"})
        assert r.kind == "message" and r.task is None
        assert "hello" in r.text and "'k': 'v'" in r.text


async def test_help_and_unknown_are_messages(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        for text in ("help", "bananas"):
            r = await send_text(c, text)
            assert r.kind == "message" and "Commands:" in r.text


async def test_work_streams_chunked_artifact(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground() as c:
        evs, seq = await states_of(c, "work 3")
        assert events.kind(evs[0]) == "task"  # first event is the Task
        assert seq[0] == "SUBMITTED" and seq[-1] == "COMPLETED" and "WORKING" in seq
        chunks = [e.artifact_update for e in evs if events.kind(e) == "artifact_update"]
        assert [(x.append, x.last_chunk) for x in chunks] == [
            (False, False),
            (True, False),
            (True, True),
        ]
        done = collect(evs)
        assert done.text == "step 1 of 3 done\nstep 2 of 3 done\nstep 3 of 3 done\n"


async def test_ask_resume_with_same_task_id(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        first = await send_text(c, "ask")
        assert first.state == S.TASK_STATE_INPUT_REQUIRED
        second = await send_text(c, "green", task_id=first.task_id, context_id=first.context_id)
        assert second.task_id == first.task_id
        assert second.state == S.TASK_STATE_COMPLETED and second.text == "You chose green"


@pytest.mark.parametrize(
    ("token", "state"), [("letmein", S.TASK_STATE_COMPLETED), ("wrong", S.TASK_STATE_FAILED)]
)
async def test_auth_resume(playground, token, state) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        first = await send_text(c, "auth")
        assert first.state == S.TASK_STATE_AUTH_REQUIRED
        r = await send_text(
            c, "token", data={"token": token}, task_id=first.task_id, context_id=first.context_id
        )
        assert r.state == state


async def test_fail_and_reject(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        assert (await send_text(c, "fail")).state == S.TASK_STATE_FAILED
        assert (await send_text(c, "reject")).state == S.TASK_STATE_REJECTED


async def test_parts_has_four_artifacts_with_each_oneof(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        task = (await send_text(c, "parts")).task
        by_name = {a.name: a.parts[0] for a in task.artifacts}
        assert list(by_name) == ["text-part", "image-part", "url-part", "data-part"]
        assert by_name["text-part"].WhichOneof("content") == "text"
        img = by_name["image-part"]
        assert img.WhichOneof("content") == "raw" and img.raw[:4] == b"\x89PNG"
        assert img.media_type == "image/png" and img.filename == "dot.png"
        assert by_name["url-part"].WhichOneof("content") == "url"
        data = by_name["data-part"]
        assert data.WhichOneof("content") == "data"
        assert data.data.struct_value["temperature"] == 21.5


async def test_multi_three_artifacts(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        task = (await send_text(c, "multi")).task
        assert {a.name for a in task.artifacts} == {"alpha", "beta", "gamma"}


async def test_refs_sees_referenced_task(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        first = await send_text(c, "work 1")
        r = await send_text(c, "refs", reference_task_ids=[first.task_id, "ghost"])
        assert f"{first.task_id}: COMPLETED" in r.text and "ghost: NOT FOUND" in r.text


async def test_cancel_slow_task_then_cancel_again(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        r = await send_text(c, "slow 30", return_immediately=True)
        assert r.state in (S.TASK_STATE_SUBMITTED, S.TASK_STATE_WORKING)
        canceled = await c.client.cancel_task(CancelTaskRequest(id=r.task_id))
        assert canceled.status.state == S.TASK_STATE_CANCELED
        with pytest.raises(Exception) as ei:
            await c.client.cancel_task(CancelTaskRequest(id=r.task_id))
        assert type(ei.value).__name__ == "TaskNotCancelableError"


async def test_get_task_history_length_and_not_found(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        r = await send_text(c, "work 2")
        none = await c.client.get_task(GetTaskRequest(id=r.task_id, history_length=0))
        some = await c.client.get_task(GetTaskRequest(id=r.task_id, history_length=10))
        assert len(none.history) == 0 and len(some.history) >= 1
        with pytest.raises(Exception) as ei:
            await c.client.get_task(GetTaskRequest(id="nope"))
        assert type(ei.value).__name__ == "TaskNotFoundError"


async def test_list_tasks_filters_and_pagination(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        ids = [(await send_text(c, "work 1")).task_id for _ in range(3)]
        await send_text(c, "fail")
        allp = await c.client.list_tasks(ListTasksRequest())
        assert len(allp.tasks) == 4
        done = await c.client.list_tasks(ListTasksRequest(status=S.TASK_STATE_COMPLETED))
        assert {t.id for t in done.tasks} == set(ids)
        seen, token = [], ""
        while True:
            page = await c.client.list_tasks(ListTasksRequest(page_size=2, page_token=token))
            seen += [t.id for t in page.tasks]
            token = page.next_page_token
            if not token:
                break
        assert len(seen) == 4 and len(set(seen)) == 4
        ctx = await c.client.list_tasks(ListTasksRequest(context_id=allp.tasks[0].context_id))
        assert len(ctx.tasks) == 1


async def test_same_context_new_task(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        a = await send_text(c, "work 1")
        b = await send_text(c, "work 1", context_id=a.context_id)
        assert b.context_id == a.context_id and b.task_id != a.task_id
        assert state_name(b.state) == "COMPLETED"
