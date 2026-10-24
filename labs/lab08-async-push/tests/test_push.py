"""Push in every framework, the config lifecycle, the SSRF guard, a lost push and a restart."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from a2a.client import ClientCallContext  # noqa: F401 - documents the call context type
from a2a.types import (
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    SubscribeToTaskRequest,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.a2a.push import make_push_url_validator
from lab08_push._testutil import Live, free_port
from lab08_push.work_queue import PushWorkQueue

SDK = "lab08-sdk-report-agent"
ALL = [
    SDK,
    "lab08-strands-batch-agent",
    "lab08-adk-research-agent",
    "lab08-langgraph-pipeline-agent",
]


def jwks_of(agents: dict[str, Live]) -> Any:
    return lambda service: f"{agents[service].url}/.well-known/jwks.json"


@pytest.mark.parametrize("service", ALL)
async def test_every_agent_pushes_signed_notifications_ending_in_a_final_state(
    agents: dict[str, Live], receiver: Any, service: str
) -> None:
    q = PushWorkQueue(receiver, jwks_for=jwks_of(agents))
    task_id = await q.submit(service, "Research solid-state batteries")
    jobs = [j async for j in q.completions()]
    await q.aclose()
    assert jobs[0].done.status.state == TaskState.TASK_STATE_COMPLETED  # type: ignore[union-attr]
    rows = await receiver.notifications(task_id=task_id)
    assert rows and all(r["jwt_valid"] is True and r["token_valid"] is True for r in rows)
    assert rows[-1]["state"] == "COMPLETED" and jobs[0].confirmed_by == "push"


async def test_push_config_lifecycle_with_two_channels(
    agents: dict[str, Live], receiver: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHASE_SECONDS", "0.4")
    tok_a, tok_b = str(uuid.uuid4()), str(uuid.uuid4())
    for tok in (tok_a, tok_b):
        await receiver.expect(tok, jwks_of(agents)(SDK))
    inline = TaskPushNotificationConfig(url=receiver.webhook_url("a"), token=tok_a, id="chan-a")
    async with await connect(SDK, streaming=False, push_config=inline) as c:
        run = await send_text(c, "Build the Q3 report", return_immediately=True)
        second = TaskPushNotificationConfig(
            task_id=run.task_id, id="chan-b", url=receiver.webhook_url("b"), token=tok_b
        )
        created = await c.client.create_task_push_notification_config(second)
        got = await c.client.get_task_push_notification_config(
            GetTaskPushNotificationConfigRequest(task_id=run.task_id, id="chan-b")
        )
        listed = await c.client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id=run.task_id)
        )
        assert created.id == got.id == "chan-b" and got.url.endswith("/webhooks/a2a/b")
        assert sorted(x.id for x in listed.configs) == ["chan-a", "chan-b"]
        await c.client.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest(task_id=run.task_id, id="chan-b")
        )
        seq_at_delete = max([n["seq"] for n in await receiver.notifications()] or [0])
        left = await c.client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id=run.task_id)
        )
        assert [x.id for x in left.configs] == ["chan-a"]
        for _ in range(100):  # wait for completion
            t = await c.client.get_task(GetTaskRequest(id=run.task_id))
            if t.status.state == TaskState.TASK_STATE_COMPLETED:
                break
            await asyncio.sleep(0.1)
    after = await receiver.notifications(since=seq_at_delete)
    assert after and {n["channel"] for n in after} == {"a"}  # the deleted channel went quiet
    assert (await receiver.notifications(channel="a"))[-1]["state"] == "COMPLETED"


async def test_the_ssrf_guard_rejects_metadata_and_private_addresses(
    agents: dict[str, Live], monkeypatch: pytest.MonkeyPatch
) -> None:
    for url in (
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/hook",
        "file:///etc/passwd",
    ):
        async with await connect(SDK, streaming=False) as c:
            run = await send_text(c, "Build the Q3 report", return_immediately=True)
            cfg = TaskPushNotificationConfig(task_id=run.task_id, id="x", url=url)
            with pytest.raises(Exception, match=r"(?i)invalid"):
                await c.client.create_task_push_notification_config(cfg)
    # the allowlisted compose names pass without a DNS lookup; others are screened by the SDK
    validate = make_push_url_validator("webhook-receiver,jupyter")
    assert await validate("http://webhook-receiver:9100/webhooks/a2a")
    assert not await validate("http://169.254.169.254/")
    assert not await validate("http://localhost:9100/")  # loopback is not on THIS allowlist


async def _capture() -> tuple[Live, list[tuple[bytes, dict[str, str]]]]:
    """A webhook that only records what it receives, so the notifications can be replayed."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    seen: list[tuple[bytes, dict[str, str]]] = []

    async def hook(request: Request) -> JSONResponse:
        seen.append((await request.body(), dict(request.headers)))
        return JSONResponse({"ok": True})

    return await Live(
        Starlette(routes=[Route("/{p:path}", hook, methods=["POST"])]), free_port()
    ).start(), seen


async def test_the_receiver_rejects_forged_replayed_stale_and_tampered_notifications(
    agents: dict[str, Live], receiver: Any
) -> None:
    cap, seen = await _capture()
    token = str(uuid.uuid4())
    try:
        push = TaskPushNotificationConfig(url=f"{cap.url}/hook", token=token)
        async with await connect(SDK, streaming=False, push_config=push) as c:
            run = await send_text(c, "Build the Q3 report", return_immediately=True)
            for _ in range(100):
                if any(b'"COMPLETED"' in body for body, _h in seen):
                    break
                await asyncio.sleep(0.1)
    finally:
        cap.stop()
    assert seen, "the agent never called the capture webhook"
    await receiver.expect(token, jwks_of(agents)(SDK))
    body, headers = seen[0]
    replay_headers = {
        k: v
        for k, v in headers.items()
        if k in ("authorization", "x-a2a-notification-token", "content-type")
    }
    post = lambda b, h: httpx.post(f"{receiver.base}/webhooks/a2a", content=b, headers=h)  # noqa: E731
    first = await asyncio.to_thread(post, body, replay_headers)
    assert first.status_code == 200  # the genuine notification is accepted once
    assert (await asyncio.to_thread(post, body, replay_headers)).status_code == 401  # replayed jti
    tampered = body.replace(b'"contextId":"', b'"contextId":"x', 1)
    # a fresh jti is needed for the tamper test: sign a new notification with the agent's own key
    signer = agents[SDK].app.state.signer
    import hashlib
    import time

    def bearer(**over: Any) -> str:
        claims = {
            "iss": "x",
            "aud": "y",
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
            "task_id": run.task_id,
            "request_body_sha256": hashlib.sha256(body).hexdigest(),
        }
        return signer.sign({**claims, **over})

    def hdr(t: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {t}",
            "x-a2a-notification-token": token,
            "content-type": "application/json",
        }

    assert (await asyncio.to_thread(post, tampered, hdr(bearer()))).status_code == 401  # body hash
    assert (
        await asyncio.to_thread(post, body, hdr(bearer(iat=int(time.time()) - 4000)))
    ).status_code == 401
    from a2a_labs_common.a2a.push import PushSigner

    forged = PushSigner("self-signed").sign({"iat": int(time.time()), "jti": "z"})
    assert (await asyncio.to_thread(post, body, hdr(forged))).status_code == 401  # unknown key
    assert (await asyncio.to_thread(post, body, hdr(bearer()))).status_code == 200  # control: valid


async def test_a_task_survives_an_agent_restart_in_the_durable_store(
    agents: dict[str, Live], monkeypatch: pytest.MonkeyPatch
) -> None:
    from lab08_push.servers.sdk_report_agent import build_app

    async with await connect(SDK, streaming=False) as c:
        res = await send_text(c, "Build the Q3 report")
    assert res.state == TaskState.TASK_STATE_COMPLETED and res.task_id
    port = agents[SDK].port
    agents[SDK].stop()
    reborn = await Live(build_app(durable=True), port).start()  # same port, same SQLite file
    agents[SDK] = reborn
    async with await connect(SDK, streaming=False) as c:
        again = await c.client.get_task(GetTaskRequest(id=res.task_id))
    assert again.status.state == TaskState.TASK_STATE_COMPLETED and again.artifacts


async def test_the_work_queue_confirms_eight_tasks_as_they_complete(
    agents: dict[str, Live], receiver: Any
) -> None:
    q = PushWorkQueue(receiver, jwks_for=jwks_of(agents))
    for i in range(8):
        await q.submit(ALL[i % 4], f"job {i}")
    done = [j async for j in q.completions()]
    await q.aclose()
    assert len(done) == 8 and len({j.task_id for j in done}) == 8
    assert all(j.done.status.state == TaskState.TASK_STATE_COMPLETED for j in done)  # type: ignore[union-attr]
    assert {j.confirmed_by for j in done} == {"push"}  # every completion arrived by push


async def test_a_lost_push_is_found_by_reconcile(
    agents: dict[str, Live], receiver: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHASE_SECONDS", "0.2")
    q = PushWorkQueue(receiver, jwks_for=jwks_of(agents), quiet_after_s=0.3)
    await q.submit(SDK, "Build the Q3 report")
    receiver.stop()  # the webhook goes away mid-task: every push fails
    done = [j async for j in q.completions()]
    await q.aclose()
    assert done[0].confirmed_by == "poll"  # GetTask, the source of truth, caught it


async def test_subscribe_to_task_reattaches_to_a_running_task(
    agents: dict[str, Live], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHASE_SECONDS", "0.3")
    async with await connect(SDK, streaming=False) as c:  # a non-streaming send returns at once
        run = await send_text(c, "Build the Q3 report", return_immediately=True)
    async with await connect(SDK, streaming=True) as c:  # ...then attach a stream, as a UI would
        states = [
            ev.status_update.status.state
            async for ev in c.client.subscribe(SubscribeToTaskRequest(id=run.task_id))
            if ev.WhichOneof("payload") == "status_update"
        ]
    assert states and states[-1] == TaskState.TASK_STATE_COMPLETED


async def test_cancelling_an_async_task_pushes_a_canceled_notification(
    agents: dict[str, Live], receiver: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a2a.types import CancelTaskRequest

    monkeypatch.setenv("PHASE_SECONDS", "0.5")
    q = PushWorkQueue(receiver, jwks_for=jwks_of(agents))
    task_id = await q.submit(SDK, "Build the Q3 report")
    await asyncio.sleep(0.3)
    await q.jobs[task_id].conn.client.cancel_task(CancelTaskRequest(id=task_id))
    done = [j async for j in q.completions()]
    await q.aclose()
    assert done[0].done.status.state == TaskState.TASK_STATE_CANCELED  # type: ignore[union-attr]
    assert (await receiver.notifications(task_id=task_id))[-1]["state"] == "CANCELED"
