"""Bearer-token authentication for the extended card and ``stats`` (spec lab00 §4).

# CUSTOM: the SDK has no guard for the extended card. It does give every request handler a
# ``ServerCallContext.user`` filled from Starlette's authentication, so we plug in a tiny
# ``AuthenticationBackend``. Lab 10 builds the full version (``a2a_labs_common.auth``).
"""

from __future__ import annotations

from typing import Any

from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    SimpleUser,
)
from starlette.requests import HTTPConnection


class AdminBearerBackend(AuthenticationBackend):
    def __init__(self, token: str) -> None:
        self.token = token

    async def authenticate(self, conn: HTTPConnection) -> tuple[AuthCredentials, SimpleUser] | None:
        header = conn.headers.get("Authorization")
        if header is None:
            return None  # anonymous callers are fine; only admin features check the user
        scheme, _, credentials = header.partition(" ")
        if scheme.lower() != "bearer" or credentials != self.token:
            raise AuthenticationError("invalid bearer token")
        return AuthCredentials(["admin"]), SimpleUser("admin")


def on_auth_error(_: Any, exc: Exception) -> Any:
    from starlette.responses import JSONResponse

    return JSONResponse({"error": str(exc)}, status_code=401)


# JSON-RPC names (v1.0 and v0.3) and REST paths that return the extended card.
_EXTENDED_RPC = {"GetExtendedAgentCard", "agent/getAuthenticatedExtendedCard"}
_EXTENDED_PATHS = ("/extendedAgentCard", "/v1/card")


class ExtendedCardGuard:
    """Pure-ASGI guard: only authenticated callers may fetch the extended card.

    # CUSTOM: the SDK serves the extended card to anyone who asks and has no 'unauthenticated'
    # A2A error, so we answer HTTP 401 ourselves (spec lab00 §4, DEV-8). JSON-RPC multiplexes
    # every method on ``POST /``, so we peek at the body and replay it for the real handler.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or "admin" in scope.get("auth", AuthCredentials()).scopes:
            await self.app(scope, receive, send)
            return
        replay = receive
        if scope["method"] == "POST":
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            guarded = _method_of(body) in _EXTENDED_RPC

            replayed = False

            async def replay() -> dict[str, Any]:  # type: ignore[misc]
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                # After the body, defer to the real channel: SSE handlers poll it for a
                # client disconnect and must not see an endless stream of request messages.
                return await receive()  # type: ignore[no-any-return]

        else:
            guarded = scope["path"].endswith(_EXTENDED_PATHS)
        if guarded:
            from starlette.responses import JSONResponse

            resp = JSONResponse({"error": "extended card needs the admin bearer token"}, 401)
            await resp(scope, receive, send)
            return
        await self.app(scope, replay, send)


def _method_of(body: bytes) -> str:
    import json

    try:
        data = json.loads(body)
        return str(data.get("method", "")) if isinstance(data, dict) else ""
    except ValueError:
        return ""
