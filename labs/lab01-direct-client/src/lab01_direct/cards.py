"""Agent Cards for the three servers that build their own (ADK generates its card)."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill

TRIP_TIP = skill(
    "trip_tip",
    "Trip Tip",
    "Given a city, returns one practical travel tip and the local time.",
    ["travel", "tips"],
    ["Give me a tip for Lisbon", "What should I know before visiting Tokyo?"],
)


def sdk_echo_card() -> AgentCard:
    return build_card(
        service="lab01-sdk-echo",
        name="SDK Echo",
        description="Message-only agent built directly on the a2a-sdk: echoes your text back.",
        skills=[skill("echo", "Echo", "Echoes the user's text.", ["demo"], ["hello"])],
        streaming=True,  # declared, yet every reply is a single Message
    )


def strands_card() -> AgentCard:
    return build_card(
        service="lab01-strands-agent",
        name="Trip Tip (Strands)",
        description="Strands agent that gives one practical travel tip for a city.",
        skills=[TRIP_TIP],
    )


def langgraph_card() -> AgentCard:
    return build_card(
        service="lab01-langgraph-agent",
        name="Trip Tip (LangGraph)",
        description="LangGraph agent that gives one practical travel tip for a city.",
        skills=[TRIP_TIP],
    )
