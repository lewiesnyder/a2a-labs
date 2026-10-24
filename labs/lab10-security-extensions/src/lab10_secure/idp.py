"""A tiny OAuth2 / OIDC issuer for the lab (``lab10-mock-idp``).

# CUSTOM: the spec prefers ``ghcr.io/navikt/mock-oauth2-server`` and allows this fallback. The
# navikt server has no RFC 8628 device-authorization endpoint, which the AUTH_REQUIRED demo needs
# (DEV-57). Everything else here is the standard shape: discovery, JWKS, ``client_credentials``,
# ``device_code`` and a signed ES256 access token.

Plus ONE lab-specific endpoint: a token *reference*. When the user approves a device request the
approver gets an opaque, single-use ``access_token_ref``. The client puts the reference (never the
token) in an A2A message, and the agent that started the device flow redeems it at the IdP with
its own client credentials. So no user token ever travels through an A2A payload.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from jwt.algorithms import ECAlgorithm

AUDIENCE = "a2a-labs-lab10"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
REF_GRANT = "urn:a2a-labs:grant-type:token-ref"
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"


def default_clients() -> dict[str, dict[str, Any]]:
    """Dev-only credentials; every secret can be overridden from the environment."""
    return {
        "lab10-client": {
            "secret": os.environ.get("LAB10_CLIENT_SECRET", "lab10-secret"),
            "scopes": ["agent:invoke"],
        },
        "lab10-admin-client": {
            "secret": os.environ.get("LAB10_ADMIN_SECRET", "lab10-admin-secret"),
            "scopes": ["agent:invoke", "agent:admin"],
        },
        # the calendar agent: it starts device flows and redeems token references
        "lab10-secure-agent": {
            "secret": os.environ.get("LAB10_AGENT_SECRET", "lab10-agent-secret"),
            "scopes": ["calendar:read"],
        },
    }


class Idp:
    def __init__(self, issuer: str, *, ttl_s: float = 300, clients: dict[str, Any] | None = None):
        self.issuer, self.ttl_s = issuer, ttl_s
        self.clients = clients or default_clients()
        key = ec.generate_private_key(ec.SECP256R1())
        self._key = key
        self.kid = f"mock-idp-{uuid.uuid4().hex[:8]}"
        public = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(key.public_key()))
        self.public_jwk = {**public, "kid": self.kid, "alg": "ES256", "use": "sig"}
        self.devices: dict[str, dict[str, Any]] = {}  # device_code -> request state
        self.refs: dict[str, dict[str, Any]] = {}  # access_token_ref -> device_code
        self.issued: list[str] = []  # every token handed out (tests scan task output for these)

    # ── tokens ───────────────────────────────────────────────────────────────────────────
    def mint(self, *, sub: str, client_id: str, scope: str, audience: str, **extra: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": sub,
            "aud": audience,
            "scope": scope,
            "client_id": client_id,
            "iat": now,
            "exp": now + int(self.ttl_s),
            "jti": uuid.uuid4().hex,
            **extra,
        }
        token = jwt.encode(claims, self._key, algorithm="ES256", headers={"kid": self.kid})
        self.issued.append(token)
        return token

    def token_response(self, token: str, scope: str) -> dict[str, Any]:
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": int(self.ttl_s),
            "scope": scope,
        }

    def authenticate(self, request: Request, form: dict[str, Any]) -> str:
        """Client authentication: HTTP Basic or ``client_id``/``client_secret`` form fields."""
        import base64

        client_id, secret = form.get("client_id"), form.get("client_secret")
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("basic "):
            raw = base64.b64decode(header[6:]).decode()
            client_id, _, secret = raw.partition(":")
        client = self.clients.get(str(client_id))
        if client is None or (client.get("secret") and secret != client["secret"]):
            raise HTTPException(401, {"error": "invalid_client"})
        return str(client_id)


def _granted(client: dict[str, Any], requested: str) -> str:
    wanted = requested.split() or list(client["scopes"])
    denied = [s for s in wanted if s not in client["scopes"]]
    if denied:
        raise HTTPException(400, {"error": "invalid_scope", "detail": f"not allowed: {denied}"})
    return " ".join(wanted)


def create_app(idp: Idp) -> FastAPI:
    app = FastAPI(title="lab10-mock-idp")
    app.state.idp = idp

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/.well-known/openid-configuration")
    def discovery() -> dict[str, Any]:
        return {
            "issuer": idp.issuer,
            "jwks_uri": f"{idp.issuer}/jwks",
            "token_endpoint": f"{idp.issuer}/token",
            "device_authorization_endpoint": f"{idp.issuer}/device_authorization",
            "grant_types_supported": ["client_credentials", DEVICE_GRANT, REF_GRANT],
            "id_token_signing_alg_values_supported": ["ES256"],
            "scopes_supported": ["agent:invoke", "agent:admin", "calendar:read"],
        }

    @app.get("/jwks")
    def jwks() -> dict[str, Any]:
        return {"keys": [idp.public_jwk]}

    @app.post("/device_authorization")
    async def device_authorization(
        request: Request, scope: str = Form(""), audience: str = Form(AUDIENCE)
    ) -> dict[str, Any]:
        client_id = idp.authenticate(request, dict(await request.form()))
        granted = _granted(idp.clients[client_id], scope)
        device_code = secrets.token_urlsafe(24)
        user_code = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
        user_code = f"{user_code[:4]}-{user_code[4:]}"
        idp.devices[device_code] = {
            "client_id": client_id,
            "scope": granted,
            "audience": audience,
            "user_code": user_code,
            "status": "pending",
            "expires": time.time() + 600,
        }
        return {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": f"{idp.issuer}/device",
            "verification_uri_complete": f"{idp.issuer}/device?user_code={user_code}",
            "expires_in": 600,
            "interval": 1,
        }

    def _by_user_code(user_code: str) -> tuple[str, dict[str, Any]]:
        for code, dev in idp.devices.items():
            if dev["user_code"] == user_code and dev["expires"] > time.time():
                return code, dev
        raise HTTPException(404, {"error": "unknown or expired user_code"})

    @app.get("/device", response_class=PlainTextResponse)
    def device_page(user_code: str = "") -> str:
        # The page a person would open in a browser. In the notebook a cell plays the person.
        if not user_code:
            return "Enter the code shown by your agent: POST /device/approve user_code=XXXX-XXXX"
        _, dev = _by_user_code(user_code)
        return f"{dev['client_id']} asks for: {dev['scope']}. POST /device/approve to allow."

    @app.post("/device/approve")
    def approve(user_code: str = Form(...), user: str = Form("alice")) -> dict[str, Any]:
        """The user's consent. Returns the single-use reference the CLIENT hands to the agent."""
        code, dev = _by_user_code(user_code)
        dev.update(status="approved", user=user)
        ref = "tokref_" + secrets.token_urlsafe(18)
        idp.refs[ref] = {"device_code": code, "expires": time.time() + 120}
        return {"approved": True, "user": user, "access_token_ref": ref}

    @app.post("/device/deny")
    def deny(user_code: str = Form(...)) -> dict[str, bool]:
        _, dev = _by_user_code(user_code)
        dev["status"] = "denied"
        return {"denied": True}

    @app.post("/token")
    async def token(request: Request, grant_type: str = Form(...)) -> JSONResponse:
        form = dict(await request.form())
        client_id = idp.authenticate(request, form)
        client = idp.clients[client_id]
        if grant_type == "client_credentials":
            scope = _granted(client, str(form.get("scope", "")))
            tok = idp.mint(
                sub=client_id,
                client_id=client_id,
                scope=scope,
                audience=str(form.get("audience") or AUDIENCE),
            )
            return JSONResponse(idp.token_response(tok, scope))
        if grant_type in (DEVICE_GRANT, REF_GRANT):
            if grant_type == DEVICE_GRANT:
                code = str(form.get("device_code", ""))
            else:
                ref = idp.refs.pop(str(form.get("token_ref", "")), None)  # single use
                if ref is None or ref["expires"] < time.time():
                    raise HTTPException(400, {"error": "invalid_grant", "detail": "bad token_ref"})
                code = ref["device_code"]
            dev = idp.devices.get(code)
            if dev is None or dev["client_id"] != client_id:
                raise HTTPException(400, {"error": "invalid_grant"})
            if dev["status"] == "pending":
                raise HTTPException(400, {"error": "authorization_pending"})
            if dev["status"] != "approved":
                raise HTTPException(400, {"error": "access_denied"})
            tok = idp.mint(
                sub=dev["user"],
                client_id=client_id,
                scope=dev["scope"],
                audience=dev["audience"],
                preferred_username=dev["user"],
            )
            return JSONResponse(idp.token_response(tok, dev["scope"]))
        raise HTTPException(400, {"error": "unsupported_grant_type"})

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        body = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return JSONResponse(body, status_code=exc.status_code)

    return app
