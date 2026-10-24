"""The mock IdP: discovery, client credentials, device flow and the token reference."""

from __future__ import annotations

import httpx
import jwt
from lab10_secure.clients import approve, client_token
from lab10_secure.idp import AUDIENCE, DEVICE_GRANT, REF_GRANT

AGENT = ("lab10-secure-agent", "lab10-agent-secret")


def test_discovery_and_jwks_describe_the_issuer(stack) -> None:
    base = stack["lab10-mock-idp"].url
    disco = httpx.get(f"{base}/.well-known/openid-configuration").json()
    assert disco["issuer"] == base and disco["jwks_uri"] == f"{base}/jwks"
    assert DEVICE_GRANT in disco["grant_types_supported"]
    keys = httpx.get(f"{base}/jwks").json()["keys"]
    assert keys[0]["alg"] == "ES256" and keys[0]["kid"]


def test_client_credentials_tokens_carry_issuer_audience_scope_and_expiry(stack) -> None:
    claims = jwt.decode(client_token(), options={"verify_signature": False})
    assert claims["iss"] == stack["lab10-mock-idp"].url
    assert claims["aud"] == AUDIENCE and claims["scope"] == "agent:invoke"
    assert claims["exp"] > claims["iat"] and claims["sub"] == "lab10-client"


def test_a_client_cannot_get_scopes_it_was_not_registered_for(stack) -> None:
    base = stack["lab10-mock-idp"].url
    r = httpx.post(
        f"{base}/token",
        data={"grant_type": "client_credentials", "scope": "agent:admin"},
        auth=("lab10-client", "lab10-secret"),
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_scope"
    bad = httpx.post(
        f"{base}/token", data={"grant_type": "client_credentials"}, auth=("lab10-client", "nope")
    )
    assert bad.status_code == 401


def _device(base: str) -> dict:
    r = httpx.post(f"{base}/device_authorization", data={"scope": "calendar:read"}, auth=AGENT)
    assert r.status_code == 200
    return r.json()


def test_device_flow_is_pending_until_the_user_approves(stack) -> None:
    base = stack["lab10-mock-idp"].url
    dev = _device(base)
    poll = {"grant_type": DEVICE_GRANT, "device_code": dev["device_code"]}
    pending = httpx.post(f"{base}/token", data=poll, auth=AGENT)
    assert pending.status_code == 400 and pending.json()["error"] == "authorization_pending"
    approve(dev["user_code"])
    done = httpx.post(f"{base}/token", data=poll, auth=AGENT).json()
    claims = jwt.decode(done["access_token"], options={"verify_signature": False})
    assert claims["sub"] == "alice" and claims["scope"] == "calendar:read"


def test_a_denied_device_request_never_yields_a_token(stack) -> None:
    base = stack["lab10-mock-idp"].url
    dev = _device(base)
    httpx.post(f"{base}/device/deny", data={"user_code": dev["user_code"]})
    r = httpx.post(
        f"{base}/token",
        data={"grant_type": DEVICE_GRANT, "device_code": dev["device_code"]},
        auth=AGENT,
    )
    assert r.json()["error"] == "access_denied"


def test_a_token_reference_is_single_use_and_bound_to_the_requesting_client(stack) -> None:
    base = stack["lab10-mock-idp"].url
    dev = _device(base)
    ref = approve(dev["user_code"])
    grant = {"grant_type": REF_GRANT, "token_ref": ref}
    thief = httpx.post(f"{base}/token", data=grant, auth=("lab10-client", "lab10-secret"))
    assert thief.status_code == 400  # another client redeeming it gets nothing
    # a wrong redeemer burns the reference too (pessimistic by design): the owner must retry
    assert httpx.post(f"{base}/token", data=grant, auth=AGENT).status_code == 400
    ref2 = approve(_device(base)["user_code"])
    ok = httpx.post(f"{base}/token", data={**grant, "token_ref": ref2}, auth=AGENT)
    assert ok.status_code == 200
    again = httpx.post(f"{base}/token", data={**grant, "token_ref": ref2}, auth=AGENT)
    assert again.status_code == 400


def test_healthz_and_the_device_page(stack) -> None:
    base = stack["lab10-mock-idp"].url
    assert httpx.get(f"{base}/healthz").json() == {"ok": True}
    dev = _device(base)
    page = httpx.get(f"{base}/device", params={"user_code": dev["user_code"]}).text
    assert "calendar:read" in page
    assert "user_code" in httpx.get(f"{base}/device").text
