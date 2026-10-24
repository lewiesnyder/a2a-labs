"""Calling a remote A2A agent from inside a tool, safely (spec lab02 §1.3, §1.4).

Pattern 2 helper shared by the LangChain and Strands tools. It enforces the rules every
"agent as tool" needs:

* never raise into the LLM loop: every outcome becomes a ``RemoteReply``;
* one remote ``context_id`` per (controller conversation, remote agent);
* INPUT_REQUIRED is remembered, so the next call continues the SAME remote task.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

import httpx
from a2a.types import AgentCard, TaskState
from google.protobuf.json_format import ParseDict

from a2a_labs_common.a2a.client import Connected, bridge_card_urls, connect, send_text
from a2a_labs_common.a2a.ids import derived_context_id
from a2a_labs_common.config import endpoint

logger = logging.getLogger(__name__)

Status = Literal["done", "needs_input", "needs_auth", "error"]


@dataclass
class RemoteReply:
    status: Status
    text: str
    task_id: str | None = None
    context_id: str | None = None

    def for_llm(self) -> str:
        """The one string a tool returns. The controller's LLM reads this and nothing else."""
        if self.status == "needs_input":
            return f"NEEDS_INPUT: {self.text} [task_id={self.task_id}]"
        if self.status == "needs_auth":
            return f"NEEDS_AUTH: {self.text} [task_id={self.task_id}]"
        if self.status == "error":
            return f"ERROR: {self.text}"
        return self.text


def fetch_card_sync(service_or_url: str) -> AgentCard:
    """Fetch an Agent Card at build time (tool schemas and ADK remote agents need it up front)."""
    base = service_or_url if "://" in service_or_url else endpoint(service_or_url)
    data = httpx.get(f"{base}/.well-known/agent-card.json", timeout=10).json()
    card = ParseDict(data, AgentCard(), ignore_unknown_fields=True)
    bridge_card_urls(card)  # CUSTOM: docker names -> localhost when this process runs on the host
    return card


def card_description(card: AgentCard) -> str:
    """Tool description derived from the Agent Card: the card IS the tool schema source."""
    skills = "; ".join(
        f"{s.name}: {s.description}" + (f" (e.g. {s.examples[0]!r})" if s.examples else "")
        for s in card.skills
    )
    return f"{card.name}. {card.description} Skills: {skills}".strip()


class RemoteAgent:
    def __init__(self, service_or_url: str) -> None:
        self.target = service_or_url
        self._conn: Connected | None = None
        self._lock = asyncio.Lock()
        # (scope, target) -> task_id of a remote task waiting for the user's answer
        self._pending: dict[tuple[str, str], str] = {}

    async def _connection(self) -> Connected:
        async with self._lock:
            if self._conn is None:
                self._conn = await connect(self.target, streaming=False)
            return self._conn

    async def card(self) -> AgentCard:
        return (await self._connection()).card

    def pending_task(self, scope: str) -> str | None:
        return self._pending.get((scope, self.target))

    async def ask(self, request: str, *, scope: str, task_id: str | None = None) -> RemoteReply:
        """Send ``request``; ``scope`` identifies the controller conversation."""
        key = (scope, self.target)
        context_id = derived_context_id(scope, self.target)
        task_id = task_id or self._pending.get(key)  # continue a task that asked for input
        try:
            conn = await self._connection()
            result = await send_text(conn, request, context_id=context_id, task_id=task_id)
        except Exception as exc:
            # A stale task_id (remote restarted, or already finished) must not wedge the tool.
            self._pending.pop(key, None)
            logger.warning("remote call to %s failed: %s", self.target, exc)
            return RemoteReply("error", f"{type(exc).__name__}: {exc}")
        if result.kind == "message":
            return RemoteReply("done", result.text, None, result.context_id)
        task = result.task
        assert task is not None
        status_text = ""
        if task.status.HasField("message") and task.status.message.parts:
            status_text = " ".join(p.text for p in task.status.message.parts if p.text)
        state = task.status.state
        if state == TaskState.TASK_STATE_COMPLETED:
            self._pending.pop(key, None)
            return RemoteReply("done", result.text, task.id, task.context_id)
        if state in (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED):
            self._pending[key] = task.id
            kind: Status = (
                "needs_input" if state == TaskState.TASK_STATE_INPUT_REQUIRED else "needs_auth"
            )
            return RemoteReply(kind, status_text, task.id, task.context_id)
        self._pending.pop(key, None)  # FAILED / REJECTED / CANCELED
        name = TaskState.Name(state).removeprefix("TASK_STATE_")
        return RemoteReply("error", f"remote task {name}: {status_text}".strip())

    async def aclose(self) -> None:
        if self._conn is not None:
            await self._conn.aclose()
            self._conn = None
