"""Relay mode (spec lab07 §4): each hop forwards its result to the next one, behind ONE task.

The client sends a message to the FIRST hop with the route in its metadata::

    {"relay": {"route": ["writer", "editor"], "origin_task": "<T1>", "hop": 0}}

``RelayExecutor`` wraps any ``AgentExecutor`` (ADK, Strands, LangGraph, plain SDK). It lets the
inner executor do its normal work, HOLDS BACK the inner terminal status, then calls the next hop
with the inner artifacts as parts and ``reference_task_ids=[own task]``, relays the downstream
progress as its own WORKING updates, attaches the downstream result as a ``downstream-final``
artifact and finally completes. Without a route it is a transparent pass-through.

# CUSTOM: A2A defines no relay or chaining protocol; this is a convention carried in metadata.
"""

from __future__ import annotations

import logging
from typing import Any

from a2a.helpers import new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict

from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import collect, connect, stream_text
from a2a_labs_common.a2a.ids import derived_context_id
from a2a_labs_common.a2a.states import TERMINAL

logger = logging.getLogger(__name__)
DOWNSTREAM_FINAL = "downstream-final"


def relay_meta(message: Message | None) -> dict[str, Any] | None:
    if message is None or not message.HasField("metadata"):
        return None
    found = MessageToDict(message.metadata).get("relay")
    return found if isinstance(found, dict) else None


class HoldingQueue(EventQueue):
    """Forwards everything except the terminal status, and remembers the artifacts."""

    def __init__(self, inner: EventQueue, *, forward_artifacts: bool = True) -> None:
        self._inner = inner
        self._forward_artifacts = forward_artifacts
        self.terminal: TaskStatusUpdateEvent | None = None
        self.artifacts: dict[str, Any] = {}

    async def enqueue_event(self, event: Any) -> None:
        if isinstance(event, TaskStatusUpdateEvent) and event.status.state in TERMINAL:
            self.terminal = event
            return
        if isinstance(event, TaskArtifactUpdateEvent):
            art = event.artifact
            if art.artifact_id in self.artifacts and event.append:
                self.artifacts[art.artifact_id].parts.extend(art.parts)
            else:
                self.artifacts[art.artifact_id] = type(art)()
                self.artifacts[art.artifact_id].CopyFrom(art)
            if not self._forward_artifacts:
                return
        await self._inner.enqueue_event(event)

    def parts(self) -> list[Part]:
        return [p for a in self.artifacts.values() for p in a.parts]


def parts_to_call(parts: list[Part]) -> tuple[str, dict[str, Any] | None]:
    """Text parts joined, first data part kept structured: what ``stream_text`` can send."""
    text = "\n".join(p.text for p in parts if p.text)
    data = next(
        (MessageToDict(p.data) for p in parts if p.HasField("data")),
        None,
    )
    return text, data if isinstance(data, dict) else None


class RelayExecutor(AgentExecutor):
    def __init__(self, inner: AgentExecutor, *, service: str) -> None:
        self.inner, self.service = inner, service

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        relay = relay_meta(context.message)
        if not relay or not relay.get("route"):
            await self.inner.execute(context, event_queue)
            return
        hold = HoldingQueue(event_queue)
        await self.inner.execute(context, hold)
        terminal = hold.terminal
        if terminal is None or terminal.status.state != TaskState.TASK_STATE_COMPLETED:
            if terminal is not None:  # failed or cancelled inside: report it unchanged
                await event_queue.enqueue_event(terminal)
            return
        assert context.task_id and context.context_id
        u = TaskUpdater(event_queue, context.task_id, context.context_id)
        route: list[str] = list(relay["route"])
        nxt, rest = route[0], route[1:]
        text, data = parts_to_call(hold.parts())
        meta = {
            "relay": {
                "route": rest,
                "origin_task": relay.get("origin_task") or context.task_id,
                "hop": int(relay.get("hop", 0)) + 1,
            }
        }
        await u.update_status(
            TaskState.TASK_STATE_WORKING,
            u.new_agent_message([new_text_part(f"↳ relaying to {nxt}")]),
        )
        try:
            async with await connect(nxt, streaming=True) as conn:
                evs = []
                async for ev in stream_text(
                    conn,
                    text,
                    data=data,
                    context_id=derived_context_id(context.context_id, nxt),
                    reference_task_ids=[context.task_id],
                    metadata=meta,
                ):
                    evs.append(ev)
                    if events.kind(ev) == "status_update" and ev.status_update.status.HasField(
                        "message"
                    ):
                        said = " ".join(p.text for p in ev.status_update.status.message.parts)
                        if said.strip():
                            await u.update_status(
                                TaskState.TASK_STATE_WORKING,
                                u.new_agent_message([new_text_part(f"↳ {nxt}: {said[:120]}")]),
                            )
                result = collect(evs)
        except Exception as exc:
            await u.failed(u.new_agent_message([new_text_part(f"hop {nxt} unreachable: {exc}")]))
            return
        if result.state != TaskState.TASK_STATE_COMPLETED:
            name = TaskState.Name(result.state).removeprefix("TASK_STATE_") if result.state else "?"
            await u.failed(u.new_agent_message([new_text_part(f"hop {nxt} ended {name}")]))
            return
        assert result.task is not None
        arts = {a.name: a for a in result.task.artifacts}
        chosen = [arts[DOWNSTREAM_FINAL]] if DOWNSTREAM_FINAL in arts else list(arts.values())
        await u.add_artifact(
            [p for a in chosen for p in a.parts],
            artifact_id=DOWNSTREAM_FINAL,
            name=DOWNSTREAM_FINAL,
            metadata={"from_hop": nxt, "downstream_task": result.task.id},
        )
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self.inner.cancel(context, event_queue)
