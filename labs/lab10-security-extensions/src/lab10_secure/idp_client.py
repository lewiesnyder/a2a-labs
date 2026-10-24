"""What the calendar agent needs from the IdP: start a device flow, redeem a token reference.

# CUSTOM: the reference pattern (see ``idp.py``). The agent authenticates to the IdP with its own
# client credentials, so a stolen reference is useless to anyone else.
"""

from __future__ import annotations

from typing import Any

import httpx
from a2a_labs_common.auth import JwtVerifier
from a2a_labs_common.config import endpoint, public_url

from lab10_secure.idp import AUDIENCE, REF_GRANT, default_clients


class IdpError(Exception):
    pass


class IdpClient:
    def __init__(self, client_id: str = "lab10-secure-agent", secret: str | None = None) -> None:
        self.client_id = client_id
        self.secret = secret or default_clients()[client_id]["secret"]
        self.base = endpoint("lab10-mock-idp")

    async def _post(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                f"{self.base}{path}", data=data, auth=(self.client_id, self.secret)
            )
        if resp.status_code >= 400:
            raise IdpError(resp.text)
        return resp.json()  # type: ignore[no-any-return]

    async def start_device_flow(self, scope: str) -> dict[str, Any]:
        return await self._post("/device_authorization", {"scope": scope, "audience": AUDIENCE})

    async def redeem(self, token_ref: str) -> str:
        body = await self._post(
            "/token", {"grant_type": REF_GRANT, "token_ref": token_ref, "audience": AUDIENCE}
        )
        return str(body["access_token"])


def make_verifier() -> JwtVerifier:
    # issuer = what the IdP puts in `iss`: its own public URL; keys come from where WE can reach it
    return JwtVerifier(
        issuer=public_url("lab10-mock-idp"),
        audience=AUDIENCE,
        jwks_url=f"{endpoint('lab10-mock-idp')}/jwks",
    )
