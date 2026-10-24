"""Authenticated push notifications (spec lab08 §2.1, §2.2).

What the SDK gives us: ``BasePushNotificationSender`` (one POST per config, the config's token in
``X-A2A-Notification-Token``, optional config-supplied ``Authorization``) and a URL screen,
``validate_push_notification_url``. What it does not: a signed notification the receiver can
verify without sharing a secret. This module adds that.

* ``PushSigner``: an ES256 key per agent process; ``jwks()`` is what the agent publishes.
* ``JwtPushNotificationSender``: adds ``Authorization: Bearer <JWT>`` whose claims bind the token
  to this delivery (``aud`` = webhook URL, ``jti`` for replay detection, ``task_id`` and
  ``request_body_sha256`` of the exact bytes sent). Retries 3x with backoff on 5xx / connection
  errors; 4xx is final (the receiver said no).
* ``make_push_url_validator``: the SDK's SSRF screen plus an allowlist of hosts that are allowed
  to resolve to private addresses (the compose network's receivers).

# CUSTOM: the SDK ships no authenticated sender. Follows the a2a-samples push-auth pattern.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from a2a.server.tasks import BasePushNotificationSender
from a2a.types import TaskPushNotificationConfig
from a2a.utils.proto_utils import to_stream_response
from a2a.utils.push_url_validator import validate_push_notification_url
from cryptography.hazmat.primitives.asymmetric import ec
from google.protobuf.json_format import MessageToDict
from jwt.algorithms import ECAlgorithm
from opentelemetry import trace
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a_labs_common.config import public_url
from a2a_labs_common.telemetry.metrics import instruments

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("a2a_labs_common.push")
TOKEN_HEADER = "X-A2A-Notification-Token"
DEFAULT_ALLOWED_HOSTS = "webhook-receiver,jupyter,host.docker.internal"
RETRIES = 3


class PushSigner:
    """An ES256 key pair held in memory; a restart rotates it (the JWKS follows)."""

    def __init__(self, service: str) -> None:
        self.service = service
        self._key = ec.generate_private_key(ec.SECP256R1())
        public = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(self._key.public_key()))
        self.kid = f"{service}-{uuid.uuid4().hex[:8]}"
        self.public_jwk = {**public, "kid": self.kid, "alg": "ES256", "use": "sig"}

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.public_jwk]}

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": self.kid})


def add_jwks_route(app: Any, signer: PushSigner) -> None:
    """Publish the agent's push-signing public key at ``/.well-known/jwks.json`` (# CUSTOM)."""

    async def endpoint(_: Request) -> JSONResponse:
        return JSONResponse(signer.jwks())

    app.router.routes.append(Route("/.well-known/jwks.json", endpoint))


def make_push_url_validator(
    allowed_hosts: str | None = None,
) -> Callable[[str], Awaitable[bool]]:
    """SDK screen (scheme, private/loopback/link-local ranges) except for allowlisted hosts."""
    raw = allowed_hosts or os.environ.get("PUSH_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)
    allowed = {h.strip() for h in raw.split(",") if h.strip()}

    async def validate(url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.hostname in allowed:
            return True
        return await validate_push_notification_url(url)

    return validate


class JwtPushNotificationSender(BasePushNotificationSender):
    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        config_store: Any,
        signer: PushSigner,
        *,
        push_url_validator: Callable[[str], Awaitable[bool]] | None = None,
        backoff_s: float = 0.2,
    ) -> None:
        super().__init__(httpx_client, config_store, push_url_validator=push_url_validator)
        self.signer, self.backoff_s = signer, backoff_s

    def claims_for(self, url: str, task_id: str, body: bytes) -> dict[str, Any]:
        return {
            "iss": f"{public_url(self.signer.service)}/.well-known/agent-card.json",
            "aud": url,
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
            "task_id": task_id,
            "request_body_sha256": hashlib.sha256(body).hexdigest(),
        }

    async def _dispatch_notification(
        self, event: Any, push_info: TaskPushNotificationConfig, task_id: str
    ) -> bool:
        url = push_info.url
        if self._push_url_validator is not None and not await self._push_url_validator(url):
            logger.warning("push url rejected at delivery time: %s", url)
            return False
        body = json.dumps(
            MessageToDict(to_stream_response(event)), separators=(",", ":"), ensure_ascii=False
        ).encode()
        with _tracer.start_as_current_span(
            "a2a.push.deliver",
            attributes={
                "a2a.task.id": task_id,
                "a2a.push.url": url,
                "a2a.push.config": push_info.id,
            },
        ) as span:
            for attempt in range(1, RETRIES + 1):
                bearer = self.signer.sign(self.claims_for(url, task_id, body))
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"}
                if push_info.token:
                    headers[TOKEN_HEADER] = push_info.token
                outcome = "error"
                try:
                    resp = await self._client.post(url, content=body, headers=headers)
                    span.set_attribute("http.response.status_code", resp.status_code)
                    if resp.status_code < 400:
                        outcome = "delivered"
                        break
                    outcome = f"http_{resp.status_code}"
                    if resp.status_code < 500:  # the receiver refused: retrying cannot help
                        break
                except httpx.HTTPError as exc:
                    span.record_exception(exc)
                span.set_attribute("a2a.push.attempts", attempt)
                if attempt < RETRIES:
                    await asyncio.sleep(self.backoff_s * 2 ** (attempt - 1))
            span.set_attribute("a2a.push.outcome", outcome)
            instruments().push_deliveries.add(1, {"outcome": outcome})
            if outcome != "delivered":
                logger.warning("push to %s failed for %s: %s", url, task_id, outcome)
            return outcome == "delivered"
