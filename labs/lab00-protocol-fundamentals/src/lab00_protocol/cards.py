"""Agent Cards for the playground variants (spec lab00 §4).

Implements: lab00 §4. One module, three variants: ``full``, ``nostream`` and ``nopush``.
"""

from __future__ import annotations

import os
from typing import Literal

from a2a.types import (
    AgentCard,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a_labs_common.a2a.cards import Binding, build_card, skill

Variant = Literal["full", "nostream", "nopush"]
SERVICES: dict[str, str] = {
    "full": "lab00-protocol-agent",
    "nostream": "lab00-protocol-agent-nostream",
    "nopush": "lab00-protocol-agent-nopush",
}
ADMIN_SCHEME = "adminBearer"

DESCRIPTION = (
    "Deterministic agent for learning A2A. Commands: help, echo <text>, work [n], slow [s], ask, "
    "auth, fail, reject, parts, multi, crash, refs, stats (authenticated)."
)


def _admin_scheme() -> dict[str, SecurityScheme]:
    return {
        ADMIN_SCHEME: SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="bearer")
        )
    }


def _skills() -> list:
    return [
        skill(
            "playground.commands",
            "Playground commands",
            "Scripted commands that walk through every task state.",
            ["demo", "protocol"],
            ["work 3", "ask", "echo hi"],
        ),
        skill(
            "playground.parts",
            "Part gallery",
            "Returns text, raw bytes, URL and JSON data parts.",
            ["demo", "parts"],
            ["parts"],
            output_modes=["text/plain", "image/png", "application/json"],
        ),
    ]


def playground_card(service: str, variant: Variant) -> AgentCard:
    bindings: tuple[Binding, ...] = (
        ("JSONRPC", "HTTP+JSON", "GRPC") if variant == "full" else ("JSONRPC",)
    )
    return build_card(
        service=service,
        name="Protocol Playground Agent",
        description=DESCRIPTION,
        skills=_skills(),
        streaming=variant != "nostream",
        push_notifications=variant != "nopush",
        extended_agent_card=variant == "full",
        bindings=bindings,
        input_modes=("text/plain", "application/json"),
        output_modes=("text/plain", "application/json", "image/png"),
        # A2A: the scheme is declared on the public card; only the extended card requires it.
        security_schemes=_admin_scheme(),
    )


def playground_extended_card(service: str) -> AgentCard:
    """Same card plus the admin skill, served to callers presenting the bearer token."""
    card = playground_card(service, "full")
    card.skills.append(
        skill(
            "playground.admin",
            "Admin stats",
            "Task counts by state; needs the admin bearer token.",
            ["admin"],
            ["stats"],
        )
    )
    card.security_requirements.append(
        SecurityRequirement(schemes={ADMIN_SCHEME: StringList(list=[])})
    )
    return card


def admin_token() -> str:
    return os.environ.get("PLAYGROUND_ADMIN_TOKEN", "lab00-admin")
