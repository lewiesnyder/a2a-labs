"""``PlaygroundExecutor``: a scripted agent that walks through every A2A construct.

Pattern: pure a2a-sdk ``AgentExecutor``. Implements: spec lab00 §3.

Rules this file demonstrates (read them as a checklist):
* Task commands emit the ``Task`` first, then updates via ``TaskUpdater``.
* Message commands emit exactly ONE ``Message`` and nothing else.
* Interrupted tasks (INPUT_REQUIRED / AUTH_REQUIRED) resume when the client sends a new
  message with the same ``task_id``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import zlib

from a2a.helpers import (
    get_data_parts,
    new_data_part,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
    new_url_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskStore, TaskUpdater
from a2a.types import ListTasksRequest, Part, Role, TaskState
from a2a_labs_common.a2a.states import INTERRUPTED, state_name
from a2a_labs_common.config import public_url
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)

HELP = (
    "Commands: help | echo <text> | work [n] | slow [seconds] | ask | auth | fail | reject | "
    "parts | multi | crash | refs | stats (admin only)"
)


def tiny_png() -> bytes:
    """A valid 1x1 red PNG, generated so no binary blob lives in the repo."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class PlaygroundExecutor(AgentExecutor):
    def __init__(
        self,
        task_store: TaskStore,
        *,
        service: str = "lab00-protocol-agent",
        step_delay: float | None = None,
        auth_token: str | None = None,
    ) -> None:
        self.task_store = task_store
        self.service = service
        self.step_delay = (
            float(os.environ.get("PLAYGROUND_STEP_DELAY", "0.5"))
            if step_delay is None
            else step_delay
        )
        self.auth_token = auth_token or os.environ.get("PLAYGROUND_AUTH_TOKEN", "letmein")
        # `slow` reports once per tick; 1 s in the lab, near-zero when step_delay=0 (tests).
        self.tick = 1.0 if self.step_delay > 0 else 0.02
        self._cancel_events: dict[str, asyncio.Event] = {}

    # ── helpers ────────────────────────────────────────────────────────────────────────────
    def _agent_message(self, context: RequestContext, text: str):  # type: ignore[no-untyped-def]
        return new_text_message(text, context_id=context.context_id, role=Role.ROLE_AGENT)

    async def _pause(self, event: asyncio.Event, seconds: float) -> bool:
        """Sleep, but wake early on cancel. True means 'cancelled'."""
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True

    # ── AgentExecutor ──────────────────────────────────────────────────────────────────────
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.message is not None
        text = context.get_user_input().strip()
        task = context.current_task

        # Resume: an interrupted task receives its follow-up message.
        if task is not None and task.status.state in INTERRUPTED:
            assert context.task_id and context.context_id
            updater = TaskUpdater(event_queue, context.task_id, context.context_id)
            if task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
                await self._resume_ask(updater, text)
            else:
                await self._resume_auth(updater, context)
            return

        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        logger.info("command=%s arg=%r", cmd, arg)

        # ---- Message-only commands: exactly one Message, no Task. ----
        if cmd == "echo":
            meta = MessageToDict(context.message.metadata)
            reply = f"{arg}" + (f"\nmetadata: {meta}" if meta else "")
            await event_queue.enqueue_event(self._agent_message(context, reply))
            return
        if cmd == "stats" and not context.call_context.user.is_authenticated:
            await event_queue.enqueue_event(
                self._agent_message(context, "stats needs the admin bearer token")
            )
            return
        if cmd == "crash":
            raise RuntimeError("crash command: the executor raised on purpose")
        if cmd not in _TASK_COMMANDS:
            await event_queue.enqueue_event(self._agent_message(context, HELP))
            return

        # ---- Task commands: the Task must be the first event. ----
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        cancel = self._cancel_events.setdefault(task.id, asyncio.Event())
        try:
            await getattr(self, f"_cmd_{cmd}")(updater, context, arg, cancel)
        finally:
            self._cancel_events.pop(task.id, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        assert context.task_id and context.context_id
        event = self._cancel_events.get(context.task_id)
        if event is not None:
            event.set()  # the work/slow loops see this between steps
        # A2A: the executor decides how to stop; the terminal state is CANCELED.
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()

    # ── task commands ──────────────────────────────────────────────────────────────────────
    async def _cmd_work(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        n = _int(arg, 5)
        await u.start_work()
        for i in range(1, n + 1):
            if await self._pause(cancel, self.step_delay):
                return
            await u.update_status(
                TaskState.TASK_STATE_WORKING, u.new_agent_message([new_text_part(f"step {i}/{n}")])
            )
            # A2A: first chunk append=False, later chunks append=True, final last_chunk=True.
            await u.add_artifact(
                [new_text_part(f"step {i} of {n} done\n")],
                artifact_id="progress-report",
                name="progress-report",
                append=i > 1,
                last_chunk=i == n,
            )
        await u.complete()

    async def _cmd_slow(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        seconds = _int(arg, 30)
        await u.start_work()
        for i in range(1, seconds + 1):
            if await self._pause(cancel, self.tick):
                return
            await u.update_status(
                TaskState.TASK_STATE_WORKING,
                u.new_agent_message([new_text_part(f"slow {i}/{seconds}s")]),
            )
        await u.add_artifact(
            [new_text_part(f"slept {seconds}s")], artifact_id="slow-result", name="slow-result"
        )
        await u.complete()

    async def _cmd_ask(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        await u.start_work()
        # A2A: INTERRUPTED state; the client continues with the SAME task_id.
        await u.requires_input(
            u.new_agent_message([new_text_part("Which colour do you choose? red/green/blue")])
        )

    async def _resume_ask(self, u: TaskUpdater, answer: str) -> None:
        await u.start_work()
        await u.add_artifact(
            [new_text_part(f"You chose {answer}")], artifact_id="answer", name="answer"
        )
        await u.complete()

    async def _cmd_auth(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        await u.start_work()
        await u.requires_auth(
            u.new_agent_message([new_text_part('Send a data part {"token": "<token>"}')])
        )

    async def _resume_auth(self, u: TaskUpdater, ctx: RequestContext) -> None:
        assert ctx.message is not None
        token = next((d.get("token") for d in get_data_parts(ctx.message.parts)), None)
        await u.start_work()
        if token == self.auth_token:
            await u.add_artifact(
                [new_text_part("the secret is 42")], artifact_id="secret", name="secret"
            )
            await u.complete()
        else:
            await u.failed(u.new_agent_message([new_text_part("invalid token")]))

    async def _cmd_fail(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        await u.start_work()
        await u.failed(u.new_agent_message([new_text_part("failing on purpose: the fail command")]))

    async def _cmd_reject(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        await u.reject(u.new_agent_message([new_text_part("I don't do that")]))

    async def _cmd_parts(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        card_url = public_url(self.service) + "/.well-known/agent-card.json"
        gallery: list[tuple[str, Part]] = [
            ("text-part", new_text_part("plain text")),
            ("image-part", Part(raw=tiny_png(), media_type="image/png", filename="dot.png")),
            ("url-part", new_url_part(card_url, media_type="application/json")),
            (
                "data-part",
                new_data_part(
                    {"temperature": 21.5, "unit": "C", "tags": ["demo"]},
                    media_type="application/json",
                ),
            ),
        ]
        for name, part in gallery:
            await u.add_artifact([part], artifact_id=name, name=name)
        await u.complete()

    async def _cmd_multi(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        # Three artifacts whose chunks interleave and finish in a different order than created.
        plan = [
            ("beta", "B1 ", False, False),
            ("alpha", "A1 ", False, False),
            ("gamma", "G1", False, True),
            ("alpha", "A2", True, True),
            ("beta", "B2", True, True),
        ]
        for name, text, append, last in plan:
            await u.add_artifact(
                [new_text_part(text)], artifact_id=name, name=name, append=append, last_chunk=last
            )
        await u.complete()

    async def _cmd_refs(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        assert ctx.message is not None
        lines = []
        for ref in ctx.message.reference_task_ids:
            found = await self.task_store.get(ref, ctx.call_context)
            lines.append(f"{ref}: {state_name(found.status.state) if found else 'NOT FOUND'}")
        await u.add_artifact(
            [new_text_part("\n".join(lines) or "no reference_task_ids")],
            artifact_id="refs",
            name="refs",
        )
        await u.complete()

    async def _cmd_stats(
        self, u: TaskUpdater, ctx: RequestContext, arg: str, cancel: asyncio.Event
    ) -> None:
        counts: dict[str, int] = {}
        token = ""
        while True:
            page = await self.task_store.list(
                ListTasksRequest(page_size=100, page_token=token), ctx.call_context
            )
            for t in page.tasks:
                counts[state_name(t.status.state)] = counts.get(state_name(t.status.state), 0) + 1
            token = page.next_page_token
            if not token:
                break
        await u.add_artifact([new_data_part(counts)], artifact_id="stats", name="stats")
        await u.complete()


_TASK_COMMANDS = {
    "work",
    "slow",
    "ask",
    "auth",
    "fail",
    "reject",
    "parts",
    "multi",
    "refs",
    "stats",
}


def _int(arg: str, default: int) -> int:
    try:
        return max(1, int(arg.split()[0])) if arg else default
    except ValueError:
        return default
