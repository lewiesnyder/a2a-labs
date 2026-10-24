"""Client-side helpers for the notebook and tests: tokens, consent, raw JSON-RPC.

Everything here uses a2a-sdk calls (``AuthInterceptor`` via ``connect(auth=...)``) or plain HTTP
to the IdP. The raw ``rpc`` helper exists so the notebook can show the exact headers and bodies.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import httpx
from a2a.client import ClientCallContext, CredentialService
from a2a_labs_common.config import endpoint

from lab10_secure.idp import AUDIENCE, DEVICE_GRANT, default_clients


class StaticCredentials(CredentialService):
    """``AuthInterceptor`` asks for a credential PER SCHEME NAME declared on the card.

    ``StaticCredentials({"oauth2": token})`` answers only for ``oauth2``; the interceptor then
    skips the card's ``api_key`` requirement and sends ``Authorization: Bearer <token>``.
    """

    def __init__(self, by_scheme: Mapping[str, str]) -> None:
        self.by_scheme = dict(by_scheme)

    async def get_credentials(
        self, security_scheme_name: str, context: ClientCallContext | None
    ) -> str | None:
        return self.by_scheme.get(security_scheme_name)


def idp_url() -> str:
    return endpoint("lab10-mock-idp")


def client_token(
    client_id: str = "lab10-client", scope: str = "agent:invoke", audience: str = AUDIENCE
) -> str:
    """OAuth2 client-credentials grant against the lab IdP."""
    secret = default_clients()[client_id]["secret"]
    resp = httpx.post(
        f"{idp_url()}/token",
        data={"grant_type": "client_credentials", "scope": scope, "audience": audience},
        auth=(client_id, secret),
        timeout=10,
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


def approve(user_code: str, user: str = "alice") -> str:
    """The USER consenting on the IdP's verification page. Returns an ``access_token_ref``."""
    resp = httpx.post(
        f"{idp_url()}/device/approve", data={"user_code": user_code, "user": user}, timeout=10
    )
    resp.raise_for_status()
    return str(resp.json()["access_token_ref"])


def rpc(
    url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    version: str | None = "1.0",
) -> httpx.Response:
    """One raw JSON-RPC call. ``version=None`` omits ``A2A-Version`` (the server assumes 0.3)."""
    hdrs = dict(headers or {})
    if version is not None:
        hdrs["A2A-Version"] = version
    return httpx.post(
        url,
        json={"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params or {}},
        headers=hdrs,
        timeout=30,
    )


def message_params(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "message": {
            "messageId": str(uuid.uuid4()),
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            **extra,
        }
    }


def card_json(base: str) -> dict[str, Any]:
    """The public Agent Card as plain JSON (no credentials: discovery is open)."""
    resp = httpx.get(f"{base}/.well-known/agent-card.json", timeout=10)
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


def post_json(url: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return httpx.post(url, json=body, timeout=30).json()  # type: ignore[no-any-return]


def device_flow_token(user: str = "alice", scope: str = "calendar:read") -> str:
    """Run the whole device flow as ONE party and return the user's raw access token.

    Real deployments never do this: the agent starts the flow and the user approves elsewhere.
    The test uses it to get a transport token that already carries the user's consent.
    """
    client_id = "lab10-secure-agent"
    secret = default_clients()[client_id]["secret"]
    base = idp_url()
    dev = httpx.post(
        f"{base}/device_authorization", data={"scope": scope}, auth=(client_id, secret), timeout=10
    ).json()
    approve(dev["user_code"], user)
    token = httpx.post(
        f"{base}/token",
        data={"grant_type": DEVICE_GRANT, "device_code": dev["device_code"]},
        auth=(client_id, secret),
        timeout=10,
    ).json()["access_token"]
    return str(token)
