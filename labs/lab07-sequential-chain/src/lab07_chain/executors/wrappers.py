"""Executor wrappers that adapt framework executors to the chain's Parts (spec lab07 §2).

* ``OutlineExecutor``: asks the planner's inner executor for JSON, then replaces its text with an
  ``outline`` artifact (DataPart + text rendering). Also asks the user when the topic is too
  vague, which is how a hop reaches INPUT_REQUIRED.
* ``DataAsTextExecutor``: framework executors read only the TEXT of a message. This folds the
  outline DataPart into the prompt, so the writer sees it.

# CUSTOM: A2A passes Parts; what a framework does with a DataPart is up to its executor.
"""

from __future__ import annotations

from typing import Any

from a2a.helpers import new_data_part, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Role, TaskState
from a2a_labs_common.a2a.relay import HoldingQueue
from google.protobuf.json_format import MessageToDict

from lab07_chain.outline import Outline, fallback_outline, is_vague, parse_outline, render


def _is_thought(part: Any) -> bool:
    md = part.metadata
    return bool(part.HasField("metadata") and "adk_thought" in md and md["adk_thought"])


def topic_of(context: RequestContext) -> str:
    """The user's topic so far: earlier user messages of this task plus the current one."""
    current = context.get_user_input()
    earlier = []
    if context.current_task is not None:
        earlier = [
            " ".join(p.text for p in m.parts if p.text)
            for m in context.current_task.history
            if m.role == Role.ROLE_USER
        ]
    texts = [t for t in earlier if t and t != current] + [current]
    return " ".join(texts).strip()


class OutlineExecutor(AgentExecutor):
    def __init__(self, inner: AgentExecutor) -> None:
        self.inner = inner

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message and context.task_id and context.context_id
        topic = topic_of(context)
        if is_vague(topic):
            from a2a.helpers import new_task_from_user_message

            if context.current_task is None:
                await event_queue.enqueue_event(new_task_from_user_message(context.message))
            u = TaskUpdater(event_queue, context.task_id, context.context_id)
            # A2A: a hop that needs more information pauses in INPUT_REQUIRED with its question.
            await u.requires_input(u.new_agent_message([new_text_part("Who is the audience?")]))
            return
        task = context.current_task
        if task is not None and task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            # ADK's executor assumes INPUT_REQUIRED came from one of ITS tool calls and demands a
            # function response. Ours came from this wrapper, so show it a task that is working.
            working = type(task)()
            working.CopyFrom(task)
            working.status.state = TaskState.TASK_STATE_WORKING
            context.current_task = working
        # The inner executor reads context.message: hand it the combined topic.
        if context.message.parts:
            context.message.parts[0].text = topic
        hold = HoldingQueue(event_queue, forward_artifacts=False)
        await self.inner.execute(context, hold)
        if hold.terminal is None or hold.terminal.status.state != TaskState.TASK_STATE_COMPLETED:
            if hold.terminal is not None:
                await event_queue.enqueue_event(hold.terminal)
            return
        text = "".join(p.text for p in hold.parts() if p.text and not _is_thought(p))
        outline: Outline = parse_outline(text, topic) if text else fallback_outline(topic)
        u = TaskUpdater(event_queue, context.task_id, context.context_id)
        await u.add_artifact(
            [new_data_part(outline.model_dump()), new_text_part(render(outline))],
            artifact_id="outline",
            name="outline",
        )
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self.inner.cancel(context, event_queue)


class DataAsTextExecutor(AgentExecutor):
    """Fold an outline DataPart into the text the inner (text-only) executor will read."""

    def __init__(self, inner: AgentExecutor) -> None:
        self.inner = inner

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        if msg is not None:
            data = next((MessageToDict(p.data) for p in msg.parts if p.HasField("data")), None)
            if isinstance(data, dict) and "sections" in data:
                text = context.get_user_input()
                lead = (
                    text
                    if text.startswith(("Revise", "Write the article"))
                    else "Write the article from this outline."
                )
                del msg.parts[:]
                msg.parts.add().text = f"{lead}\n\nOutline:\n{render(Outline.model_validate(data))}"
        await self.inner.execute(context, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self.inner.cancel(context, event_queue)


__all__ = ["DataAsTextExecutor", "OutlineExecutor", "topic_of"]
