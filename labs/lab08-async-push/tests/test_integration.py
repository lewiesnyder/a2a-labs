"""Integration tests against the running ``lab08`` profile (``make e2e LAB=lab08``).

The receiver is EMBEDDED in the test process (the client owns the webhook): the containers reach
it at ``host.docker.internal``.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import AsyncIterator
from typing import Any

import pytest
from a2a.types import GetTaskRequest, TaskPushNotificationConfig, TaskState
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.config import find_config_dir
from lab08_push.receivers import EmbeddedReceiver
from lab08_push.work_queue import PushWorkQueue

pytestmark = pytest.mark.compose
SERVICES = [
    "lab08-sdk-report-agent",
    "lab08-strands-batch-agent",
    "lab08-adk-research-agent",
    "lab08-langgraph-pipeline-agent",
]


@pytest.fixture(scope="module", autouse=True)
async def _services_up() -> None:
    await wait_for_cards(SERVICES, timeout=180)


@pytest.fixture
async def receiver() -> AsyncIterator[EmbeddedReceiver]:
    r = EmbeddedReceiver()  # host.docker.internal is on the agents' default allowlist
    await r.start()
    yield r
    r.stop()


@pytest.mark.parametrize("service", SERVICES)
async def test_each_agent_pushes_signed_notifications_to_the_clients_own_webhook(
    receiver: Any, service: str
) -> None:
    q = PushWorkQueue(receiver)
    task_id = await q.submit(service, "Research solid-state batteries")
    jobs = [j async for j in q.completions()]
    await q.aclose()
    assert jobs[0].done.status.state == TaskState.TASK_STATE_COMPLETED  # type: ignore[union-attr]
    rows = await receiver.notifications(task_id=task_id)
    assert rows and all(r["jwt_valid"] is True for r in rows) and rows[-1]["state"] == "COMPLETED"


async def test_the_metadata_address_is_rejected_as_a_push_target() -> None:
    async with await connect(SERVICES[0], streaming=False) as c:
        run = await send_text(c, "Build the Q3 report", return_immediately=True)
        cfg = TaskPushNotificationConfig(
            task_id=run.task_id, id="x", url="http://169.254.169.254/latest/meta-data"
        )
        with pytest.raises(Exception, match=r"(?i)invalid"):
            await c.client.create_task_push_notification_config(cfg)


async def test_eight_jobs_all_complete_and_are_confirmed_by_get_task(receiver: Any) -> None:
    q = PushWorkQueue(receiver)
    for i in range(8):
        await q.submit(SERVICES[i % 4], f"job {i}")
    done = [j async for j in q.completions()]
    await q.aclose()
    assert len(done) == 8
    assert all(j.done.status.state == TaskState.TASK_STATE_COMPLETED for j in done)  # type: ignore[union-attr]


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker CLI")
async def test_a_completed_task_survives_an_agent_restart() -> None:
    async with await connect(SERVICES[0], streaming=False) as c:
        res = await send_text(c, "Build the Q3 report")
    assert res.state == TaskState.TASK_STATE_COMPLETED
    root = find_config_dir().parent
    cmd = ["docker", "compose", "--profile", "lab08", "restart", SERVICES[0]]
    await asyncio.to_thread(subprocess.run, cmd, cwd=root, check=True, capture_output=True)
    await wait_for_cards([SERVICES[0]], timeout=120)
    async with await connect(SERVICES[0], streaming=False) as c:
        again = await c.client.get_task(GetTaskRequest(id=res.task_id))
    assert again.status.state == TaskState.TASK_STATE_COMPLETED and again.artifacts
