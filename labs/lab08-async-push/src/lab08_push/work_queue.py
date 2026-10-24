"""A client-side async work queue fed by push notifications (spec lab08 §3).

Fan out N tasks with ``return_immediately`` and a push config each, then consume completions as
they arrive (out of order). Push is a HINT: a terminal notification is confirmed with ``GetTask``
(the source of truth) before the task is yielded, and ``reconcile()`` polls tasks whose push never
came, which is the safety net for a lost webhook.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import httpx
from a2a.types import GetTaskRequest, Task, TaskPushNotificationConfig
from a2a_labs_common.a2a.client import Connected, connect, send_text
from a2a_labs_common.a2a.states import TERMINAL, state_name

from lab08_push.receivers import Receiver

TERMINAL_NAMES = {state_name(s) for s in TERMINAL}


@dataclass
class Job:
    task_id: str
    service: str
    token: str
    conn: Connected
    submitted: float = field(default_factory=time.monotonic)
    last_push: float | None = None
    pushes: int = 0
    done: Task | None = None
    confirmed_by: str = ""  # "push" or "poll"


class PushWorkQueue:
    def __init__(
        self,
        receiver: Receiver,
        *,
        jwks_for: Callable[[str], str] | None = None,
        poll_s: float = 0.2,
        quiet_after_s: float = 20.0,
    ) -> None:
        self.receiver, self.poll_s, self.quiet_after_s = receiver, poll_s, quiet_after_s
        self.jwks_for = jwks_for or receiver.jwks_url
        self.jobs: dict[str, Job] = {}
        self._done: asyncio.Queue[Job] = asyncio.Queue()
        self._pump_task: asyncio.Task[None] | None = None
        self._since = 0

    async def submit(self, service: str, text: str) -> str:
        token = str(uuid.uuid4())
        # Registered BEFORE the task exists, so the first notifications cannot race the expectation.
        await self.receiver.expect(token, self.jwks_for(service))
        push = TaskPushNotificationConfig(url=self.receiver.webhook_url(token[:8]), token=token)
        conn = await connect(service, streaming=False, push_config=push)
        res = await send_text(conn, text, return_immediately=True)
        assert res.task_id
        self.jobs[res.task_id] = Job(res.task_id, service, token, conn)
        self._ensure_pump()
        return res.task_id

    def _ensure_pump(self) -> None:
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump())

    async def _confirm(self, job: Job, how: str) -> None:
        if job.done is not None:
            return
        task = await job.conn.client.get_task(GetTaskRequest(id=job.task_id))
        if task.status.state in TERMINAL:
            job.done, job.confirmed_by = task, how
            await self._done.put(job)

    async def _pump(self) -> None:
        last_reconcile = time.monotonic()
        while any(j.done is None for j in self.jobs.values()):
            try:
                batch = await self.receiver.notifications(self._since)
            except httpx.HTTPError:  # the receiver is down: push is lost, reconcile() covers it
                batch = []
            for n in batch:
                self._since = max(self._since, n["seq"])
                job = self.jobs.get(n["task_id"])
                if job is None:
                    continue
                job.last_push, job.pushes = time.monotonic(), job.pushes + 1
                if n.get("state") in TERMINAL_NAMES:
                    await self._confirm(job, "push")
            if time.monotonic() - last_reconcile > self.quiet_after_s:
                await self.reconcile()
                last_reconcile = time.monotonic()
            await asyncio.sleep(self.poll_s)

    async def reconcile(self) -> list[Job]:
        """GetTask for every unfinished task that has been quiet for ``quiet_after_s``."""
        found: list[Job] = []
        for job in self.jobs.values():
            quiet = time.monotonic() - (job.last_push or job.submitted)
            if job.done is None and quiet >= self.quiet_after_s:
                await self._confirm(job, "poll")
                if job.done is not None:
                    found.append(job)
        return found

    async def completions(self) -> AsyncIterator[Job]:
        """Yield each job once, as it becomes terminal and confirmed, until all are done."""
        yielded = 0
        while yielded < len(self.jobs):
            yield await self._done.get()
            yielded += 1

    async def aclose(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
        for job in self.jobs.values():
            await job.conn.aclose()
