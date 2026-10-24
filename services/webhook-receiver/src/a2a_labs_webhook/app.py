"""Shared push-notification receiver (spec: labs/shared-webhook-receiver.md).

Implements: shared-webhook-receiver spec. Stands in for the client-side webhook an
orchestrator would run in production. Run it only on the private compose network.

# CUSTOM: ``POST /expectations`` lets a notebook tell this *shared* receiver which token and
# JWKS to expect per task. A real orchestrator embeds that knowledge itself (Lab 08).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import deque
from typing import Any

import httpx
import jwt
import uvicorn
from a2a.types import StreamResponse
from a2a_labs_common.a2a.events import kind, state_of
from a2a_labs_common.config import port
from a2a_labs_common.telemetry import init_telemetry, instrument_app
from a2a_labs_common.telemetry.metrics import instruments
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from google.protobuf.json_format import ParseDict
from opentelemetry import trace
from pydantic import BaseModel

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("a2a_labs_webhook")
RING_SIZE = 10_000
JWKS_TTL_S = 300
IAT_SKEW_S = 300
# A2A: the SDK push sender puts the config's `token` in this header (verified in M0/M2).
TOKEN_HEADER = "X-A2A-Notification-Token"


class Expectation(BaseModel):
    """What to expect for a task, or (``task_id`` omitted) for every push carrying ``token``.

    A token-keyed expectation can be registered BEFORE the task exists, which is how a client
    avoids losing the first notifications to a race with ``SendMessage``.
    """

    task_id: str | None = None
    token: str | None = None
    agent_jwks_url: str | None = None
    # The config's `authentication.credentials` (scheme Bearer): a shared secret the agent must
    # present in `Authorization`. Lab 10's authenticated-push demo; Lab 08 uses the JWT instead.
    bearer: str | None = None


class Store:
    """In-memory ring buffer of received notifications plus per-task expectations."""

    def __init__(self, size: int = RING_SIZE) -> None:
        self.items: deque[dict[str, Any]] = deque(maxlen=size)
        self.expectations: dict[str, Expectation] = {}
        self.by_token: dict[str, Expectation] = {}
        self.seen_jti: dict[str, float] = {}  # replay protection
        self.seq = 0
        self.waiters: list[asyncio.Queue[dict[str, Any]]] = []
        self._jwks: dict[str, tuple[float, dict[str, Any]]] = {}

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        entry = {"seq": self.seq, "received_at": time.time(), **entry}
        self.items.append(entry)
        for q in self.waiters:
            q.put_nowait(entry)
        return entry

    def query(
        self, task_id: str | None = None, channel: str | None = None, since: int = 0
    ) -> list[dict[str, Any]]:
        return [
            e
            for e in self.items
            if e["seq"] > since
            and (task_id is None or e["task_id"] == task_id)
            and (channel is None or e["channel"] == channel)
        ]

    async def jwks(self, url: str, *, refresh: bool = False) -> dict[str, Any]:
        cached = self._jwks.get(url)
        if cached and not refresh and time.time() - cached[0] < JWKS_TTL_S:
            return cached[1]
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        self._jwks[url] = (time.time(), data)
        return data


async def verify_jwt(store: Store, bearer: str, jwks_url: str, body: bytes) -> tuple[bool, str]:
    """Agent-JWKS signature (by ``kid``), ``iat`` freshness, body hash and no replayed ``jti``."""
    try:
        kid = jwt.get_unverified_header(bearer).get("kid")
        keys = (await store.jwks(jwks_url)).get("keys", [])
        key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:  # unknown kid: the agent may have rotated its key; refetch once
            keys = (await store.jwks(jwks_url, refresh=True)).get("keys", [])
            key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:
            return False, f"no key with kid={kid}"
        claims = jwt.decode(
            bearer,
            jwt.PyJWK(key).key,
            algorithms=["RS256", "ES256", "EdDSA"],
            options={"verify_aud": False, "require": ["iat"]},
        )
        if abs(time.time() - float(claims["iat"])) > IAT_SKEW_S:
            return False, "iat outside +/-5 min"
        expected = claims.get("request_body_sha256")  # [VERIFY claim name in Lab 08]
        if expected and expected != hashlib.sha256(body).hexdigest():
            return False, "request body hash mismatch"
        jti = claims.get("jti")
        if jti:
            now = time.time()
            store.seen_jti = {k: t for k, t in store.seen_jti.items() if now - t < 2 * IAT_SKEW_S}
            if jti in store.seen_jti:
                return False, "replayed jti"
            store.seen_jti[jti] = now  # only a fully verified notification burns its jti
    except Exception as exc:  # any failure means "not verified"
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def create_app(require_auth: bool | None = None, store: Store | None = None) -> FastAPI:
    app = FastAPI(title="a2a-labs webhook receiver")
    store = store or Store()
    app.state.store = store
    if require_auth is None:
        require_auth = os.environ.get("WEBHOOK_REQUIRE_AUTH", "false").lower() == "true"

    async def receive(request: Request, channel: str) -> JSONResponse:
        raw = await request.body()
        with _tracer.start_as_current_span("a2a.push.receive") as span:
            try:
                payload = json.loads(raw)
                event = ParseDict(payload, StreamResponse())
            except Exception as exc:
                instruments().push_received.add(1, {"valid": False})
                return JSONResponse({"error": f"bad body: {exc}"}, status_code=400)
            k = kind(event)
            task_id = {
                "task": event.task.id,
                "status_update": event.status_update.task_id,
                "artifact_update": event.artifact_update.task_id,
            }.get(k, "")
            token = request.headers.get(TOKEN_HEADER)
            exp = store.expectations.get(task_id) or (store.by_token.get(token) if token else None)
            token_valid = None if exp is None or exp.token is None else token == exp.token
            bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            jwt_valid: bool | None = None
            jwt_detail = ""
            if bearer and exp is not None and exp.agent_jwks_url:
                jwt_valid, jwt_detail = await verify_jwt(store, bearer, exp.agent_jwks_url, raw)
            bearer_valid: bool | None = None
            if exp is not None and exp.bearer is not None:
                bearer_valid = bearer == exp.bearer
            ok = token_valid is not False and jwt_valid is not False and bearer_valid is not False
            authenticated = token_valid is True or jwt_valid is True or bearer_valid is True
            span.set_attributes(
                {
                    "a2a.task.id": task_id,
                    "a2a.push.token.valid": token_valid if token_valid is not None else False,
                    "a2a.push.jwt.valid": jwt_valid if jwt_valid is not None else False,
                }
            )
            state = state_of(event)
            if state:
                span.set_attribute("a2a.task.state", state)
            instruments().push_received.add(
                1, {"valid": ok and (authenticated or not require_auth)}
            )
            if not ok or (require_auth and not authenticated):
                logger.warning(
                    "push rejected task=%s token=%s jwt=%s", task_id, token_valid, jwt_valid
                )
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            store.add(
                {
                    "channel": channel,
                    "task_id": task_id,
                    "kind": k,
                    "state": state,
                    "token_valid": token_valid,
                    "jwt_valid": jwt_valid,
                    "bearer_valid": bearer_valid,
                    "jwt_detail": jwt_detail,
                    "payload": payload,
                }
            )
            logger.info("push received task=%s kind=%s state=%s", task_id, k, state)
            return JSONResponse({"status": "ok"})

    @app.post("/webhooks/a2a")
    async def webhook(request: Request) -> JSONResponse:
        return await receive(request, "default")

    @app.post("/webhooks/a2a/{channel}")
    async def webhook_channel(channel: str, request: Request) -> JSONResponse:
        return await receive(request, channel)

    @app.post("/expectations")
    async def expect(e: Expectation) -> dict[str, str]:
        if e.task_id:
            store.expectations[e.task_id] = e
        elif e.token:
            store.by_token[e.token] = e
        return {"status": "registered"}

    @app.get("/notifications")
    async def notifications(
        task_id: str | None = None, channel: str | None = None, since: int = 0
    ) -> list[dict[str, Any]]:
        return store.query(task_id, channel, since)

    @app.get("/notifications/stream")
    async def stream(task_id: str | None = None) -> StreamingResponse:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        store.waiters.append(q)

        async def gen() -> Any:
            try:
                while True:
                    entry = await q.get()
                    if task_id is None or entry["task_id"] == task_id:
                        yield f"data: {json.dumps(entry)}\n\n"
            finally:
                store.waiters.remove(q)

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.delete("/notifications")
    async def clear() -> dict[str, str]:
        store.items.clear()
        store.expectations.clear()
        store.by_token.clear()
        store.seen_jti.clear()
        return {"status": "cleared"}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    instrument_app(app)
    return app


def main() -> None:
    init_telemetry("webhook-receiver", framework="service", lab_id="shared")
    uvicorn.run(create_app(), host="0.0.0.0", port=port("webhook-receiver"), log_config=None)


if __name__ == "__main__":
    main()
