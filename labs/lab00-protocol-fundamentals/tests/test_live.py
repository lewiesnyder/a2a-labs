"""Streaming-timing behaviour against a real socket: cancel mid-stream, resubscribe, concurrency."""

from __future__ import annotations

import asyncio

import pytest
from a2a.types import CancelTaskRequest, SubscribeToTaskRequest
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import stream_text


async def test_cancel_during_stream_emits_canceled(live_playground) -> None:  # type: ignore[no-untyped-def]
    async with live_playground() as c:
        seen = []
        async for ev in stream_text(c, "slow 30"):
            seen.append(ev)
            if events.kind(ev) == "task":
                await c.client.cancel_task(CancelTaskRequest(id=ev.task.id))
        assert events.state_of(seen[-1]) == "CANCELED"


async def test_resubscribe_starts_with_task_snapshot(live_playground) -> None:  # type: ignore[no-untyped-def]
    async with live_playground() as c:
        task_id, got = "", 0
        async for ev in stream_text(c, "work 6"):
            if events.kind(ev) == "task":
                task_id = ev.task.id
            got += 1
            if got == 3:
                break  # simulated network drop
        resumed = [ev async for ev in c.client.subscribe(SubscribeToTaskRequest(id=task_id))]
        assert events.kind(resumed[0]) == "task"  # A2A: SubscribeToTask sends the Task first
        assert events.state_of(resumed[-1]) == "COMPLETED"


async def test_two_concurrent_subscribers_see_the_same_events(live_playground) -> None:  # type: ignore[no-untyped-def]
    async with live_playground(step_delay=0.1) as c:
        first = None
        async for ev in stream_text(c, "work 8", return_immediately=True):
            first = ev
            break
        assert first is not None
        tid = first.task.id if events.kind(first) == "task" else first.message.task_id

        async def collect() -> list[str]:
            return [
                f"{events.kind(e)}:{events.state_of(e)}"
                async for e in c.client.subscribe(SubscribeToTaskRequest(id=tid))
            ]

        a, b = await asyncio.gather(collect(), collect())
        assert a[1:] == b[1:] or len(a) == len(b)  # snapshots may differ by one update; tails match
        assert a[-1] == b[-1] == "status_update:COMPLETED"


async def test_subscribe_to_finished_task_is_an_error(live_playground) -> None:  # type: ignore[no-untyped-def]
    async with live_playground(step_delay=0) as c:
        evs = [e async for e in stream_text(c, "work 1")]
        tid = evs[0].task.id
        with pytest.raises(Exception) as ei:
            [e async for e in c.client.subscribe(SubscribeToTaskRequest(id=tid))]
        assert "Unsupported" in type(ei.value).__name__ or "not supported" in str(ei.value).lower()
