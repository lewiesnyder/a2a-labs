"""Shared server helpers: sign the card at startup, publish the public key as a JWKS."""

from __future__ import annotations

from typing import Any

from a2a.types import AgentCard
from a2a_labs_common.a2a.trust import sign_card
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from lab06_registry.keys import load_private, public_of


def signed(card: AgentCard, kid: str) -> AgentCard:
    # A2A: sign the card at startup with the agent's private key; clients verify it against a
    # trust store they control, never against keys served by the agent itself.
    return sign_card(card, load_private(kid), kid)


def add_jwks(app: Any, kid: str) -> None:
    """Serve ``/.well-known/jwks.json`` for completeness. Verifiers must NOT rely on it."""
    jwks = {"keys": [{**public_of(load_private(kid)), "kid": kid, "alg": "ES256", "use": "sig"}]}

    async def endpoint(_: Request) -> JSONResponse:
        return JSONResponse(jwks)

    app.router.routes.append(Route("/.well-known/jwks.json", endpoint))
