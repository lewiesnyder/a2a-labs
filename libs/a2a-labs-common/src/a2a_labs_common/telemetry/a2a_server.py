"""Server-side tracing: ``TracedExecutor`` wraps any ``AgentExecutor`` (spec 05 §3.2).

# CUSTOM: the SDK ships no task-state tracing; this wraps AgentExecutor and taps EventQueue.

``EventQueue`` is a one-method ABC in a2a-sdk 1.x, so the tap simply forwards
``enqueue_event`` after observing the event [VERIFY resolved].
"""

from __future__ import annotations

import logging
import time

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import Event, EventQueue
from a2a.types import Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from a2a_labs_common.a2a.states import state_name
from a2a_labs_common.telemetry.metrics import instruments

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("a2a_labs_common.server")


class EventQueueTap(EventQueue):
    """Forwards every event unchanged while recording state changes and artifact chunks."""

    def __init__(self, inner: EventQueue, span: trace.Span, agent_name: str) -> None:
        self._inner, self._span, self._agent = inner, span, agent_name
        self.state: str | None = None
        self.chunks: dict[str, int] = {}

    async def enqueue_event(self, event: Event) -> None:
        self._observe(event)
        await self._inner.enqueue_event(event)

    def _observe(self, event: Event) -> None:
        if isinstance(event, Task):
            self._transition(state_name(event.status.state), event.id, event.context_id, None)
        elif isinstance(event, TaskStatusUpdateEvent):
            text = ""
            if event.status.HasField("message") and event.status.message.parts:
                text = event.status.message.parts[0].text
            self._transition(state_name(event.status.state), event.task_id, event.context_id, text)
        elif isinstance(event, TaskArtifactUpdateEvent):
            aid = event.artifact.artifact_id
            self.chunks[aid] = self.chunks.get(aid, 0) + 1
            size = sum(len(p.text) + len(p.raw) for p in event.artifact.parts)
            self._span.add_event(
                "a2a.artifact.chunk",
                {
                    "artifact_id": aid,
                    "append": event.append,
                    "last_chunk": event.last_chunk,
                    "bytes": size,
                },
            )
            instruments().artifact_chunks.add(1, {"a2a.agent.name": self._agent})
            if event.last_chunk:
                logger.info("artifact emitted id=%s chunks=%d", aid, self.chunks[aid])

    def _transition(self, to: str, task_id: str, context_id: str, message: str | None) -> None:
        prev = self.state
        if prev == to:
            return
        self.state = to
        attrs = {"from": prev or "NONE", "to": to}
        if message:
            attrs["message"] = message[:200]
        self._span.add_event("a2a.task.state_change", attrs)
        self._span.set_attributes({"a2a.task.id": task_id, "a2a.context.id": context_id})
        instruments().transitions.add(
            1, {"from": prev or "NONE", "to": to, "a2a.agent.name": self._agent}
        )
        if prev is None:
            logger.info("task created id=%s context=%s", task_id, context_id)
        logger.info("task state id=%s %s→%s", task_id, prev or "NONE", to)


class TracedExecutor(AgentExecutor):
    def __init__(self, inner: AgentExecutor, agent_name: str) -> None:
        self.inner, self.agent_name = inner, agent_name

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        attrs: dict[str, str | int] = {
            "a2a.task.id": context.task_id or "",
            "a2a.context.id": context.context_id or "",
            "a2a.agent.name": self.agent_name,
            "a2a.user_input.chars": len(context.get_user_input()) if context.message else 0,
        }
        instruments().tasks_started.add(1, {"a2a.agent.name": self.agent_name})
        start = time.perf_counter()
        with _tracer.start_as_current_span(
            "a2a.server.execute", kind=SpanKind.INTERNAL, attributes=attrs
        ) as span:
            tap = EventQueueTap(event_queue, span, self.agent_name)
            try:
                await self.inner.execute(context, tap)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                logger.exception("executor failed")
                raise
            finally:
                final = tap.state or "NONE"
                span.set_attribute("a2a.task.final_state", final)
                instruments().task_duration.record(
                    time.perf_counter() - start,
                    {"a2a.agent.name": self.agent_name, "a2a.task.final_state": final},
                )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        with _tracer.start_as_current_span(
            "a2a.server.cancel", attributes={"a2a.task.id": context.task_id or ""}
        ) as span:
            tap = EventQueueTap(event_queue, span, self.agent_name)
            await self.inner.cancel(context, tap)
            logger.info("task canceled id=%s", context.task_id)
