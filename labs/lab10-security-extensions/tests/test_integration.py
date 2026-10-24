"""Against the running compose profile (`make e2e LAB=lab10`): the same behaviours, over docker."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from a2a.helpers import get_data_parts
from a2a.types import AuthenticationInfo, TaskPushNotificationConfig, TaskState
from a2a.utils.errors import ExtensionSupportRequiredError, VersionNotSupportedError
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.config import host_url
from lab10_secure.clients import (
    StaticCredentials,
    approve,
    card_json,
    client_token,
    message_params,
    rpc,
)
from lab10_secure.data import BUDGET_EXT, TRACE_EXT

pytestmark = pytest.mark.compose
PUBLIC = "What's on the public team calendar?"
SECURE, EXT, REQUIRED = "lab10-secure-agent", "lab10-ext-agent", "lab10-required-ext-agent"


def creds(**kw: str) -> StaticCredentials:
    return StaticCredentials({"oauth2": client_token(**kw)})


async def test_transport_auth_and_a_public_card() -> None:
    base = host_url(SECURE)
    assert rpc(base, "SendMessage", message_params(PUBLIC)).status_code == 401
    assert "oauth2" in card_json(base)["securitySchemes"]
    async with await connect(SECURE, auth=creds(), streaming=False) as c:
        res = await send_text(c, PUBLIC)
    assert res.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.parametrize("name", ["lab10-adk-secure", "lab10-strands-secure"])
async def test_framework_agents_need_a_token(name: str) -> None:
    assert rpc(host_url(name), "SendMessage", message_params(PUBLIC)).status_code == 401
    async with await connect(name, auth=creds(), streaming=False) as c:
        assert (await send_text(c, PUBLIC)).state == TaskState.TASK_STATE_COMPLETED


async def test_auth_required_then_resume_on_the_same_task() -> None:
    async with await connect(SECURE, auth=creds(), streaming=False) as c:
        first = await send_text(c, "Show my private appointments")
        assert first.state == TaskState.TASK_STATE_AUTH_REQUIRED
        challenge = get_data_parts(first.task.status.message.parts)[0]["auth"]
        ref = approve(challenge["user_code"])
        done = await send_text(
            c,
            "approved",
            task_id=first.task_id,
            context_id=first.context_id,
            data={"auth": {"access_token_ref": ref}},
        )
    assert done.state == TaskState.TASK_STATE_COMPLETED and done.task_id == first.task_id
    assert "Dentist" in done.text


async def test_a_destructive_request_is_rejected() -> None:
    async with await connect(SECURE, auth=creds(), streaming=False) as c:
        res = await send_text(c, "Delete all calendars")
    assert res.state == TaskState.TASK_STATE_REJECTED


async def test_extended_card_depends_on_the_scope() -> None:
    plain = rpc(host_url(SECURE), "GetExtendedAgentCard", headers=_bearer())
    admin = rpc(
        host_url(SECURE),
        "GetExtendedAgentCard",
        headers=_bearer(client_id="lab10-admin-client", scope="agent:invoke agent:admin"),
    )
    ids = lambda r: [s["id"] for s in r.json()["result"]["skills"]]  # noqa: E731
    assert "calendar.admin_stats" not in ids(plain) and "calendar.admin_stats" in ids(admin)


def _bearer(**kw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {client_token(**kw)}"}


async def test_the_optional_extension_is_opt_in() -> None:
    async with await connect(EXT, streaming=False) as off:
        assert len((await send_text(off, "capital of Portugal")).task.artifacts) == 1
    async with await connect(EXT, streaming=False, extensions=[TRACE_EXT]) as on:
        assert len((await send_text(on, "capital of Portugal")).task.artifacts) == 2


async def test_the_required_extension_is_enforced() -> None:
    async with await connect(REQUIRED, streaming=False) as c:
        with pytest.raises(ExtensionSupportRequiredError):
            await send_text(c, "Summarize: hi")
    async with await connect(REQUIRED, streaming=False, extensions=[BUDGET_EXT]) as c:
        ok = await send_text(c, "Summarize: hi", metadata={BUDGET_EXT: {"max_usd": 0.05}})
        assert ok.state == TaskState.TASK_STATE_COMPLETED


async def test_versions() -> None:
    async with await connect(EXT, streaming=False, a2a_version="2.0") as c:
        with pytest.raises(VersionNotSupportedError):
            await send_text(c, "hi")
    versions = [i["protocolVersion"] for i in card_json(host_url(EXT))["supportedInterfaces"]]
    assert versions == ["1.0", "0.3"]


async def test_authenticated_push_reaches_the_shared_receiver() -> None:
    receiver = host_url("webhook-receiver")
    async with httpx.AsyncClient() as http:
        await http.delete(f"{receiver}/notifications")
        await http.post(f"{receiver}/expectations", json={"token": "lab10-it", "bearer": "s3cret"})
    cfg = TaskPushNotificationConfig(
        url="http://webhook-receiver:9100/webhooks/a2a/lab10", token="lab10-it"
    )
    cfg.authentication.CopyFrom(AuthenticationInfo(scheme="Bearer", credentials="s3cret"))
    async with await connect(SECURE, auth=creds(), streaming=False, push_config=cfg) as c:
        await send_text(c, PUBLIC)
    await asyncio.sleep(2)
    async with httpx.AsyncClient() as http:
        rows = (await http.get(f"{receiver}/notifications", params={"channel": "lab10"})).json()
    assert rows and all(r["bearer_valid"] for r in rows)
