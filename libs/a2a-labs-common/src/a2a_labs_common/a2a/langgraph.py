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
from dataclasses import dataclass
from typing import Any

from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import GetTaskRequest, TaskState
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.types import Command, interrupt

from a2a_labs_common.a2a.client import SendResult, collect, connect, stream_text
from a2a_labs_common.a2a.ids import derived_context_id

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
        artifact_name: str = "answer",
        emit_node_updates: bool = False,
        snapshot_artifact: bool = False,
        extra_artifacts: Callable[[dict[str, Any]], list[tuple[str, list[Any]]]] | None = None,
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
        # Pattern 3 mapping options (Lab 04), all off by default so Labs 01-03 are unchanged:
        self.artifact_name = artifact_name  # name of the final artifact, e.g. "itinerary"
        self.emit_node_updates = emit_node_updates  # WORKING "▶ node: x" + langgraph_node metadata
        self.snapshot_artifact = snapshot_artifact  # a `state-snapshot` DataPart after each node
        # Called with the final graph state; each (name, parts) becomes one more artifact, so a
        # graph can return several results (Lab 07's editor: `final` text + `review` data).
        self.extra_artifacts = extra_artifacts
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
    def _interrupt_value(chunk: dict[str, Any]) -> Any:
        interrupts = chunk["__interrupt__"]
        first = (
            interrupts[0] if isinstance(interrupts, (list, tuple)) and interrupts else interrupts
        )
        return getattr(first, "value", first)

    @classmethod
    def _interrupt_parts(cls, chunk: dict[str, Any]) -> list[Any]:
        """The interrupt payload as A2A parts: its question as text, a dict also as a DataPart."""
        value = cls._interrupt_value(chunk)
        if isinstance(value, str):
            return [new_text_part(value)]
        if isinstance(value, dict):
            question = str(value.get("question", value))
            return [new_text_part(question), new_data_part(jsonable(value))]
        return [new_text_part(str(value))]

    @staticmethod
    def _resume_value(context: RequestContext) -> Any:
        """The client's resume value: a DataPart (its ``resume`` key or whole dict), else text."""
        if context.message is not None:
            data = get_data_parts(context.message.parts)
            if data:
                first = data[0]
                return first["resume"] if isinstance(first, dict) and "resume" in first else first
        return context.get_user_input()

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
        inputs: Any = (
            Command(resume=self._resume_value(context))
            if resuming
            else {"messages": [("user", text)]}
        )
        await u.start_work()

        final = ""
        artifact_id = f"{self.agent_name}-{self.artifact_name}"
        streamed = False
        # NEVER graph.stream() here: a sync iterator blocks the event loop and stalls SSE (LG-1).
        graph = await self._ensure_graph()
        modes = ["updates", "messages"] + (["tasks"] if self.emit_node_updates else [])
        async for mode, chunk in graph.astream(inputs, config, stream_mode=modes):
            if mode == "tasks":
                # A task START event has an `input`; the finish event has `result`/`error`.
                if "input" in chunk and not str(chunk["name"]).startswith("__"):
                    await u.update_status(
                        TaskState.TASK_STATE_WORKING,
                        u.new_agent_message([new_text_part(f"▶ node: {chunk['name']}")]),
                        metadata={"langgraph_node": chunk["name"]},
                    )
                continue
            if mode == "messages":
                msg, _meta = chunk
                token = message_text(msg) if isinstance(msg, AIMessageChunk) else ""
                if self.stream_tokens and token and not getattr(msg, "tool_call_chunks", None):
                    await u.add_artifact(
                        [new_text_part(token)],
                        artifact_id=artifact_id,
                        name=self.artifact_name,
                        append=streamed,
                        last_chunk=False,
                    )
                    streamed = True
                continue
            if "__interrupt__" in chunk and self.interrupt_to_input_required:
                # A2A: the graph needs the user -> INTERRUPTED state; client resumes same task_id.
                await u.requires_input(u.new_agent_message(self._interrupt_parts(chunk)))
                return
            for node, update in chunk.items():
                if node == "__interrupt__" or not isinstance(update, dict):
                    continue
                for msg in update.get("messages", []):
                    if not isinstance(msg, AIMessage):
                        continue
                    if msg.tool_calls:
                        names = ", ".join(c["name"] for c in msg.tool_calls)
                        label = (
                            f"🔧 tool: {names}"
                            if self.emit_node_updates
                            else f"Calling tool {names}"
                        )
                        # A2A: a WORKING status message is informational progress, not a result.
                        await u.update_status(
                            TaskState.TASK_STATE_WORKING,
                            u.new_agent_message([new_text_part(label)]),
                            metadata={"langgraph_node": node},
                        )
                    else:
                        final = message_text(msg)
            if self.snapshot_artifact:
                values = (await graph.aget_state(config)).values
                snap = jsonable({k: v for k, v in values.items() if k != "messages"})
                # A2A: append=False replaces the previous snapshot, so the artifact stays current.
                await u.add_artifact(
                    [new_data_part(snap)],
                    artifact_id=f"{self.agent_name}-state",
                    name="state-snapshot",
                    append=False,
                )
        if self.output_key != "messages":
            state = await graph.aget_state(config)
            final = str(state.values.get(self.output_key, final))
        if streamed:
            await u.add_artifact(
                [], artifact_id=artifact_id, name=self.artifact_name, append=True, last_chunk=True
            )
        elif final:
            await u.add_artifact(
                [new_text_part(final)], artifact_id=artifact_id, name=self.artifact_name
            )
        if self.extra_artifacts is not None:
            values = (await graph.aget_state(config)).values
            for name, parts in self.extra_artifacts(values):
                await u.add_artifact(parts, artifact_id=f"{self.agent_name}-{name}", name=name)
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        running = self._running.get(context.task_id)
        if running is not None:
            running.cancel()
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def jsonable(value: Any) -> Any:
    """Make graph state safe for a DataPart: models and unknown objects become plain data."""
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ── A LangGraph node that calls an A2A agent ────────────────────────────────────────────────
@dataclass
class _Session:
    """One conversation with one remote agent, as seen from one graph thread."""

    context_id: str
    task_id: str | None = None
    forwarded: int = 0  # answers already sent to the remote task (== interrupts to replay)


class RemoteA2ANode:
    """A LangGraph node that delegates to a remote A2A agent (spec lab04 §2.2).

    * Reads its request from state via ``input_fn(state) -> str``.
    * Streams the remote task and appends its status texts to ``state[progress_key]``.
    * On remote INPUT_REQUIRED it calls a LOCAL ``interrupt()`` carrying the remote question, then
      forwards the answer to the SAME remote task and keeps going. Interrupts therefore propagate
      across the A2A boundary: remote pause -> local pause -> the end client's INPUT_REQUIRED.
    * On COMPLETED it writes the artifact text to ``state[output_key]``; on FAILED, REJECTED or
      CANCELED it writes ``state["error"]`` (route on it with a conditional edge).
    * Records ``{context_id, task_id, state}`` per remote in ``state["remote_tasks"][service]``.

    LangGraph re-runs a node from its first line after an interrupt, which would send the remote
    request again. So the node remembers, per (thread, remote), the remote ``task_id`` and how many
    answers it has forwarded; on re-entry it replays that many ``interrupt()`` calls (they return
    instantly), then reads the remote task's CURRENT state instead of re-sending. This memory lives
    in the process (like a ``MemorySaver``); a durable variant would persist it in graph state.

    # CUSTOM: the SDK has no LangGraph integration. Uses only a2a-sdk client calls.
    """

    def __init__(
        self,
        service: str,
        *,
        input_fn: Callable[[Any], str],
        output_key: str,
        streaming: bool = True,
        progress_key: str = "progress",
        remote_key: str = "remote_tasks",
        on_update: Callable[[str, str, str, str | None, str], None] | None = None,
    ) -> None:
        self.service = service
        # Called as ``on_update(thread, service, context_id, task_id, state_name)`` after every
        # remote reply, so a caller (the Lab 05 delegation ledger) sees a task the moment it
        # pauses, not only when the node finishes.
        self.on_update = on_update
        self.input_fn = input_fn
        self.output_key = output_key
        self.streaming = streaming
        self.progress_key = progress_key
        self.remote_key = remote_key
        self._sessions: dict[tuple[str, str], _Session] = {}
        self._conn: Any = None
        self._lock = asyncio.Lock()

    async def _connection(self) -> Any:
        async with self._lock:
            if self._conn is None:
                self._conn = await connect(self.service, streaming=self.streaming)
            return self._conn

    async def _send(
        self, thread: str, text: str, sess: _Session, task_id: str | None, progress: list[str]
    ) -> Any:
        conn = await self._connection()
        events = [
            e async for e in stream_text(conn, text, context_id=sess.context_id, task_id=task_id)
        ]
        for ev in events:  # the remote's progress, kept in graph state
            if ev.WhichOneof("payload") == "status_update":
                st = ev.status_update.status
                if st.HasField("message"):
                    msg = " ".join(p.text for p in st.message.parts if p.text)
                    if msg:
                        progress.append(f"[{self.service}] {msg}")
        result = collect(events)
        sess.task_id = result.task_id or sess.task_id
        if self.on_update is not None:
            self.on_update(
                thread,
                self.service,
                sess.context_id,
                sess.task_id,
                TaskState.Name(result.state).removeprefix("TASK_STATE_")
                if result.state
                else "COMPLETED",
            )
        return result

    @staticmethod
    def _status_text(result: Any) -> str:
        task = result.task
        if task is not None and task.status.HasField("message"):
            return " ".join(p.text for p in task.status.message.parts if p.text)
        return ""

    # `config` must be annotated exactly `RunnableConfig`: LangGraph injects the run config only
    # for that annotation (the same trap as in the LangChain tool).
    async def __call__(self, state: Any, config: RunnableConfig) -> dict[str, Any]:
        thread = str((config.get("configurable") or {}).get("thread_id", "default"))
        key = (thread, self.service)
        progress: list[str] = []
        remembered = self._sessions.get(key)
        try:
            if remembered is None:  # first run of this node for this conversation
                sess = self._sessions[key] = _Session(derived_context_id(thread, self.service))
                result = await self._send(thread, self.input_fn(state), sess, None, progress)
            else:  # re-run after an interrupt: do NOT re-send the request
                sess = remembered
                for _ in range(sess.forwarded):
                    interrupt({"remote": self.service, "replay": True})  # returns the old answer
                conn = await self._connection()
                task = await conn.client.get_task(GetTaskRequest(id=sess.task_id))
                result = SendResult("task", None, task)
            while result.state == TaskState.TASK_STATE_INPUT_REQUIRED:
                question = self._status_text(result)
                value = interrupt(
                    {"remote": self.service, "question": question, "task_id": sess.task_id}
                )
                sess.forwarded += 1
                answer = (
                    value
                    if isinstance(value, str)
                    else str(value.get("resume", value) if isinstance(value, dict) else value)
                )
                result = await self._send(thread, answer, sess, sess.task_id, progress)
        except GraphInterrupt:
            raise  # a pause, not a failure: LangGraph catches it and checkpoints
        except Exception as exc:
            self._sessions.pop(key, None)
            logger.warning("remote node %s failed: %s", self.service, exc)
            return {
                self.output_key: None,
                "error": f"{self.service}: {type(exc).__name__}: {exc}",
                self.progress_key: progress,
            }

        self._sessions.pop(key, None)
        state_name_ = (
            TaskState.Name(result.state).removeprefix("TASK_STATE_")
            if result.state
            else "COMPLETED"
        )
        tracked = {
            **(state.get(self.remote_key) or {}),
            self.service: {
                "context_id": sess.context_id,
                "task_id": result.task_id,
                "state": state_name_,
            },
        }
        out: dict[str, Any] = {self.progress_key: progress, self.remote_key: tracked, "error": None}
        if result.state in (None, TaskState.TASK_STATE_COMPLETED):
            out[self.output_key] = result.text
        else:
            out[self.output_key] = None
            out["error"] = (
                f"{self.service} ended {state_name_}: {self._status_text(result)}".strip()
            )
        return out
