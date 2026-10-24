"""The canonical LangGraph <-> A2A bridge, shared by Labs 01, 02, 04, 05, 07 and 08.

Pattern: any compiled LangGraph graph behind the standard a2a-sdk server.
Implements: spec lab01 §2.3.

# CUSTOM: the SDK ships no LangGraph adapter, and the LangGraph Agent Server's own A2A endpoint
# lacks SubscribeToTask and push configs (D9, pitfall LG-3). This executor keeps a2a-sdk in charge
# of the protocol and uses LangGraph only to run the graph.

Mapping (A2A -> LangGraph):
* ``context_id``                  -> ``thread_id`` (checkpointer), so multi-turn memory works
* ``interrupt(...)`` in the graph -> ``TASK_STATE_INPUT_REQUIRED`` (the value is the status text)
* a follow-up message, same task  -> ``Command(resume=text)``
* final AI message                -> an ``answer`` Artifact, then COMPLETED
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langgraph.types import Command

logger = logging.getLogger(__name__)


def message_text(msg: BaseMessage | Any) -> str:
    """Plain text of a LangChain message (content may be a string or a list of blocks)."""
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b if isinstance(b, str) else str(b.get("text", "")) for b in content if b)
    return str(content)


class LangGraphA2AExecutor(AgentExecutor):
    def __init__(
        self,
        graph: Any = None,
        *,
        agent_name: str,
        graph_factory: Callable[[], Awaitable[Any]] | None = None,
        output_key: str = "messages",
        interrupt_to_input_required: bool = True,
        stream_tokens: bool | None = None,
    ) -> None:
        if graph is None and graph_factory is None:
            raise ValueError("pass a compiled graph or an async graph_factory")
        self._graph = graph
        # For graphs whose tools load asynchronously (MCP): built on first use, then cached.
        self._graph_factory = graph_factory
        self._graph_lock = asyncio.Lock()
        self.agent_name = agent_name
        self.output_key = output_key
        self.interrupt_to_input_required = interrupt_to_input_required
        # Partial AI tokens as appended artifact chunks (spec: STREAM_TOKENS_AS_ARTIFACT=true).
        self.stream_tokens = (
            os.environ.get("STREAM_TOKENS_AS_ARTIFACT", "false").lower() == "true"
            if stream_tokens is None
            else stream_tokens
        )
        self._running: dict[str, asyncio.Task[Any]] = {}

    # ── helpers ────────────────────────────────────────────────────────────────────────────
    async def _ensure_graph(self) -> Any:
        if self._graph is None:
            async with self._graph_lock:
                if self._graph is None:
                    assert self._graph_factory is not None
                    self._graph = await self._graph_factory()
        return self._graph

    @property
    def graph(self) -> Any:
        return self._graph

    async def _is_resuming(self, config: dict[str, Any]) -> bool:
        """LangGraph is mid-interrupt when its state has pending ``next`` nodes with interrupts."""
        state = await (await self._ensure_graph()).aget_state(config)
        return bool(state.next) and any(t.interrupts for t in state.tasks)

    @staticmethod
    def _interrupt_text(chunk: dict[str, Any]) -> str:
        interrupts = chunk["__interrupt__"]
        first = (
            interrupts[0] if isinstance(interrupts, (list, tuple)) and interrupts else interrupts
        )
        value = getattr(first, "value", first)
        return value if isinstance(value, str) else str(value)

    # ── AgentExecutor ──────────────────────────────────────────────────────────────────────
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)  # A2A: the Task is always the first event
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        current = asyncio.current_task()
        if current is not None:
            self._running[task.id] = current
        try:
            await self._run(updater, context, task.context_id)
        except asyncio.CancelledError:
            raise  # the framework (or cancel()) is stopping us; cancel() emits CANCELED
        except Exception as exc:
            logger.exception("langgraph run failed")
            await updater.failed(updater.new_agent_message([new_text_part(f"Agent error: {exc}")]))
        finally:
            self._running.pop(task.id, None)

    async def _run(self, u: TaskUpdater, context: RequestContext, context_id: str) -> None:
        text = context.get_user_input()
        # A2A context_id -> LangGraph thread_id: the checkpointer keeps the conversation.
        config = {"configurable": {"thread_id": context_id}}
        resuming = await self._is_resuming(config)
        inputs: Any = Command(resume=text) if resuming else {"messages": [("user", text)]}
        await u.start_work()

        final = ""
        artifact_id = f"{self.agent_name}-answer"
        streamed = False
        # NEVER graph.stream() here: a sync iterator blocks the event loop and stalls SSE (LG-1).
        graph = await self._ensure_graph()
        async for mode, chunk in graph.astream(inputs, config, stream_mode=["updates", "messages"]):
            if mode == "messages":
                msg, _meta = chunk
                token = message_text(msg) if isinstance(msg, AIMessageChunk) else ""
                if self.stream_tokens and token and not getattr(msg, "tool_call_chunks", None):
                    await u.add_artifact(
                        [new_text_part(token)],
                        artifact_id=artifact_id,
                        name="answer",
                        append=streamed,
                        last_chunk=False,
                    )
                    streamed = True
                continue
            if "__interrupt__" in chunk and self.interrupt_to_input_required:
                # A2A: the graph needs the user -> INTERRUPTED state; client resumes same task_id.
                await u.requires_input(
                    u.new_agent_message([new_text_part(self._interrupt_text(chunk))])
                )
                return
            for node, update in chunk.items():
                if node == "__interrupt__" or not isinstance(update, dict):
                    continue
                for msg in update.get("messages", []):
                    if not isinstance(msg, AIMessage):
                        continue
                    if msg.tool_calls:
                        names = ", ".join(c["name"] for c in msg.tool_calls)
                        # A2A: a WORKING status message is informational progress, not a result.
                        await u.update_status(
                            TaskState.TASK_STATE_WORKING,
                            u.new_agent_message([new_text_part(f"Calling tool {names}")]),
                        )
                    else:
                        final = message_text(msg)
        if self.output_key != "messages":
            state = await graph.aget_state(config)
            final = str(state.values.get(self.output_key, final))
        if streamed:
            await u.add_artifact(
                [], artifact_id=artifact_id, name="answer", append=True, last_chunk=True
            )
        elif final:
            await u.add_artifact([new_text_part(final)], artifact_id=artifact_id, name="answer")
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        running = self._running.get(context.task_id)
        if running is not None:
            running.cancel()
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
