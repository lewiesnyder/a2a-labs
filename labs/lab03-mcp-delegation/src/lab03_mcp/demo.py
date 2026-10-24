"""Notebook helpers: a slow A2A agent plus a bridge to it, run inside the notebook process.

Step 11 of the notebook needs a task that takes a while. The specialists answer in seconds, so we
start a scripted "Slow Worker" (A2A) and a second bridge (MCP) in background threads and talk to
both. Everything is torn down by ``DemoStack.stop()``. Implements: spec lab03 §4 step 11.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from dataclasses import dataclass, field

import uvicorn
from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState


class SlowWorker(AgentExecutor):
    """Works for ``seconds`` (default 8), reporting progress every second, then answers."""

    def __init__(self, seconds: int = 8) -> None:
        self.seconds = seconds
        self._cancel: dict[str, asyncio.Event] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        u = TaskUpdater(event_queue, task.id, task.context_id)
        flag = self._cancel.setdefault(task.id, asyncio.Event())
        await u.start_work()
        for i in range(1, self.seconds + 1):
            try:
                await asyncio.wait_for(flag.wait(), timeout=1.0)
                return  # cancelled
            except TimeoutError:
                await u.update_status(
                    TaskState.TASK_STATE_WORKING,
                    u.new_agent_message([new_text_part(f"processed {i}/{self.seconds}")]),
                )
        await u.add_artifact([new_text_part("all done")], name="result")
        await u.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        if context.task_id in self._cancel:
            self._cancel[context.task_id].set()
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class DemoStack:
    worker_url: str
    bridge_url: str
    _servers: list[tuple[uvicorn.Server, threading.Thread]] = field(default_factory=list)

    def stop(self) -> None:
        for server, _ in self._servers:
            server.should_exit = True  # graceful first: avoids lifespan tracebacks in the notebook
        for server, thread in self._servers:
            thread.join(timeout=3)
            if thread.is_alive():  # open MCP streams can hold shutdown; then force
                server.force_exit = True
                thread.join(timeout=5)


def _start(app: object, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    return server, thread


def start_demo_stack(seconds: int = 8) -> DemoStack:
    """Start the Slow Worker (A2A) and a bridge to it (MCP). Blocks until both are up."""
    from a2a_labs_common.a2a.server import build_a2a_app

    from lab03_mcp.servers import mcp_a2a_bridge

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    card = AgentCard(
        name="Slow Worker",
        description="Works for a few seconds, reporting progress, then answers.",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(url=url, protocol_binding="JSONRPC", protocol_version="1.0")
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="work",
                name="Slow work",
                description="A long-running job.",
                tags=["demo"],
                examples=["do the slow thing"],
            )
        ],
    )
    worker = _start(build_a2a_app(card=card, executor=SlowWorker(seconds)), port)
    bridge = mcp_a2a_bridge.build_server(mcp_a2a_bridge.resolve_agents([url], wait_s=10))
    bridge_port = _free_port()
    bridge_server = _start(bridge.streamable_http_app(), bridge_port)
    return DemoStack(url, f"http://127.0.0.1:{bridge_port}/mcp", [worker, bridge_server])
