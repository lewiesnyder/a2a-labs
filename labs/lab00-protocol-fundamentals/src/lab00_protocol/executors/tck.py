"""TCK shim: answers the A2A TCK's ``messageId``-prefix scenarios, delegates everything else.

# CUSTOM: the official A2A TCK (a2aproject/a2a-tck) drives the agent under test through
# in-band ``messageId`` prefixes defined in its ``scenarios/*.feature`` files. No SDK
# construct provides that contract, so this wrapper implements it. Real lab commands
# (``work``, ``ask``, ...) never start with ``tck-`` and go straight to the inner executor.
"""

from __future__ import annotations

import asyncio
import os

from a2a.helpers import (
    new_data_part,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
    new_url_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Role, TaskState


class TckExecutor(AgentExecutor):
    def __init__(self, inner: AgentExecutor) -> None:
        self.inner = inner

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        mid = context.message.message_id
        if not mid.startswith(("tck-", "test-resubscribe-message-id")):
            await self.inner.execute(context, event_queue)
            return
        if mid.startswith("tck-message-response"):
            # A2A: a direct Message reply is the *only* event; no Task is created.
            await event_queue.enqueue_event(
                new_text_message(
                    "Direct message response", context_id=context.context_id, role=Role.ROLE_AGENT
                )
            )
            return

        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)  # A2A: first event is the Task
        u = TaskUpdater(event_queue, task.id, task.context_id)

        if mid.startswith("tck-input-required"):
            await u.update_status(TaskState.TASK_STATE_INPUT_REQUIRED)
        elif mid.startswith("tck-reject-task"):
            await u.reject(u.new_agent_message([new_text_part("rejected")]))
        elif mid.startswith("tck-complete-task"):
            await u.complete(u.new_agent_message([new_text_part("Hello from TCK")]))
        elif mid.startswith("tck-artifact-file-url"):
            await self._artifact(
                u,
                new_url_part(
                    "https://example.com/output.txt", media_type="text/plain", filename="output.txt"
                ),
            )
        elif mid.startswith("tck-artifact-file"):
            await self._artifact(u, _file_part())
        elif mid.startswith("tck-artifact-data"):
            await self._artifact(u, new_data_part({"key": "value", "count": 42}))
        elif mid.startswith("tck-artifact-text"):
            await self._artifact(u, new_text_part("Generated text content"))
        elif mid.startswith("tck-stream-artifact-chunked"):
            await u.start_work()
            await u.add_artifact(
                [new_text_part("chunk-1 ")], artifact_id="chunked", append=False, last_chunk=False
            )
            await u.add_artifact(
                [new_text_part("chunk-2")], artifact_id="chunked", append=True, last_chunk=True
            )
            await u.complete()
        elif mid.startswith("tck-stream-artifact-file"):
            await self._streamed(u, _file_part())
        elif mid.startswith("tck-stream-"):  # 001, 002, 003, ordering-001, artifact-text
            await self._streamed(
                u, new_text_part(_STREAM_TEXT.get(mid.split("-")[2], "Ordered output"))
            )
        elif mid.startswith("test-resubscribe-message-id"):
            await u.start_work()
            # Keep the task alive >= 2 x TCK_STREAMING_TIMEOUT so the TCK can resubscribe.
            await asyncio.sleep(2 * float(os.environ.get("TCK_STREAMING_TIMEOUT", "2.0")) + 0.5)
            await u.complete()
        else:  # any other tck-* prefix: plain completion
            await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self.inner.cancel(context, event_queue)

    @staticmethod
    async def _artifact(u: TaskUpdater, part: Part) -> None:
        await u.add_artifact([part], artifact_id="tck-artifact", name="tck-artifact")
        await u.complete()

    @staticmethod
    async def _streamed(u: TaskUpdater, part: Part) -> None:
        await u.start_work()
        await u.add_artifact([part], artifact_id="tck-artifact", name="tck-artifact")
        await u.complete()


_STREAM_TEXT = {"001": "Stream hello from TCK", "003": "Stream task lifecycle"}


def _file_part() -> Part:
    return Part(raw=b"file content", filename="output.txt", media_type="text/plain")
