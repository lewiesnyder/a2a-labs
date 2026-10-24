"""Transport authentication for A2A servers and clients (spec lab10 §2).

What the SDK gives us: ``ServerCallContext.user`` (filled from Starlette's ``request.user``),
``ServerCallContext.state["auth"]`` (Starlette's ``request.auth``, i.e. the scopes), the client's
``AuthInterceptor`` + ``CredentialService``, and the card's ``security_schemes``. What it does not
give us: anything that actually REFUSES an unauthenticated request. Transport auth is the web
framework's job (A2A: credentials travel in HTTP headers, never in A2A payloads), so this module
plugs three small pieces into Starlette's own ``AuthenticationMiddleware``:

* backends: ``BearerBackend`` (IdP-issued JWTs), ``ApiKeyBackend`` and ``ChainBackend``;
* ``RequireAuth``: answers 401 before any task exists, except for the public card;
* ``OAuthClientCredentialsAuth``: an ``httpx.Auth`` that fetches and caches a client token.

# CUSTOM: none of these ship with a2a-sdk, ADK, Strands or LangGraph (spec lab10 §2, DEV-57).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from a2a.server.agent_execution import RequestContext
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

API_KEY_HEADER = "X-API-Key"


@dataclass
class Principal(BaseUser):
    """Who is calling: the display name the SDK reads as ``user.user_name`` plus the claims."""

    name: str
    method: str  # "bearer" | "api_key"
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def identity(self) -> str:
        return self.name


# ── server side ──────────────────────────────────────────────────────────────────────────────
class JwtVerifier:
    """Validates IdP-issued access tokens: signature (JWKS), issuer, audience and expiry.

    The JWKS is fetched lazily and once more if a token names an unknown ``kid`` (an IdP restart
    rotates its in-memory key, the same lesson as Lab 08's push receiver).
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        http: httpx.AsyncClient | None = None,
        leeway_s: int = 5,
    ) -> None:
        self.issuer, self.audience, self.jwks_url = issuer, audience, jwks_url
        self.leeway_s = leeway_s
        self._http = http or httpx.AsyncClient(timeout=5)
        self._keys: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        resp = await self._http.get(self.jwks_url)
        resp.raise_for_status()
        self._keys = {
            k["kid"]: jwt.PyJWK.from_dict(k).key for k in resp.json()["keys"] if "kid" in k
        }

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            kid = jwt.get_unverified_header(token).get("kid", "")
        except jwt.PyJWTError as exc:
            raise AuthenticationError("malformed bearer token") from exc
        if kid not in self._keys:
            async with self._lock:
                if kid not in self._keys:
                    try:
                        await self._refresh()
                    except httpx.HTTPError as exc:
                        raise AuthenticationError("the identity provider is unreachable") from exc
        if kid not in self._keys:
            raise AuthenticationError("token signed by an unknown key")
        try:
            return jwt.decode(
                token,
                self._keys[kid],
                algorithms=["ES256", "RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_s,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"invalid bearer token: {exc}") from exc


def claim_scopes(claims: Mapping[str, Any]) -> list[str]:
    raw = claims.get("scope", "")
    return raw.split() if isinstance(raw, str) else [str(s) for s in raw]


class BearerBackend(AuthenticationBackend):
    """``Authorization: Bearer <jwt>``. No header means anonymous; a bad token is a 401."""

    def __init__(self, verifier: JwtVerifier) -> None:
        self.verifier = verifier

    async def authenticate(self, conn: HTTPConnection) -> tuple[AuthCredentials, BaseUser] | None:
        header = conn.headers.get("Authorization")
        if header is None:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("expected 'Authorization: Bearer <token>'")
        claims = await self.verifier.verify(token.strip())
        name = str(claims.get("preferred_username") or claims["sub"])
        return AuthCredentials(claim_scopes(claims)), Principal(name, "bearer", claims)


class ApiKeyBackend(AuthenticationBackend):
    """``X-API-Key: <key>``: ``keys`` maps each key to ``(principal name, scopes)``."""

    def __init__(self, keys: Mapping[str, tuple[str, Sequence[str]]], header: str = API_KEY_HEADER):
        self.keys, self.header = dict(keys), header

    async def authenticate(self, conn: HTTPConnection) -> tuple[AuthCredentials, BaseUser] | None:
        key = conn.headers.get(self.header)
        if key is None:
            return None
        if key not in self.keys:
            raise AuthenticationError("unknown API key")
        name, scopes = self.keys[key]
        return AuthCredentials(list(scopes)), Principal(name, "api_key")


class ChainBackend(AuthenticationBackend):
    """The first backend that recognises the request wins; an invalid credential is final."""

    def __init__(self, *backends: AuthenticationBackend) -> None:
        self.backends = backends

    async def authenticate(self, conn: HTTPConnection) -> tuple[AuthCredentials, BaseUser] | None:
        for backend in self.backends:
            found = await backend.authenticate(conn)
            if found is not None:
                return found
        return None


def on_auth_error(_: Any, exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": "unauthenticated", "detail": str(exc)},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


class RequireAuth:
    """Pure-ASGI gate: HTTP 401 for anonymous callers, before any A2A handler (and so any task).

    ``public`` paths stay open: discovery (the well-known card) must work without credentials,
    otherwise a client could not even learn WHICH scheme to use.
    """

    def __init__(self, app: Any, public: Iterable[str] = (AGENT_CARD_WELL_KNOWN_PATH,)) -> None:
        self.app, self.public = app, tuple(public)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] not in self.public:
            user = scope.get("user")
            if user is None or not getattr(user, "is_authenticated", False):
                resp = JSONResponse(
                    {"error": "unauthenticated", "detail": "credentials required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


def auth_middleware(
    backend: AuthenticationBackend, *, public: Iterable[str] = (AGENT_CARD_WELL_KNOWN_PATH,)
) -> list[Middleware]:
    """The two middleware to pass to ``build_a2a_app(middleware=...)``, outermost first."""
    return [
        Middleware(AuthenticationMiddleware, backend=backend, on_error=on_auth_error),
        Middleware(RequireAuth, public=tuple(public)),
    ]


def caller(context: RequestContext) -> tuple[str, set[str]]:
    """``(principal name, scopes)`` of the request, as the SDK hands them to an executor."""
    call = context.call_context
    auth = call.state.get("auth") if call is not None else None
    scopes = set(getattr(auth, "scopes", []) or [])
    return (call.user.user_name if call is not None else ""), scopes


# ── client side ──────────────────────────────────────────────────────────────────────────────
class OAuthClientCredentialsAuth(httpx.Auth):
    """``httpx.Auth`` that fetches (and caches until shortly before expiry) a client token.

    Works anywhere an ``httpx.AsyncClient`` is accepted, which is how a Strands tool or a
    LangGraph node gets authenticated A2A calls without knowing about OAuth.
    """

    requires_response_body = True

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str = "",
        *,
        audience: str | None = None,
        refresh_margin_s: float = 30,
    ) -> None:
        self.token_url, self.client_id, self.client_secret = token_url, client_id, client_secret
        self.scope, self.audience, self.margin = scope, audience, refresh_margin_s
        self._token: str | None = None
        self._expires = 0.0
        self.fetches = 0

    async def token(self, http: httpx.AsyncClient | None = None) -> str:
        if self._token is None or time.time() > self._expires - self.margin:
            data = {"grant_type": "client_credentials", "scope": self.scope}
            if self.audience:
                data["audience"] = self.audience
            auth = (self.client_id, self.client_secret)
            if http is not None:  # a caller-owned client is never closed here
                resp = await http.post(self.token_url, data=data, auth=auth)
            else:
                async with httpx.AsyncClient(timeout=10) as own:
                    resp = await own.post(self.token_url, data=data, auth=auth)
            resp.raise_for_status()
            body = resp.json()
            self._token = str(body["access_token"])
            self._expires = time.time() + float(body.get("expires_in", 300))
            self.fetches += 1
        return self._token

    async def async_auth_flow(self, request: httpx.Request) -> Any:
        request.headers["Authorization"] = f"Bearer {await self.token()}"
        yield request


TokenProvider = Callable[[], Awaitable[str]]


def bearer_interceptor(token: Callable[[], str] | Callable[[], Awaitable[str]]) -> Any:
    """ADK ``RemoteA2aAgent`` request interceptor that adds ``Authorization: Bearer <token>``.

    ADK has no OAuth support of its own; its ``A2aRemoteAgentConfig.request_interceptors`` hook is
    the extension point, and ``ClientCallContext.service_parameters`` is how the SDK client turns
    a value into an HTTP header (the same slot ``AuthInterceptor`` writes to).
    """
    import inspect

    from a2a.client import ClientCallContext
    from google.adk.a2a.agent.config import RequestInterceptor

    async def before(_: Any, message: Any, params: Any) -> tuple[Any, Any]:
        value = token()
        if inspect.isawaitable(value):
            value = await value
        call = params.client_call_context or ClientCallContext()
        call.service_parameters = {
            **(call.service_parameters or {}),
            "Authorization": f"Bearer {value}",
        }
        params.client_call_context = call
        return message, params

    return RequestInterceptor(before_request=before)
