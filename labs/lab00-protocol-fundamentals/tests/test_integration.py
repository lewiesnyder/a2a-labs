"""Integration tests against the running ``lab00`` compose profile (``make e2e LAB=lab00``)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from a2a.types import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    SubscribeToTaskRequest,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a_labs_common.a2a import events
from a2a_labs_common.a2a.client import connect, send_text, stream_text, wait_for_cards
from a2a_labs_common.config import endpoint

pytestmark = pytest.mark.compose
AGENT, NOSTREAM, NOPUSH = (
    "lab00-protocol-agent",
    "lab00-protocol-agent-nostream",
    "lab00-protocol-agent-nopush",
)
RECEIVER = "webhook-receiver"
HDR = {"Content-Type": "application/json", "A2A-Version": "1.0"}


@pytest.fixture(scope="module", autouse=True)
async def _services_up() -> None:
    await wait_for_cards([AGENT, NOSTREAM, NOPUSH], timeout=60)


def msg(text: str) -> dict[str, object]:
    return {"message": {"messageId": "it-1", "role": "ROLE_USER", "parts": [{"text": text}]}}


async def rpc(service: str, method: str, params: object, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(base_url=endpoint(service), timeout=20) as http:
        r = await http.post(
            "/",
            headers={**HDR, **(headers or {})},
            content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
        )
        return r.json()


async def test_all_eleven_operations_against_full() -> None:
    async with await connect(
        AGENT, streaming=True, headers={"Authorization": "Bearer lab00-admin"}
    ) as c:
        # SendMessage, SendStreamingMessage
        direct = await send_text(c, "echo hi")
        assert direct.kind == "message"
        evs = [e async for e in stream_text(c, "work 2")]
        task_id = evs[0].task.id
        assert events.state_of(evs[-1]) == "COMPLETED"
        # GetTask, ListTasks
        assert (
            await c.client.get_task(GetTaskRequest(id=task_id))
        ).status.state == TaskState.TASK_STATE_COMPLETED
        assert any(t.id == task_id for t in (await c.client.list_tasks(ListTasksRequest())).tasks)
        # CancelTask, SubscribeToTask
        # A streaming connection would follow the whole stream, so create the task non-streaming.
        # Same principal as `c`: the SDK scopes tasks to the authenticated user (owner).
        async with await connect(
            AGENT, streaming=False, headers={"Authorization": "Bearer lab00-admin"}
        ) as quick:
            slow = await send_text(quick, "slow 30", return_immediately=True)
        resub = c.client.subscribe(SubscribeToTaskRequest(id=slow.task_id))
        first = await anext(resub)
        assert events.kind(first) == "task"
        await c.client.cancel_task(CancelTaskRequest(id=slow.task_id))
        # Push config CRUD (Create, Get, List, Delete)
        ask = await send_text(c, "ask")
        cfg = TaskPushNotificationConfig(
            task_id=ask.task_id, id="it", url="http://webhook-receiver:9100/webhooks/a2a"
        )
        assert (await c.client.create_task_push_notification_config(cfg)).id == "it"
        got = await c.client.get_task_push_notification_config(
            GetTaskPushNotificationConfigRequest(task_id=ask.task_id, id="it")
        )
        assert got.url.endswith("/webhooks/a2a")
        listed = await c.client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id=ask.task_id)
        )
        assert [x.id for x in listed.configs] == ["it"]
        await c.client.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest(task_id=ask.task_id, id="it")
        )
        # GetExtendedAgentCard (bearer token set on the connection)
        ext = await c.client.get_extended_agent_card(GetExtendedAgentCardRequest())
        assert any(s.id == "playground.admin" for s in ext.skills)


async def test_legacy_rejected_and_v03_works() -> None:
    assert (await rpc(AGENT, "tasks/send", msg("echo hi")))["error"]["code"] == -32601
    async with httpx.AsyncClient(base_url=endpoint(AGENT)) as http:
        r = await http.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "message": {
                        "kind": "message",
                        "messageId": "m",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "work 1"}],
                    }
                },
            },
        )
        assert r.json()["result"]["status"]["state"] == "completed"


async def test_variants_reject_what_they_do_not_support() -> None:
    assert (await rpc(NOSTREAM, "SendStreamingMessage", msg("echo hi")))["error"]["code"] == -32004
    assert (await rpc(NOSTREAM, "SubscribeToTask", {"id": "x"}))["error"]["code"] == -32004
    assert (await rpc(NOPUSH, "ListTaskPushNotificationConfigs", {"taskId": "x"}))["error"][
        "code"
    ] == -32003


async def test_rest_grpc_parity() -> None:
    texts = []
    for binding in ("JSONRPC", "HTTP+JSON", "GRPC"):
        async with await connect(AGENT, streaming=False, transport=binding) as c:  # type: ignore[arg-type]
            texts.append((await send_text(c, "work 2")).text)
    assert len(set(texts)) == 1


async def test_push_notification_reaches_the_receiver_within_10s() -> None:
    async with httpx.AsyncClient(base_url=endpoint(RECEIVER)) as rx:
        await rx.delete("/notifications")
        cfg = TaskPushNotificationConfig(
            url="http://webhook-receiver:9100/webhooks/a2a", token="it-token"
        )
        async with await connect(AGENT, streaming=False, push_config=cfg) as c:
            r = await send_text(c, "work 2", return_immediately=True)
            await rx.post("/expectations", json={"task_id": r.task_id, "token": "it-token"})
            states: set[str] = set()
            for _ in range(100):
                got = (await rx.get("/notifications", params={"task_id": r.task_id})).json()
                states = {e["state"] for e in got if e["state"]}
                if "COMPLETED" in states:
                    break
                await asyncio.sleep(0.1)
            assert "COMPLETED" in states


async def test_two_concurrent_subscribers_receive_equal_tails() -> None:
    async with await connect(AGENT) as c:
        first = await anext(aiter(stream_text(c, "work 6", return_immediately=True)))
        tid = first.task.id if events.kind(first) == "task" else first.message.task_id

        async def collect() -> list[str]:
            return [
                f"{events.kind(e)}:{events.state_of(e)}"
                async for e in c.client.subscribe(SubscribeToTaskRequest(id=tid))
            ]

        a, b = await asyncio.gather(collect(), collect())
        assert a[-1] == b[-1] == "status_update:COMPLETED"
