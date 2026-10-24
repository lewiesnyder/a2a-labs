"""Strands agent exposed through an a2a-sdk 1.x ``AgentExecutor``.

Pattern: any framework agent behind the standard SDK server (spec 00 D11, deviation DEV-1).
Implements: spec 00 D11 as amended by docs/spec-deviations.md DEV-1.

# CUSTOM: strands.multiagent.a2a.A2AServer imports a2a-sdk 0.3 types (DataPart) and cannot be
# imported next to a2a-sdk 1.x (strands-agents 1.62.0). This executor reproduces its useful
# behaviour (agent_factory per context, LRU eviction, chunked artifact streaming) on the
# official 1.x server stack. Remove when Strands ships 1.x support.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater

logger = logging.getLogger(__name__)


class StrandsLikeAgent(Protocol):
    """The slice of ``strands.Agent`` this executor needs."""

    def stream_async(self, prompt: str, **kwargs: Any) -> AsyncIterator[Any]: ...


AgentFactory = Callable[[str], StrandsLikeAgent]


class StrandsA2AExecutor(AgentExecutor):
    def __init__(self, agent_factory: AgentFactory, *, max_contexts: int = 32) -> None:
        self._factory = agent_factory
        self._max = max_contexts
        # One agent per contextId keeps conversations separate (multi-tenant safe).
        self._agents: OrderedDict[str, StrandsLikeAgent] = OrderedDict()

    def _agent_for(self, context_id: str) -> StrandsLikeAgent:
        if context_id in self._agents:
            self._agents.move_to_end(context_id)
        else:
            self._agents[context_id] = self._factory(context_id)
            while len(self._agents) > self._max:
                self._agents.popitem(last=False)
        return self._agents[context_id]

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None and context.task_id and context.context_id
        # A2A: first event must be the Task (task pattern) so clients can track it.
        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()

        agent = self._agent_for(context.context_id)
        artifact_id = str(uuid.uuid4())
        streamed = False
        try:
            async for event in agent.stream_async(context.get_user_input()):
                chunk = event.get("data") if isinstance(event, dict) else None
                if not chunk:
                    continue
                # A2A: results go in Artifacts, streamed with append=True chunks.
                await updater.add_artifact(
                    [new_text_part(chunk)],
                    artifact_id=artifact_id,
                    name="response",
                    append=streamed,
                    last_chunk=False,
                )
                streamed = True
        except Exception as exc:
            logger.exception("strands agent failed")
            await updater.failed(updater.new_agent_message([new_text_part(f"Agent error: {exc}")]))
            return
        if streamed:
            await updater.add_artifact(
                [], artifact_id=artifact_id, name="response", append=True, last_chunk=True
            )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        agent = self._agents.get(context.context_id)
        if agent is not None and hasattr(agent, "cancel"):
            agent.cancel()
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

