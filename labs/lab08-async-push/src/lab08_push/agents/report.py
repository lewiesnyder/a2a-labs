"""The report agent: six phases, each one a status update and an artifact chunk."""

from __future__ import annotations

import asyncio

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from a2a_labs_common.models import get_langchain_model

from lab08_push.agents.data import PHASES, seconds


class ReportExecutor(AgentExecutor):
    def __init__(self) -> None:
        self._running: dict[str, asyncio.Task[None]] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message and context.task_id and context.context_id
        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
        u = TaskUpdater(event_queue, context.task_id, context.context_id)
        self._running[context.task_id] = asyncio.current_task()  # type: ignore[assignment]
        try:
            await u.start_work()
            for i, phase in enumerate(PHASES, 1):
                await u.update_status(
                    TaskState.TASK_STATE_WORKING,
                    u.new_agent_message([new_text_part(f"phase {i}/{len(PHASES)}: {phase}")]),
                )
                await asyncio.sleep(seconds("PHASE_SECONDS", 5))
                text = f"{i}. {phase}: done\n"
                if i == len(PHASES):  # the last phase asks the quick model for a summary
                    prompt = "Write one sentence summarising a finished quarterly report."
                    reply = await get_langchain_model("quick").ainvoke(prompt)
                    text += f"Summary: {getattr(reply, 'content', reply)}\n"
                await u.add_artifact(
                    [new_text_part(text)],
                    artifact_id="report",
                    name="report",
                    append=i > 1,
                    last_chunk=i == len(PHASES),
                )
            await u.complete()
        finally:
            self._running.pop(context.task_id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        running = self._running.get(context.task_id)
        if running is not None:
            running.cancel()
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
