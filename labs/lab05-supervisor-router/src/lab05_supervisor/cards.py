"""Agent Cards for the three supervisors."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill

ASKS = [
    "Convert 300 CAD to EUR",
    "What is the weather in Tokyo and convert 50 EUR to USD, plus one fun fact about Tokyo.",
]


def supervisor_card() -> AgentCard:
    return build_card(
        service="lab05-supervisor",
        name="Travel Supervisor (LangGraph)",
        description="Discovers specialists from their Agent Cards, routes with a decision model.",
        skills=[
            skill(
                "supervise",
                "Route to specialists",
                "Routes a request to the currency, weather or city-facts specialist (or several "
                "in parallel), tracks each remote task in a delegation ledger, merges the answers.",
                ["supervisor", "router"],
                ASKS,
            )
        ],
    )


def coordinator_card() -> AgentCard:
    return build_card(
        service="lab05-adk-coordinator",
        name="Travel Coordinator (ADK)",
        description="An LLM coordinator that transfers the conversation to a remote specialist.",
        skills=[
            skill(
                "coordinate",
                "Transfer to a specialist",
                "The reasoning model picks one remote specialist and hands the request over.",
                ["supervisor", "router"],
                ASKS[:1],
            )
        ],
    )


def strands_graph_card() -> AgentCard:
    return build_card(
        service="lab05-strands-graph-supervisor",
        name="Travel Graph Supervisor (Strands)",
        description="A Strands Graph: a classifier node and conditional edges to remote agents.",
        skills=[
            skill(
                "graph_route",
                "Deterministic graph routing",
                "Classifies the request, follows the matching graph edge to one remote "
                "specialist, then summarises.",
                ["supervisor", "router"],
                ASKS[:1],
            )
        ],
    )
