"""Agent Card signing and verification (spec lab06 §4): decide who to trust BEFORE delegating.

A card is signed with JWS (RFC 7515) over its JCS-canonical JSON (RFC 8785) without the
``signatures`` field; the SDK's ``a2a.utils.signing`` does the canonicalisation and the crypto.
What this module adds is the part the SDK leaves to the caller: a **trust store** that decides
which keys count, which names and hosts each key may speak for, and how fresh a card must be.

The verifier never fetches a key from the card's own host (``jku``): a host that controls both
the card and the key proves nothing. Keys come from the trust store only.

Implements: spec lab06 §4. # CUSTOM: the SDK offers sign/verify primitives, not a trust policy.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from a2a.client import A2ACardResolver
from a2a.types import AgentCard
from a2a.utils.signing import (
    SignatureVerificationError,
    create_agent_card_signer,
    create_signature_verifier,
)
from google.protobuf.json_format import ParseDict
from jwt import PyJWK, PyJWTError
from opentelemetry import trace
from pydantic import BaseModel, Field

from a2a_labs_common import config

_tracer = trace.get_tracer("a2a_labs_common.trust")
ALG = "ES256"
CHECKS = (
    "signature_present",
    "key_trusted",  # before signature_valid: an unknown key cannot be checked, say why first
    "signature_valid",
    "name_bound",
    "host_bound",
    "interfaces_match_host",
    "not_expired",
    "capabilities_ok",
)


class TrustedKey(BaseModel):
    kid: str
    jwk: dict[str, Any]  # the PUBLIC key
    allowed_names: list[str]
    allowed_hosts: list[str]


class TrustStore(BaseModel):
    trusted_keys: list[TrustedKey] = Field(default_factory=list)
    require_signature: bool = True
    max_card_age_s: int = 86400

    @classmethod
    def load(cls, path: str | Path) -> TrustStore:
        return cls.model_validate_json(Path(path).read_text())

    def key(self, kid: str | None) -> TrustedKey | None:
        return next((k for k in self.trusted_keys if k.kid == kid), None)


class VerificationReport(BaseModel):
    ok: bool
    checks: dict[str, bool]
    kid: str | None = None
    reasons: list[str] = Field(default_factory=list)

    @property
    def first_failure(self) -> str | None:
        return next((c for c in CHECKS if not self.checks.get(c, False)), None)


class UntrustedAgentError(Exception):
    def __init__(self, report: VerificationReport, source: str = "") -> None:
        self.report = report
        super().__init__(f"untrusted agent {source}: {'; '.join(report.reasons)}")


def sign_card(
    card: AgentCard,
    private_jwk: dict[str, Any],
    kid: str,
    *,
    iat: int | None = None,
    alg: str = ALG,
) -> AgentCard:
    """Append a JWS signature (protected header: ``alg``, ``kid``, ``typ``, ``iat``) to ``card``."""
    header: Any = {"alg": alg, "kid": kid, "typ": "JOSE", "iat": int(iat or time.time())}
    return create_agent_card_signer(PyJWK(private_jwk), header)(card)


def protected_header(card: AgentCard, index: int = 0) -> dict[str, Any]:
    padded = card.signatures[index].protected + "=" * (-len(card.signatures[index].protected) % 4)
    raw = base64.urlsafe_b64decode(padded)
    return dict(json.loads(raw))


def logical_host(url: str) -> str:
    """The service a URL stands for: ``localhost:10602`` is ``lab06-agent-a`` on the host."""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        for entry in config._endpoints().values():
            if urlparse(str(entry["host_url"])).port == parsed.port:
                return urlparse(str(entry["docker_url"])).hostname or host
    return host


class CardVerifier:
    def __init__(self, trust_store: TrustStore) -> None:
        self.store = trust_store

    def verify(
        self, card: AgentCard, fetched_from: str, *, require_streaming: bool = False
    ) -> VerificationReport:
        with _tracer.start_as_current_span("registry.verify_card") as span:
            report = self._verify(card, fetched_from, require_streaming)
            span.set_attribute("a2a.card.name", card.name)
            span.set_attribute("a2a.card.signature.valid", report.checks["signature_valid"])
            span.set_attribute("a2a.card.trusted", report.ok)
            if report.first_failure:
                span.set_attribute("a2a.card.failed_check", report.first_failure)
            return report

    def _verify(
        self, card: AgentCard, fetched_from: str, require_streaming: bool
    ) -> VerificationReport:
        checks = dict.fromkeys(CHECKS, False)
        reasons: list[str] = []
        checks["signature_present"] = bool(card.signatures)
        if not card.signatures:
            reasons.append("card has no signature")
        kid, iat, key = self._pick_signature(card)
        if card.signatures and kid is None:
            reasons.append("signature has no readable kid")
        checks["key_trusted"] = key is not None
        if card.signatures and key is None:
            reasons.append(f"kid {kid!r} is not in the trust store")
        if key is not None:
            checks["signature_valid"] = self._signature_valid(card, key)
            if not checks["signature_valid"]:
                reasons.append("signature does not verify over the canonical card (tampered?)")
            checks["name_bound"] = card.name in key.allowed_names
            if not checks["name_bound"]:
                reasons.append(f"key {kid!r} may not speak for the name {card.name!r}")
            fetched = logical_host(fetched_from)
            checks["host_bound"] = fetched in key.allowed_hosts
            if not checks["host_bound"]:
                reasons.append(f"card fetched from {fetched!r}, not in {key.allowed_hosts}")
            hosts = {logical_host(i.url) for i in card.supported_interfaces}
            checks["interfaces_match_host"] = bool(hosts) and hosts <= set(key.allowed_hosts)
            if not checks["interfaces_match_host"]:
                reasons.append(f"interfaces point to {sorted(hosts)}, not {key.allowed_hosts}")
        age_ok = iat is not None and 0 <= time.time() - iat <= self.store.max_card_age_s
        checks["not_expired"] = bool(age_ok)
        if card.signatures and not age_ok:
            reasons.append("signature is missing its iat or is older than max_card_age_s")
        checks["capabilities_ok"] = card.capabilities.streaming or not require_streaming
        if not checks["capabilities_ok"]:
            reasons.append("streaming is required but not advertised")
        needed = CHECKS if self.store.require_signature else ("capabilities_ok",)
        return VerificationReport(
            ok=all(checks[c] for c in needed), checks=checks, kid=kid, reasons=reasons
        )

    def _pick_signature(self, card: AgentCard) -> tuple[str | None, int | None, TrustedKey | None]:
        """The first signature whose kid is trusted, else the first one (for the report)."""
        first: tuple[str | None, int | None, TrustedKey | None] = (None, None, None)
        for i in range(len(card.signatures)):
            try:
                header = protected_header(card, i)
            except (ValueError, KeyError):
                continue
            found = (header.get("kid"), header.get("iat"), self.store.key(header.get("kid")))
            if found[2] is not None:
                return found
            if i == 0:
                first = found
        return first

    @staticmethod
    def _signature_valid(card: AgentCard, key: TrustedKey) -> bool:
        # The key comes from the trust store; the header's `jku` is deliberately ignored.
        verifier = create_signature_verifier(lambda kid, jku: PyJWK(key.jwk), [ALG])
        try:
            verifier(card)
        except (SignatureVerificationError, PyJWTError, ValueError):
            return False
        return True


async def fetch_raw_card(
    service_or_url: str, http: httpx.AsyncClient | None = None
) -> tuple[AgentCard, str]:
    """Fetch a card WITHOUT rewriting its URLs (rewriting would break the signature)."""
    base = service_or_url if "://" in service_or_url else config.endpoint(service_or_url)
    if http is not None:
        return await A2ACardResolver(http, base).get_agent_card(), base
    async with httpx.AsyncClient(timeout=10) as client:
        return await A2ACardResolver(client, base).get_agent_card(), base


def card_from_json(data: dict[str, Any]) -> AgentCard:
    return ParseDict(data, AgentCard(), ignore_unknown_fields=True)


class VerifiedCardResolver:
    """Resolve, verify, then hand the card out; ``UntrustedAgentError`` otherwise."""

    def __init__(self, verifier: CardVerifier, *, require_streaming: bool = False) -> None:
        self.verifier, self.require_streaming = verifier, require_streaming

    async def resolve(
        self, service_or_url: str, http: httpx.AsyncClient | None = None
    ) -> AgentCard:
        card, base = await fetch_raw_card(service_or_url, http)
        report = self.verifier.verify(card, base, require_streaming=self.require_streaming)
        if not report.ok:
            raise UntrustedAgentError(report, base)
        return card
