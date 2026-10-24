"""Agent Card builders and validation (spec 03 §2).

Implements: spec 03 §2. Interfaces are derived from ``config/endpoints.yaml``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    SecurityRequirement,
    SecurityScheme,
)

from a2a_labs_common.config import public_url, settings

Binding = Literal["JSONRPC", "HTTP+JSON", "GRPC"]
REST_PREFIX = "/rest"
DEFAULT_PROVIDER = AgentProvider(
    organization="A2A Labs", url="https://github.com/a2a-labs/a2a-labs"
)


def skill(
    id: str,
    name: str,
    description: str,
    tags: Sequence[str],
    examples: Sequence[str],
    input_modes: Sequence[str] | None = None,
    output_modes: Sequence[str] | None = None,
) -> AgentSkill:
    return AgentSkill(
        id=id,
        name=name,
        description=description,
        tags=tags,
        examples=examples,
        input_modes=input_modes or [],
        output_modes=output_modes or [],
    )


def _url(service: str, binding: Binding) -> str:
    if binding == "JSONRPC":
        return public_url(service)
    if binding == "HTTP+JSON":
        return public_url(service) + REST_PREFIX  # A2A: REST shares the HTTP port under a prefix
    return public_url(f"{service}-grpc")


def interfaces(service: str, bindings: Sequence[Binding]) -> list[AgentInterface]:
    version = settings().a2a_version
    return [
        AgentInterface(url=_url(service, b), protocol_binding=b, protocol_version=version)
        for b in bindings
    ]


def build_card(
    *,
    service: str,
    name: str,
    description: str,
    skills: Sequence[AgentSkill],
    version: str = "1.0.0",
    streaming: bool = True,
    push_notifications: bool = False,
    extended_agent_card: bool = False,
    extensions: Sequence[AgentExtension] | None = None,
    bindings: Sequence[Binding] = ("JSONRPC",),
    input_modes: Sequence[str] = ("text/plain",),
    output_modes: Sequence[str] = ("text/plain",),
    security_schemes: Mapping[str, SecurityScheme] | None = None,
    security_requirements: Sequence[SecurityRequirement] | None = None,
    provider: AgentProvider | None = DEFAULT_PROVIDER,
) -> AgentCard:
    """Builds a v1.0 AgentCard (``supported_interfaces`` replaces the removed ``url``)."""
    card = AgentCard(
        name=name,
        description=description,
        version=version,
        supported_interfaces=interfaces(service, bindings),
        capabilities=AgentCapabilities(
            streaming=streaming,
            push_notifications=push_notifications,
            extended_agent_card=extended_agent_card,
            extensions=list(extensions or []),
        ),
        default_input_modes=list(input_modes),
        default_output_modes=list(output_modes),
        skills=list(skills),
        security_requirements=list(security_requirements or []),
    )
    if provider is not None:
        card.provider.CopyFrom(provider)
    for key, scheme in (security_schemes or {}).items():
        card.security_schemes[key].CopyFrom(scheme)
    return card


def validate_card(card: AgentCard) -> list[str]:
    """Human-readable problems; an empty list means the card is fine."""
    problems: list[str] = []
    if not card.supported_interfaces:
        problems.append("no supported_interfaces")
    for i in card.supported_interfaces:
        if not i.url or not i.protocol_binding or not i.protocol_version:
            problems.append(f"incomplete interface {i.protocol_binding or '?'}")
    if not card.skills:
        problems.append("no skills")
    ids = [s.id for s in card.skills]
    problems += [f"duplicate skill id {d}" for d in sorted({x for x in ids if ids.count(x) > 1})]
    problems += [
        f"skill {s.id} has no tags (required by the spec)" for s in card.skills if not s.tags
    ]
    if len(card.description) < 20:
        problems.append("description shorter than 20 characters")
    return problems
