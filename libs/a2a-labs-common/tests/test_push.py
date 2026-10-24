"""Signed push notifications: signer, claims, URL screen and delivery retries."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import jwt
from a2a.types import Task, TaskPushNotificationConfig, TaskState, TaskStatus
from a2a_labs_common.a2a.push import (
    JwtPushNotificationSender,
    PushSigner,
    make_push_url_validator,
)
from jwt import PyJWK


class Store:
    def __init__(self, cfg: TaskPushNotificationConfig) -> None:
        self.cfg = cfg

    async def get_info_for_dispatch(self, task_id: str) -> list[TaskPushNotificationConfig]:
        return [self.cfg]


def task() -> Task:
    return Task(id="t1", context_id="c1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))


def test_signer_publishes_a_jwks_that_verifies_its_tokens() -> None:
    signer = PushSigner("lab08-sdk-report-agent")
    token = signer.sign({"iat": 1, "task_id": "t"})
    key = PyJWK(signer.jwks()["keys"][0])
    assert jwt.get_unverified_header(token)["kid"] == signer.kid
    assert (
        jwt.decode(token, key.key, algorithms=["ES256"], options={"verify_aud": False})["task_id"]
        == "t"
    )
    assert "d" not in signer.jwks()["keys"][0]  # public half only


async def test_the_validator_allows_listed_hosts_and_screens_the_rest() -> None:
    validate = make_push_url_validator("hook,jupyter")
    assert await validate("http://hook:9100/x") and await validate("https://jupyter/")
    assert not await validate("http://169.254.169.254/latest/meta-data")
    assert not await validate("ftp://hook/x")
    assert not await validate("http://10.1.2.3/x")


async def test_the_sender_binds_jwt_claims_to_the_exact_body_and_retries_on_5xx() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503 if len(calls) < 3 else 200)

    cfg = TaskPushNotificationConfig(id="c", task_id="t1", url="http://hook/x", token="tok")
    signer = PushSigner("lab08-sdk-report-agent")
    sender = JwtPushNotificationSender(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), Store(cfg), signer, backoff_s=0
    )
    ok = await sender._dispatch_notification(task(), cfg, "t1")
    assert ok and len(calls) == 3  # two 503s, then delivered
    last = calls[-1]
    claims = jwt.decode(
        last.headers["authorization"].removeprefix("Bearer "),
        PyJWK(signer.jwks()["keys"][0]).key,
        algorithms=["ES256"],
        options={"verify_aud": False},
    )
    assert claims["request_body_sha256"] == hashlib.sha256(last.content).hexdigest()
    assert claims["aud"] == "http://hook/x" and claims["task_id"] == "t1" and claims["jti"]
    assert last.headers["x-a2a-notification-token"] == "tok"
    assert (
        len(
            {
                jwt.decode(c.headers["authorization"][7:], options={"verify_signature": False})[
                    "jti"
                ]
                for c in calls
            }
        )
        == 3
    )


async def test_a_4xx_is_final_and_a_rejected_url_is_never_called() -> None:
    calls: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401)

    cfg = TaskPushNotificationConfig(id="c", task_id="t1", url="http://hook/x")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = JwtPushNotificationSender(
        client, Store(cfg), PushSigner("lab08-sdk-report-agent"), backoff_s=0
    )
    assert not await sender._dispatch_notification(task(), cfg, "t1")
    assert len(calls) == 1  # the receiver said no: no retry
    blocked = JwtPushNotificationSender(
        client,
        Store(cfg),
        PushSigner("lab08-sdk-report-agent"),
        push_url_validator=make_push_url_validator("other"),
    )
    calls.clear()
    bad = TaskPushNotificationConfig(id="c", task_id="t1", url="http://169.254.169.254/x")
    assert not await blocked._dispatch_notification(task(), bad, "t1") and not calls
