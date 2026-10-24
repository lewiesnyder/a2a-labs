"""Agent Cards for the Lab 03 A2A servers that build their own (ADK generates Travel Clock's)."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill


def engineer_card() -> AgentCard:
    return build_card(
        service="lab03-strands-specialist",
        name="Engineering Helper",
        description="Converts units and does arithmetic for engineering questions.",
        skills=[
            skill(
                "engineering",
                "Units and arithmetic",
                "Converts units (length, mass, volume, temperature) and evaluates arithmetic.",
                ["units", "math"],
                ["How many feet in 3.2 km, and what is 12% of that?"],
            )
        ],
    )


def clock_card() -> AgentCard:
    """Hand-built on purpose: ADK's generated card lists every MCP tool as a skill (a leak)."""
    return build_card(
        service="lab03-adk-specialist",
        name="Travel Clock",
        description="Compares local times between cities.",
        skills=[
            skill(
                "clock",
                "Time comparison",
                "Tells what time it is in one city when it is a given time in another.",
                ["time", "travel"],
                ["What time is it in Tokyo when it is 9am in Chicago?"],
            )
        ],
    )


def consumer_card(service: str, name: str) -> AgentCard:
    return build_card(
        service=service,
        name=name,
        description="Coordinator that only sees MCP tools yet delegates to A2A specialists.",
        skills=[
            skill(
                "coordinate",
                "Coordinate specialists",
                "Answers composite questions by calling A2A specialists through an MCP bridge.",
                ["coordinator", "mcp"],
                ["Feet in 3.2 km, and the time in Tokyo when it is 9am in Chicago?"],
            )
        ],
    )
