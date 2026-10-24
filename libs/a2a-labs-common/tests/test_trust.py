"""Card signing and verification (lab06 §7 unit tests)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill
from a2a_labs_common.a2a.trust import (
    CardVerifier,
    TrustedKey,
    TrustStore,
    UntrustedAgentError,
    VerifiedCardResolver,
    logical_host,
    protected_header,
    sign_card,
)
from jwcrypto import jwk


def keypair(kid: str) -> tuple[dict[str, Any], dict[str, Any]]:
    k = jwk.JWK.generate(kty="EC", crv="P-256", kid=kid)
    return json.loads(k.export_private()), json.loads(k.export_public())


def card(
    name: str = "Translator A", service: str = "lab06-agent-a", streaming: bool = True
) -> AgentCard:
    return build_card(
        service=service,
        name=name,
        description="Translates",
        skills=[skill("translate", "Translate", "Translates text", ["translate"], ["hi"])],
        streaming=streaming,
    )


@pytest.fixture
def keys() -> dict[str, Any]:
    (a_priv, a_pub), (b_priv, b_pub), (r_priv, _) = keypair("a"), keypair("b"), keypair("rogue")
    store = TrustStore(
        trusted_keys=[
            TrustedKey(
                kid="a", jwk=a_pub, allowed_names=["Translator A"], allowed_hosts=["lab06-agent-a"]
            ),
            TrustedKey(
                kid="b", jwk=b_pub, allowed_names=["Translator B"], allowed_hosts=["lab06-agent-b"]
            ),
        ]
    )
    return {
        "a": a_priv,
        "b": b_priv,
        "rogue": r_priv,
        "verifier": CardVerifier(store),
        "store": store,
    }


URL_A = "http://lab06-agent-a:10602"


def test_sign_verify_round_trip(keys: dict[str, Any]) -> None:
    c = sign_card(card(), keys["a"], "a")
    report = keys["verifier"].verify(c, URL_A)
    assert report.ok and all(report.checks.values()) and report.kid == "a" and not report.reasons
    assert protected_header(c)["alg"] == "ES256" and "iat" in protected_header(c)


def test_unsigned_card_is_refused(keys: dict[str, Any]) -> None:
    report = keys["verifier"].verify(card(), URL_A)
    assert not report.ok and not report.checks["signature_present"]
    assert report.first_failure == "signature_present"


def test_tampering_after_signing_invalidates_the_signature(keys: dict[str, Any]) -> None:
    c = sign_card(card(), keys["a"], "a")
    c.description = "Translates and phones home"
    report = keys["verifier"].verify(c, URL_A)
    assert not report.ok and report.checks["key_trusted"] and not report.checks["signature_valid"]


def test_untrusted_kid(keys: dict[str, Any]) -> None:
    c = sign_card(card(name="Rogue"), keys["rogue"], "rogue")
    report = keys["verifier"].verify(c, URL_A)
    assert not report.checks["key_trusted"] and not report.ok
    assert any("not in the trust store" in r for r in report.reasons)


def test_a_trusted_key_cannot_speak_for_another_name(keys: dict[str, Any]) -> None:
    spoof = sign_card(card(name="Translator A", service="lab06-agent-b"), keys["b"], "b")
    report = keys["verifier"].verify(spoof, "http://lab06-agent-b:10603")
    assert report.checks["signature_valid"] and report.checks["key_trusted"]
    assert not report.checks["name_bound"] and not report.ok


def test_host_mismatch(keys: dict[str, Any]) -> None:
    c = sign_card(card(), keys["a"], "a")
    report = keys["verifier"].verify(c, "http://evil.example:10602")
    assert report.checks["signature_valid"] and not report.checks["host_bound"] and not report.ok
    moved = sign_card(card(service="lab06-agent-b"), keys["a"], "a")  # interfaces point elsewhere
    assert not keys["verifier"].verify(moved, URL_A).checks["interfaces_match_host"]


def test_stale_signature_and_missing_streaming(keys: dict[str, Any]) -> None:
    old = sign_card(card(), keys["a"], "a", iat=1)
    assert not keys["verifier"].verify(old, URL_A).checks["not_expired"]
    fresh = sign_card(card(streaming=False), keys["a"], "a")
    assert keys["verifier"].verify(fresh, URL_A).ok
    assert not keys["verifier"].verify(fresh, URL_A, require_streaming=True).ok


def test_the_jku_header_is_never_followed(keys: dict[str, Any]) -> None:
    """A rogue key advertised through jku must not make a card trusted."""
    from a2a.utils.signing import create_agent_card_signer
    from jwt import PyJWK

    c = card()
    create_agent_card_signer(
        PyJWK(keys["rogue"]),
        {"kid": "a", "alg": "ES256", "jku": "http://evil.example/jwks.json"},  # type: ignore[typeddict-item]
    )(c)
    assert not keys["verifier"].verify(c, URL_A).checks["signature_valid"]  # kid "a" is OUR key


def test_logical_host_maps_localhost_ports_to_service_names() -> None:
    assert logical_host("http://localhost:10602") == "lab06-agent-a"
    assert logical_host("http://lab06-agent-b:10603/") == "lab06-agent-b"
    assert logical_host("http://127.0.0.1:59999") == "127.0.0.1"


async def test_the_resolver_returns_only_verified_cards(
    keys: dict[str, Any], monkeypatch: Any
) -> None:
    from a2a_labs_common.a2a import trust

    good, bad = sign_card(card(), keys["a"], "a"), card()

    async def fake_fetch(target: str, http: Any = None) -> tuple[AgentCard, str]:
        return (good if target == "good" else bad), URL_A

    monkeypatch.setattr(trust, "fetch_raw_card", fake_fetch)
    resolver = VerifiedCardResolver(keys["verifier"])
    assert (await resolver.resolve("good")).name == "Translator A"
    with pytest.raises(UntrustedAgentError) as err:
        await resolver.resolve("bad")
    assert err.value.report.first_failure == "signature_present"
