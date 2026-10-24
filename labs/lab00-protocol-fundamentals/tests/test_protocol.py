"""Wire-level behaviour: errors, versions, compat, extended card, push, transports."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from a2a.types import (
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    TaskPushNotificationConfig,
)
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.testing import asgi_client
from lab00_protocol.servers.playground import build_app

HDR = {"Content-Type": "application/json", "A2A-Version": "1.0"}


def msg(text: str, **kw: object) -> dict[str, object]:
    return {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": text}], **kw}}


async def rpc(
    http: httpx.AsyncClient,
    method: str,
    params: object = None,
    headers: dict | None = None,
    raw: str | None = None,
) -> dict:  # type: ignore[type-arg]
    body = (
        raw
        if raw is not None
        else json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    )
    return (await http.post("/", content=body, headers={**HDR, **(headers or {})})).json()


def app(variant: str = "full") -> httpx.AsyncClient:
    return asgi_client(build_app(variant, step_delay=0))


async def test_legacy_name_rejected_v03_name_and_v10_name_work() -> None:
    async with app() as http:
        assert (await rpc(http, "tasks/send", msg("echo hi")))["error"]["code"] == -32601
        v10 = await rpc(http, "SendMessage", msg("echo hi"))
        assert v10["result"]["message"]["role"] == "ROLE_AGENT"  # v1.0: SCREAMING_SNAKE enums
        v03 = (
            await http.post(
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
        ).json()
        assert v03["result"]["kind"] == "task" and v03["result"]["status"]["state"] == "completed"


@pytest.mark.parametrize(
    ("variant", "method", "params", "code", "reason"),
    [
        ("full", "GetTask", {"id": "nope"}, -32001, "TASK_NOT_FOUND"),
        (
            "full",
            "SendMessage",
            {"message": {"messageId": "m", "role": "ROLE_USER", "parts": []}},
            -32602,
            "INVALID_PARAMS",
        ),
        (
            "nopush",
            "ListTaskPushNotificationConfigs",
            {"taskId": "x"},
            -32003,
            "PUSH_NOTIFICATION_NOT_SUPPORTED",
        ),
        ("nostream", "SendStreamingMessage", msg("echo hi"), -32004, "UNSUPPORTED_OPERATION"),
        ("nostream", "SubscribeToTask", {"id": "x"}, -32004, "UNSUPPORTED_OPERATION"),
        ("nostream", "GetExtendedAgentCard", {}, -32004, "UNSUPPORTED_OPERATION"),
        (
            "nostream",
            "SendMessage",
            {
                "message": {
                    "messageId": "m",
                    "role": "ROLE_USER",
                    "parts": [{"text": "x", "mediaType": "image/png"}],
                }
            },
            -32005,
            "CONTENT_TYPE_NOT_SUPPORTED",
        ),
    ],
)
async def test_error_catalog(
    variant: str, method: str, params: object, code: int, reason: str
) -> None:
    async with app(variant) as http:
        err = (await rpc(http, method, params))["error"]
        assert err["code"] == code
        if code != -32602:  # invalid params reports field errors in metadata
            assert err["data"][0]["reason"] == reason


async def test_malformed_missing_jsonrpc_version_and_crash() -> None:
    async with app() as http:
        assert (await rpc(http, "", raw="{nope"))["error"]["code"] == -32700
        no_version = json.dumps({"id": 1, "method": "SendMessage", "params": msg("echo")})
        assert (await rpc(http, "", raw=no_version))["error"]["code"] == -32600
        bad = await rpc(http, "SendMessage", msg("echo hi"), headers={"A2A-Version": "9.9"})
        assert (
            bad["error"]["code"] == -32009
            and bad["error"]["data"][0]["reason"] == "VERSION_NOT_SUPPORTED"
        )
        # Observed SDK behaviour for an executor exception: JSON-RPC -32603 carrying the message.
        crash = (await rpc(http, "SendMessage", msg("crash")))["error"]
        assert crash["code"] == -32603 and "crash command" in crash["message"]


async def test_extended_card_requires_bearer_and_adds_admin_skill() -> None:
    async with app() as http:
        denied = await http.post(
            "/",
            content=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "GetExtendedAgentCard", "params": {}}
            ),
            headers=HDR,
        )
        assert denied.status_code == 401
        rest = await http.get("/rest/extendedAgentCard")
        assert rest.status_code == 401
        ok = await rpc(
            http, "GetExtendedAgentCard", headers={"Authorization": "Bearer lab00-admin"}
        )
        assert "playground.admin" in [s["id"] for s in ok["result"]["skills"]]
        assert (await http.get("/.well-known/agent-card.json")).json()["capabilities"][
            "extendedAgentCard"
        ]
        stats = await rpc(
            http, "SendMessage", msg("stats"), headers={"Authorization": "Bearer lab00-admin"}
        )
        assert stats["result"]["task"]["artifacts"][0]["name"] == "stats"


async def test_extended_card_through_sdk_client(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False, headers={"Authorization": "Bearer lab00-admin"}) as c:
        card = await c.client.get_extended_agent_card(GetExtendedAgentCardRequest())
        assert any(s.id == "playground.admin" for s in card.skills)


async def test_push_config_crud_in_process(playground) -> None:  # type: ignore[no-untyped-def]
    async with playground(streaming=False) as c:
        r = await send_text(c, "ask")  # an interrupted task stays alive, so configs can attach
        cfg = TaskPushNotificationConfig(
            task_id=r.task_id, id="c1", token="tok", url="http://webhook-receiver:9100/webhooks/a2a"
        )
        created = await c.client.create_task_push_notification_config(cfg)
        assert created.id == "c1" and created.token == "tok"
        got = await c.client.get_task_push_notification_config(
            GetTaskPushNotificationConfigRequest(task_id=r.task_id, id="c1")
        )
        assert got.url.endswith("/webhooks/a2a")
        listed = await c.client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id=r.task_id)
        )
        assert [x.id for x in listed.configs] == ["c1"]
        await c.client.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest(task_id=r.task_id, id="c1")
        )
        again = await c.client.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id=r.task_id)
        )
        assert list(again.configs) == []


def _serve(asgi_app):  # type: ignore[no-untyped-def]
    """Run an ASGI app on an ephemeral port in a thread; returns (port, stop)."""
    import socket
    import threading
    import time

    import uvicorn

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(asgi_app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=5)

    return port, stop


async def test_push_notifications_arrive_at_the_receiver(live_playground) -> None:  # type: ignore[no-untyped-def]
    """Full loop: agent -> SDK push sender -> real webhook receiver over HTTP."""
    from a2a_labs_webhook.app import TOKEN_HEADER, create_app

    receiver_app = create_app(require_auth=False)
    port, stop = _serve(receiver_app)
    try:
        cfg = TaskPushNotificationConfig(url=f"http://127.0.0.1:{port}/webhooks/a2a", token="tok-1")
        async with live_playground(streaming=False, push_config=cfg) as c:
            r = await send_text(c, "work 3", return_immediately=True)
            store = receiver_app.state.store
            for _ in range(100):  # within 10 s
                got = store.query(task_id=r.task_id)
                if any(e["state"] == "COMPLETED" for e in got):
                    break
                await asyncio.sleep(0.1)
            kinds = {e["kind"] for e in got}
            assert {"status_update", "artifact_update"} <= kinds
            assert any(e["state"] == "COMPLETED" for e in got)
            assert TOKEN_HEADER == "X-A2A-Notification-Token"
    finally:
        stop()


async def test_rest_and_grpc_parity_and_shared_task_store(live_playground) -> None:  # type: ignore[no-untyped-def]
    """Same command over three bindings gives the same artifacts; one TaskStore backs all."""
    results = {}
    task_ids = {}
    for binding in ("JSONRPC", "HTTP+JSON", "GRPC"):
        async with live_playground(streaming=False, transport=binding, step_delay=0) as c:
            r = await send_text(c, "work 3")
            results[binding] = r.text
            task_ids[binding] = r.task_id
            assert c.card.supported_interfaces  # the card lists all three bindings
    assert len(set(results.values())) == 1 and results["GRPC"].startswith("step 1 of 3 done")
    assert len(set(task_ids.values())) == 3


async def test_task_created_over_grpc_is_visible_over_rest(live_playground) -> None:  # type: ignore[no-untyped-def]
    # One server, two clients on different bindings.
    async with live_playground(streaming=False, transport="GRPC", step_delay=0) as grpc_client:
        r = await send_text(grpc_client, "work 1")
        card_url = next(
            i.url
            for i in grpc_client.card.supported_interfaces
            if i.protocol_binding == "HTTP+JSON"
        )
        base = card_url.removesuffix("/rest")
        async with connect_rest(base) as c2:
            task = await c2.client.get_task(GetTaskRequest(id=r.task_id))
            assert task.id == r.task_id


def connect_rest(base: str):  # type: ignore[no-untyped-def]
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def ctx():  # type: ignore[no-untyped-def]
        c = await connect(base, streaming=False, transport="HTTP+JSON")
        try:
            yield c
        finally:
            await c.aclose()

    return ctx()
