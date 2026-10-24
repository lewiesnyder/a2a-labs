"""Transport auth: JWT verification, API keys, the 401 gate and the client-credentials auth."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
import pytest
from a2a_labs_common.auth import (
    ApiKeyBackend,
    BearerBackend,
    ChainBackend,
    JwtVerifier,
    OAuthClientCredentialsAuth,
    auth_middleware,
    bearer_interceptor,
    caller,
    claim_scopes,
)
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

ISSUER, AUD = "http://idp.test", "api"


class Idp:
    """A throwaway issuer served through an httpx MockTransport."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.kid = "k1"
        self.jwks_calls = 0
        self.token_calls = 0

    def jwk(self) -> dict[str, Any]:
        pub = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(self.key.public_key()))
        return {**pub, "kid": self.kid, "alg": "ES256"}

    def mint(self, **over: Any) -> str:
        claims = {
            "iss": ISSUER,
            "aud": AUD,
            "sub": "svc",
            "scope": "a b",
            "exp": int(time.time()) + 60,
            **over,
        }
        return jwt.encode(claims, self.key, algorithm="ES256", headers={"kid": self.kid})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks":
            self.jwks_calls += 1
            return httpx.Response(200, json={"keys": [self.jwk()]})
        self.token_calls += 1
        return httpx.Response(
            200, json={"access_token": f"tok{self.token_calls}", "expires_in": 100}
        )


def verifier(idp: Idp) -> JwtVerifier:
    http = httpx.AsyncClient(transport=httpx.MockTransport(idp.handler))
    return JwtVerifier(issuer=ISSUER, audience=AUD, jwks_url=f"{ISSUER}/jwks", http=http)


async def test_verifier_accepts_a_good_token_and_caches_the_jwks() -> None:
    idp = Idp()
    v = verifier(idp)
    assert (await v.verify(idp.mint()))["sub"] == "svc"
    await v.verify(idp.mint())
    assert idp.jwks_calls == 1


@pytest.mark.parametrize(
    "over",
    [{"aud": "other"}, {"iss": "http://evil"}, {"exp": 1}],
    ids=["audience", "issuer", "expired"],
)
async def test_verifier_rejects_wrong_audience_issuer_and_expiry(over: dict[str, Any]) -> None:
    from starlette.authentication import AuthenticationError

    idp = Idp()
    with pytest.raises(AuthenticationError):
        await verifier(idp).verify(idp.mint(**over))


async def test_verifier_refetches_once_for_an_unknown_kid_then_gives_up() -> None:
    from starlette.authentication import AuthenticationError

    idp = Idp()
    v = verifier(idp)
    await v.verify(idp.mint())
    idp.kid = "rotated"  # the issuer rotated its key; the verifier must look again
    idp.key = ec.generate_private_key(ec.SECP256R1())
    assert (await v.verify(idp.mint()))["sub"] == "svc"
    assert idp.jwks_calls == 2
    forged = jwt.encode(
        {"sub": "x"}, ec.generate_private_key(ec.SECP256R1()), "ES256", headers={"kid": "nope"}
    )
    with pytest.raises(AuthenticationError, match="unknown key"):
        await v.verify(forged)
    with pytest.raises(AuthenticationError, match="malformed"):
        await v.verify("garbage")


async def test_verifier_reports_an_unreachable_idp() -> None:
    from starlette.authentication import AuthenticationError

    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    v = JwtVerifier(
        issuer=ISSUER,
        audience=AUD,
        jwks_url=f"{ISSUER}/jwks",
        http=httpx.AsyncClient(transport=httpx.MockTransport(boom)),
    )
    with pytest.raises(AuthenticationError, match="unreachable"):
        await v.verify(Idp().mint())


def test_claim_scopes_accepts_a_string_or_a_list() -> None:
    assert claim_scopes({"scope": "a b"}) == ["a", "b"]
    assert claim_scopes({"scope": ["a"]}) == ["a"] and claim_scopes({}) == []


def app_with(idp: Idp) -> Starlette:
    async def whoami(request: Request) -> JSONResponse:
        return JSONResponse({"user": request.user.display_name, "scopes": request.auth.scopes})

    async def card(_: Request) -> JSONResponse:
        return JSONResponse({"card": True})

    backend = ChainBackend(BearerBackend(verifier(idp)), ApiKeyBackend({"k": ("keyuser", ["x"])}))
    return Starlette(
        routes=[Route("/whoami", whoami), Route("/.well-known/agent-card.json", card)],
        middleware=auth_middleware(backend),
    )


async def test_the_gate_authenticates_with_either_scheme_and_keeps_the_card_public() -> None:
    idp = Idp()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with(idp)), base_url="http://t"
    ) as c:
        assert (await c.get("/.well-known/agent-card.json")).status_code == 200
        anonymous = await c.get("/whoami")
        assert anonymous.status_code == 401 and anonymous.headers["www-authenticate"] == "Bearer"
        ok = await c.get("/whoami", headers={"Authorization": f"Bearer {idp.mint()}"})
        assert ok.json() == {"user": "svc", "scopes": ["a", "b"]}
        key = await c.get("/whoami", headers={"X-API-Key": "k"})
        assert key.json() == {"user": "keyuser", "scopes": ["x"]}
        for bad in (
            {"X-API-Key": "nope"},
            {"Authorization": "Basic zzz"},
            {"Authorization": "Bearer "},
        ):
            assert (await c.get("/whoami", headers=bad)).status_code == 401


async def test_caller_reads_user_and_scopes_from_the_call_context() -> None:
    from a2a.server.context import ServerCallContext

    class Ctx:
        call_context = ServerCallContext(state={"auth": type("A", (), {"scopes": ["s"]})()})

    name, scopes = caller(Ctx())  # type: ignore[arg-type]
    assert name == "" and scopes == {"s"}  # no user in this bare context


async def test_client_credentials_auth_caches_until_near_expiry() -> None:
    idp = Idp()
    auth = OAuthClientCredentialsAuth(
        f"{ISSUER}/token", "id", "secret", "a", audience="api", refresh_margin_s=0
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(idp.handler))
    assert await auth.token(http) == "tok1" and await auth.token(http) == "tok1"
    auth._expires = 0  # force expiry
    assert await auth.token(http) == "tok2"


async def test_client_credentials_auth_sets_the_bearer_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = OAuthClientCredentialsAuth(f"{ISSUER}/token", "id", "secret")
    auth._token, auth._expires = "cached", time.time() + 100
    req = httpx.Request("GET", "http://x")
    flow = auth.async_auth_flow(req)
    sent = await flow.__anext__()
    assert sent.headers["Authorization"] == "Bearer cached"


async def test_bearer_interceptor_puts_the_token_in_the_call_context() -> None:
    from google.adk.a2a.agent.config import ParametersConfig

    for provider in (lambda: "plain", _async_token):
        ic = bearer_interceptor(provider)
        _, params = await ic.before_request(None, "msg", ParametersConfig())
        assert params.client_call_context.service_parameters["Authorization"].startswith("Bearer ")


async def _async_token() -> str:
    return "late"
