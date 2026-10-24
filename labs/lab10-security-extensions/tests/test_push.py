"""Authenticated push: the push config's ``authentication`` becomes the webhook's credentials."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from a2a.types import AuthenticationInfo, TaskPushNotificationConfig
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_webhook.app import create_app
from lab10_secure._testutil import Live, free_port
from lab10_secure.clients import StaticCredentials, client_token


@pytest.fixture
async def receiver() -> AsyncIterator[Live]:
    live = await Live(create_app(require_auth=True), free_port()).start()
    try:
        yield live
    finally:
        live.stop()


async def deliver(stack, receiver: Live, token: str, credentials: str | None) -> list[dict]:
    """Send one request with an inline push config; return what the webhook accepted."""
    async with httpx.AsyncClient() as http:
        await http.post(f"{receiver.url}/expectations", json={"token": token, "bearer": "s3cret"})
    auth = AuthenticationInfo(scheme="Bearer", credentials=credentials) if credentials else None
    cfg = TaskPushNotificationConfig(url=f"{receiver.url}/webhooks/a2a", token=token)
    if auth:
        cfg.authentication.CopyFrom(auth)
    creds = StaticCredentials({"oauth2": client_token()})
    async with await connect(
        stack["lab10-secure-agent"].url, auth=creds, streaming=False, push_config=cfg
    ) as c:
        await send_text(c, "What's on the public team calendar?")
    await asyncio.sleep(1.0)  # the sender posts in the background
    async with httpx.AsyncClient() as http:
        return (await http.get(f"{receiver.url}/notifications")).json()  # type: ignore[no-any-return]


async def test_the_config_credentials_arrive_as_the_authorization_header(stack, receiver) -> None:
    rows = await deliver(stack, receiver, "tok-good", "s3cret")
    assert rows and all(r["bearer_valid"] is True for r in rows)
    assert {r["state"] for r in rows} & {"COMPLETED", "WORKING"}


async def test_wrong_credentials_in_the_config_are_refused_by_the_receiver(stack, receiver) -> None:
    assert await deliver(stack, receiver, "tok-bad", "wrong") == []


async def test_a_config_without_authentication_is_refused_by_a_receiver_that_wants_it(
    stack, receiver
) -> None:
    assert await deliver(stack, receiver, "tok-none", None) == []
