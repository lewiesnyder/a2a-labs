"""Agent Cards: squads are plain agents; gateways declare the AGP extension (spec §4)."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill
from a2a_labs_common.agp_gateway import agp_extension

from lab09_agp.squads import SQUADS

DOMAINS = {
    "lab09-root-gateway": (
        "Root Gateway",
        "Routes Intents to the finance and travel gateways.",
        ["finance", "travel"],
    ),
    "lab09-finance-gateway": (
        "Finance Gateway",
        "Routes finance capabilities to its squads.",
        ["finance"],
    ),
    "lab09-travel-gateway": (
        "Travel Gateway",
        "Routes travel capabilities to its squads.",
        ["travel"],
    ),
}


def squad_card(service: str) -> AgentCard:
    squad = SQUADS[service]
    return build_card(
        service=service,
        name=f"{squad.name} Squad",
        description=f"{squad.description} A plain A2A agent; it only announces to a gateway.",
        streaming=False,
        skills=[
            skill(a.capability, a.capability, squad.description, ["agp", "squad"], [a.capability])
            for a in squad.announcements
        ],
    )


def gateway_card(service: str) -> AgentCard:
    name, desc, domains = DOMAINS[service]
    return build_card(
        service=service,
        name=name,
        description=f"{desc} EXPERIMENTAL: speaks the Agent Gateway Protocol extension.",
        streaming=False,
        extensions=[agp_extension()],
        skills=[
            skill(
                f"route.{d}",
                f"Route {d} intents",
                f"Policy-aware routing of {d}.* capabilities.",
                ["agp", "gateway", d],
                [f"{d}.* intent"],
            )
            for d in domains
        ],
    )
