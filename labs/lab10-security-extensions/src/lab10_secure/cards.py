"""Agent Cards for the Lab 10 servers. The secure agent's card carries the security story."""

from __future__ import annotations

from a2a.types import (
    AgentCard,
    AgentInterface,
    APIKeySecurityScheme,
    ClientCredentialsOAuthFlow,
    DeviceCodeOAuthFlow,
    OAuth2SecurityScheme,
    OAuthFlows,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a_labs_common.a2a.cards import build_card, skill
from a2a_labs_common.a2a.extensions import extension
from a2a_labs_common.auth import API_KEY_HEADER
from a2a_labs_common.config import public_url

from lab10_secure.data import BUDGET_EXT, TRACE_EXT


def requirement(**schemes: list[str]) -> SecurityRequirement:
    """``requirement(oauth2=["agent:invoke"])``. Several keys in ONE requirement mean AND."""
    return SecurityRequirement(schemes={k: StringList(list=v) for k, v in schemes.items()})


def security_schemes() -> dict[str, SecurityScheme]:
    idp = public_url("lab10-mock-idp")
    return {
        # A2A: credentials always travel in HTTP headers; the card only says WHICH and HOW.
        "api_key": SecurityScheme(
            api_key_security_scheme=APIKeySecurityScheme(
                description="Static key for service-to-service use.",
                location="header",
                name=API_KEY_HEADER,
            )
        ),
        # A2A: OAuthFlows is a protobuf ONEOF, so one scheme can describe one flow only. The agent
        # call (client credentials) and the user's consent (device code) are two schemes (DEV-59).
        "oauth2": SecurityScheme(
            oauth2_security_scheme=OAuth2SecurityScheme(
                description="Client-credentials token from the lab IdP.",
                oauth2_metadata_url=f"{idp}/.well-known/openid-configuration",
                flows=OAuthFlows(
                    client_credentials=ClientCredentialsOAuthFlow(
                        token_url=f"{idp}/token",
                        scopes={"agent:invoke": "Call the agent", "agent:admin": "Admin skills"},
                    )
                ),
            )
        ),
        "user_consent": SecurityScheme(
            oauth2_security_scheme=OAuth2SecurityScheme(
                description="The user's consent to read private data (device-code flow).",
                oauth2_metadata_url=f"{idp}/.well-known/openid-configuration",
                flows=OAuthFlows(
                    device_code=DeviceCodeOAuthFlow(
                        device_authorization_url=f"{idp}/device_authorization",
                        token_url=f"{idp}/token",
                        scopes={"calendar:read": "Read the user's private calendar"},
                    )
                ),
            )
        ),
    }


def _skills(*, extended: bool = False, admin: bool = False) -> list:  # type: ignore[type-arg]
    any_auth = [requirement(api_key=[]), requirement(oauth2=["agent:invoke"])]  # a list is OR
    skills = [
        skill(
            "calendar.read_public",
            "Public calendar",
            "Reads the shared team calendar. Any authenticated caller.",
            ["calendar", "public"],
            ["What's on the public team calendar?"],
        ),
        skill(
            "calendar.read_private",
            "Private calendar",
            "Reads the user's private appointments. Needs the user's consent (device flow).",
            ["calendar", "private"],
            ["Show my private appointments"],
        ),
    ]
    skills[0].security_requirements.extend(any_auth)
    skills[1].security_requirements.append(requirement(user_consent=["calendar:read"]))
    if extended:
        s = skill(
            "calendar.free_busy",
            "Free / busy",
            "Team free/busy windows. Only in the extended card.",
            ["calendar", "extended"],
            ["When is the team free on Thursday?"],
        )
        s.security_requirements.extend(any_auth)
        skills.append(s)
    if admin:
        s = skill(
            "calendar.admin_stats",
            "Admin statistics",
            "Calendar counts. Needs the agent:admin scope.",
            ["calendar", "admin"],
            ["Show admin stats"],
        )
        s.security_requirements.append(requirement(oauth2=["agent:admin"]))
        skills.append(s)
    return skills


def secure_card() -> AgentCard:
    return build_card(
        service="lab10-secure-agent",
        name="Calendar Assistant",
        description=(
            "Answers calendar questions. Every call is authenticated; private data needs consent."
        ),
        skills=_skills(),
        streaming=True,
        push_notifications=True,
        extended_agent_card=True,
        security_schemes=security_schemes(),
        security_requirements=[requirement(api_key=[]), requirement(oauth2=["agent:invoke"])],
    )


def secure_extended_card(*, admin: bool = False) -> AgentCard:
    """What an authenticated caller may read with ``GetExtendedAgentCard``."""
    card = secure_card()
    del card.skills[:]
    card.skills.extend(_skills(extended=True, admin=admin))
    card.version = "1.0.0+extended"
    return card


def framework_card(service: str, name: str, framework: str) -> AgentCard:
    return build_card(
        service=service,
        name=name,
        description=f"Calendar helper built with {framework}, served behind bearer-token auth.",
        skills=[
            skill(
                "calendar.read_public",
                "Public calendar",
                "Answers questions about the public team calendar.",
                ["calendar", framework.lower()],
                ["What's on the public team calendar?"],
            )
        ],
        security_schemes={"oauth2": security_schemes()["oauth2"]},
        security_requirements=[requirement(oauth2=["agent:invoke"])],
    )


def ext_card() -> AgentCard:
    """Two JSON-RPC interfaces: v1.0 at ``/`` and the old v0.3 shape at ``/v03``."""
    card = build_card(
        service="lab10-ext-agent",
        name="Trace Reporter",
        description="Answers questions; adds a trace-summary artifact when that extension is on.",
        skills=[
            skill(
                "ext.ask",
                "Ask",
                "Answers a short question. Activate the trace-summary extension for a summary.",
                ["extension", "tracing"],
                ["What is the capital of Portugal?"],
            )
        ],
        extensions=[
            extension(
                TRACE_EXT,
                "Adds a `trace-summary` artifact (span count, durations, models used).",
                params={"artifact": "trace-summary"},
            )
        ],
    )
    base = card.supported_interfaces[0]
    card.supported_interfaces.append(
        AgentInterface(
            url=base.url.rstrip("/") + "/v03",
            protocol_binding="JSONRPC",
            protocol_version="0.3",
        )
    )
    return card


def required_ext_card() -> AgentCard:
    return build_card(
        service="lab10-required-ext-agent",
        name="Budgeted Summarizer",
        description="Does paid work. The cost-budget extension is REQUIRED on every request.",
        skills=[
            skill(
                "budget.summarize",
                "Summarize (paid)",
                "Summarises text at a fixed price. Requests carry a spending cap in metadata.",
                ["extension", "budget"],
                ["Summarize: A2A lets agents talk to each other."],
            )
        ],
        extensions=[
            extension(
                BUDGET_EXT,
                "Every request must state `max_usd`; the agent refuses work above that price.",
                required=True,
                params={"currency": "USD"},
            )
        ],
    )
