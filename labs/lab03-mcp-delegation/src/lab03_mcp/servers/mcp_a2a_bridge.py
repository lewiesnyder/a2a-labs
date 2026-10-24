"""A2A behind MCP: an MCP server that exposes remote A2A agents as MCP tools.

Why: an MCP-only host (an IDE, a desktop assistant, a framework with MCP support) can then
delegate to A2A agents. The price: MCP has tools and results, not tasks. Task states, streaming
and multi-turn collapse into one tool result, so the bridge returns ``task_id`` and
``context_id`` in every result and adds ``get_task`` / ``cancel_task`` tools to compensate.

Only official constructs are used: FastMCP (streamable HTTP) and the a2a-sdk client.
Implements: spec lab03 §3. Configuration: ``BRIDGE_AGENTS=service-or-url,service-or-url``.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from a2a.types import AgentCard, CancelTaskRequest, GetTaskRequest, TaskState
from a2a_labs_common.a2a.client import (
    Connected,
    SendResult,
    collect,
    connect,
    result_text,
    send_text,
    stream_text,
)
from a2a_labs_common.a2a.remote import card_description, fetch_card_sync
from a2a_labs_common.a2a.states import state_name
from google.protobuf.json_format import MessageToJson
from mcp.server.fastmcp import Context, FastMCP

from lab03_mcp import mcp_app

logger = logging.getLogger(__name__)
DEFAULT_AGENTS = "lab03-strands-specialist,lab03-adk-specialist"

# A2A task state -> the one word an MCP client sees.
STATUS = {
    TaskState.TASK_STATE_SUBMITTED: "submitted",
    TaskState.TASK_STATE_WORKING: "working",
    TaskState.TASK_STATE_COMPLETED: "completed",
    TaskState.TASK_STATE_INPUT_REQUIRED: "input_required",
    TaskState.TASK_STATE_AUTH_REQUIRED: "auth_required",
    TaskState.TASK_STATE_FAILED: "failed",
    TaskState.TASK_STATE_REJECTED: "rejected",
    TaskState.TASK_STATE_CANCELED: "canceled",
}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _status_text(task: Any) -> str:
    if task.status.HasField("message") and task.status.message.parts:
        return " ".join(p.text for p in task.status.message.parts if p.text)
    return ""


def summarize(result: SendResult) -> dict[str, Any]:
    """Collapse a SendResult into the flat dict an MCP tool returns (this is where A2A is lossy)."""
    if result.kind == "message":
        return {
            "status": "completed",
            "text": result.text,
            "task_id": None,
            "context_id": result.context_id,
            "artifacts": [],
        }
    task = result.task
    assert task is not None
    status = STATUS.get(task.status.state, state_name(task.status.state).lower())
    artifacts = [
        {"name": a.name or a.artifact_id, "text": "".join(p.text for p in a.parts if p.text)}
        for a in task.artifacts
    ]
    # Results live in artifacts; questions and errors live in the status message.
    text = result_text(task) if status == "completed" else _status_text(task) or result_text(task)
    return {
        "status": status,
        "text": text,
        "task_id": task.id,
        "context_id": task.context_id,
        "artifacts": artifacts,
    }


@dataclass
class BridgeAgent:
    slug: str
    target: str
    card: AgentCard
    _streaming: Connected | None = field(default=None, repr=False)
    _blocking: Connected | None = field(default=None, repr=False)

    @property
    def tool_name(self) -> str:
        return f"ask_{self.slug}"

    async def streaming(self) -> Connected:
        if self._streaming is None:
            self._streaming = await connect(self.target, streaming=self.card.capabilities.streaming)
        return self._streaming

    async def blocking(self) -> Connected:
        if self._blocking is None:
            self._blocking = await connect(self.target, streaming=False)
        return self._blocking


def resolve_agents(targets: list[str], *, wait_s: float = 60) -> dict[str, BridgeAgent]:
    """Fetch every Agent Card; retry while an agent is still starting."""
    agents: dict[str, BridgeAgent] = {}
    for target in targets:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                card = fetch_card_sync(target)
                break
            except Exception as exc:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"agent card for {target} unreachable: {exc}") from exc
                time.sleep(1)
        slug = slugify(card.name if "://" in target else target)
        agents[slug] = BridgeAgent(slug, target, card)
    return agents


class Bridge:
    def __init__(self, agents: dict[str, BridgeAgent]) -> None:
        self.agents = agents

    def _agent(self, name: str) -> BridgeAgent:
        key = slugify(name)
        if key not in self.agents:
            raise ValueError(f"unknown agent {name!r}; known: {', '.join(self.agents)}")
        return self.agents[key]

    async def ask(
        self,
        name: str,
        request: str,
        ctx: Context | None,
        context_id: str | None,
        task_id: str | None,
        return_immediately: bool = False,
    ) -> dict[str, Any]:
        """Send ``request`` (blocking by default) and always return a dict, never raise."""
        try:
            agent = self._agent(name)
            if return_immediately:
                conn = await agent.blocking()
                result = await send_text(
                    conn, request, context_id=context_id, task_id=task_id, return_immediately=True
                )
                return summarize(result)
            events = []
            n = 0
            async for ev in stream_text(
                await agent.streaming(), request, context_id=context_id, task_id=task_id
            ):
                events.append(ev)
                if ev.WhichOneof("payload") == "status_update" and ctx is not None:
                    msg = ev.status_update.status
                    text = (
                        " ".join(p.text for p in msg.message.parts)
                        if msg.HasField("message")
                        else ""
                    )
                    n += 1
                    # MCP has no task stream; progress and log notifications are the honest echo.
                    await _notify(ctx, n, f"{state_name(msg.state)} {text}".strip())
            return summarize(collect(events))
        except Exception as exc:
            logger.warning("bridge call failed: %s", exc)
            return {
                "status": "error",
                "text": f"{type(exc).__name__}: {exc}",
                "task_id": task_id,
                "context_id": context_id,
                "artifacts": [],
            }

    async def get_task(self, name: str, task_id: str) -> dict[str, Any]:
        try:
            conn = await self._agent(name).blocking()
            task = await conn.client.get_task(GetTaskRequest(id=task_id, history_length=0))
            return summarize(SendResult("task", None, task))
        except Exception as exc:
            return {"status": "error", "text": f"{type(exc).__name__}: {exc}", "task_id": task_id}

    async def cancel_task(self, name: str, task_id: str) -> dict[str, Any]:
        try:
            conn = await self._agent(name).blocking()
            task = await conn.client.cancel_task(CancelTaskRequest(id=task_id))
            return summarize(SendResult("task", None, task))
        except Exception as exc:
            return {"status": "error", "text": f"{type(exc).__name__}: {exc}", "task_id": task_id}


async def _notify(ctx: Context, n: int, text: str) -> None:
    try:
        await ctx.info(text)
        await ctx.report_progress(n, None, text)
    except Exception:  # a client without progress/log support must not break the call
        logger.debug("client ignored progress notification")


def build_server(agents: dict[str, BridgeAgent] | None = None) -> FastMCP:
    agents = agents or resolve_agents(
        [a.strip() for a in os.environ.get("BRIDGE_AGENTS", DEFAULT_AGENTS).split(",") if a.strip()]
    )
    bridge = Bridge(agents)
    mcp = mcp_app.new_server(
        "lab03-mcp-a2a-bridge", "Exposes A2A agents as MCP tools. One ask_<agent> tool per agent."
    )
    hint = (
        " Returns {status, text, task_id, context_id, artifacts}. If status is input_required, "
        "call again with the same context_id and task_id."
    )

    @mcp.tool(name="list_agents", description="List the A2A agents reachable through this bridge.")
    @mcp_app.traced("list_agents")
    async def list_agents() -> list[dict[str, Any]]:
        return [
            {
                "name": a.card.name,
                "description": a.card.description,
                "tool": a.tool_name,
                "skills": [s.name for s in a.card.skills],
                "url": a.target,
            }
            for a in agents.values()
        ]

    def make_ask(slug: str) -> Any:
        @mcp_app.traced(f"ask_{slug}")
        async def ask(
            request: str, ctx: Context, context_id: str | None = None, task_id: str | None = None
        ) -> dict[str, Any]:
            return await bridge.ask(slug, request, ctx, context_id, task_id)

        return ask

    for slug, agent in agents.items():  # one generated tool per Agent Card
        mcp.add_tool(
            make_ask(slug), name=agent.tool_name, description=card_description(agent.card) + hint
        )

    @mcp.tool(
        name="send_message", description="Send a message to any bridged agent by name." + hint
    )
    @mcp_app.traced("send_message")
    async def send_message(
        agent: str,
        request: str,
        ctx: Context,
        context_id: str | None = None,
        task_id: str | None = None,
        return_immediately: bool = False,
    ) -> dict[str, Any]:
        return await bridge.ask(agent, request, ctx, context_id, task_id, return_immediately)

    @mcp.tool(name="get_task", description="Fetch the current state of a task (A2A GetTask).")
    @mcp_app.traced("get_task")
    async def get_task(agent: str, task_id: str) -> dict[str, Any]:
        return await bridge.get_task(agent, task_id)

    @mcp.tool(name="cancel_task", description="Cancel a running task (A2A CancelTask).")
    @mcp_app.traced("cancel_task")
    async def cancel_task(agent: str, task_id: str) -> dict[str, Any]:
        return await bridge.cancel_task(agent, task_id)

    @mcp.resource(
        "a2a://cards/{agent}",
        mime_type="application/json",
        description="The Agent Card JSON: MCP resources are where discovery data lives.",
    )
    def card_resource(agent: str) -> str:
        return MessageToJson(bridge._agent(agent).card)

    return mcp


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab03-mcp-a2a-bridge", framework="mcp", lab_id="lab03")
    mcp_app.serve(build_server(), service="lab03-mcp-a2a-bridge")


if __name__ == "__main__":
    main()
