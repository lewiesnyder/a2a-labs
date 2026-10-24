"""Wiring shared by the Lab 10 servers: the IdP-backed bearer middleware and the entry point."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.auth import BearerBackend, auth_middleware
from starlette.middleware import Middleware

from lab10_secure.idp_client import make_verifier


def bearer_middleware() -> list[Middleware]:
    """Validate IdP-issued JWTs (issuer, audience, expiry, signature); the card stays public."""
    return auth_middleware(BearerBackend(make_verifier()))


def run_main(service: str, framework: str, build: Any) -> None:
    from a2a_labs_common.a2a.server import run
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry(service, framework=framework, lab_id="lab10")  # first call in main()
    run(build(), service=service)
