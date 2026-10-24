"""Agent Cards for the Lab 04 servers."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill


def planner_card() -> AgentCard:
    return build_card(
        service="lab04-langgraph-travel",
        name="Trip Planner",
        description="Plans flights, a hotel and an itinerary; asks when details are missing.",
        skills=[
            skill(
                "plan_trip",
                "Plan a trip",
                "Plans flights and a hotel within a budget; asks for missing details and, "
                "when the plan is risky, for approval.",
                ["travel", "planning"],
                ["Plan a trip from Chicago to Lisbon, 12-16 May, budget $2000, 2 travelers"],
            )
        ],
    )


def concierge_card() -> AgentCard:
    return build_card(
        service="lab04-langgraph-orchestrator",
        name="Concierge",
        description="Travel concierge: delegates planning to the Trip Planner, checks the budget.",
        skills=[
            skill(
                "concierge",
                "Travel concierge",
                "Gets a trip planned by a remote agent and checks it against the budget.",
                ["travel", "orchestrator"],
                ["Plan a trip from Chicago to Lisbon, 12-16 May, budget $2000, 2 travelers"],
            )
        ],
    )
