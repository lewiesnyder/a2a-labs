"""Unit tests for the webhook receiver (in-process ASGI, no network)."""

from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from a2a_labs_webhook import app as webhook
from cryptography.hazmat.primitives.asymmetric import rsa
from jwcrypto import jwk

TASK = {"task": {"id": "t1", "contextId": "c1", "status": {"state": "TASK_STATE_WORKING"}}}


def client(**kw: object) -> tuple[httpx.AsyncClient, webhook.Store]:
    store = webhook.Store()
    app = webhook.create_app(store=store, **kw)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r"), store


async def test_receives_and_lists() -> None:
    c, _ = client(require_auth=False)
    assert (await c.post("/webhooks/a2a", json=TASK)).status_code == 200
    await c.post("/webhooks/a2a/chan", json=TASK)
    items = (await c.get("/notifications", params={"task_id": "t1"})).json()
    assert [i["channel"] for i in items] == ["default", "chan"]
    assert items[0]["state"] == "WORKING" and items[0]["kind"] == "task"
    assert len((await c.get("/notifications", params={"channel": "chan"})).json()) == 1
    assert len((await c.get("/notifications", params={"since": 1})).json()) == 1
    assert (await c.delete("/notifications")).json()["status"] == "cleared"
    assert (await c.get("/healthz")).json() == {"status": "ok"}


async def test_bad_body_is_400() -> None:
    c, _ = client(require_auth=False)
    assert (await c.post("/webhooks/a2a", content=b"not json")).status_code == 400


async def test_token_check_and_required_auth() -> None:
    c, _ = client(require_auth=True)
    assert (await c.post("/webhooks/a2a", json=TASK)).status_code == 401  # no credentials at all
    await c.post("/expectations", json={"task_id": "t1", "token": "s3cret"})
    bad = await c.post("/webhooks/a2a", json=TASK, headers={webhook.TOKEN_HEADER: "nope"})
    assert bad.status_code == 401
    good = await c.post("/webhooks/a2a", json=TASK, headers={webhook.TOKEN_HEADER: "s3cret"})
    assert good.status_code == 200
    assert (await c.get("/notifications")).json()[0]["token_valid"] is True


async def test_jwt_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key = jwk.JWK.from_pyca(priv)
    key.update({"kid": "k1", "alg": "RS256", "use": "sig"})
    jwks = {"keys": [json.loads(key.export_public())]}
    c, store = client(require_auth=True)

    async def fake_jwks(url: str) -> dict[str, object]:
        return jwks

    monkeypatch.setattr(store, "jwks", fake_jwks)
    await c.post("/expectations", json={"task_id": "t1", "agent_jwks_url": "http://agent/jwks"})
    body = json.dumps(TASK).encode()

    def token(iat: float, **extra: object) -> str:
        return jwt.encode({"iat": iat, **extra}, priv, algorithm="RS256", headers={"kid": "k1"})

    ok = await c.post(
        "/webhooks/a2a", content=body, headers={"Authorization": f"Bearer {token(time.time())}"}
    )
    assert ok.status_code == 200
    stale = await c.post(
        "/webhooks/a2a",
        content=body,
        headers={"Authorization": f"Bearer {token(time.time() - 4000)}"},
    )
    assert stale.status_code == 401
    mismatch = await c.post(
        "/webhooks/a2a",
        content=body,
        headers={"Authorization": f"Bearer {token(time.time(), request_body_sha256='x')}"},
    )
    assert mismatch.status_code == 401
    wrong_kid = jwt.encode({"iat": time.time()}, priv, algorithm="RS256", headers={"kid": "zzz"})
    assert (
        await c.post(
            "/webhooks/a2a", content=body, headers={"Authorization": f"Bearer {wrong_kid}"}
        )
    ).status_code == 401
