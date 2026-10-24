"""Agent Cards for the specialists that build their own (ADK generates the facts card).

The card is the ONLY thing a controller learns about a specialist: skills and descriptions,
never its tools, prompts or model. That is the opacity the lab is about.
"""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill


def currency_card(service: str = "lab02-langgraph-currency") -> AgentCard:
    return build_card(
        service=service,
        name="Currency Converter",
        description="Converts amounts between currencies; asks when the target is missing.",
        skills=[
            skill(
                "convert",
                "Currency conversion",
                "Converts an amount from one currency to another using a fixed rate table.",
                ["currency", "finance"],
                ["Convert 100 USD to EUR", "How much is 250 GBP in JPY?"],
            )
        ],
    )


def weather_card(service: str = "lab02-strands-weather") -> AgentCard:
    return build_card(
        service=service,
        name="Weather Reporter",
        description="Reports the weather forecast for a city for up to three days.",
        skills=[
            skill(
                "forecast",
                "Weather forecast",
                "Returns a short forecast for a named city.",
                ["weather", "forecast"],
                ["What is the weather in Tokyo?"],
            )
        ],
    )


def controller_card(service: str, name: str) -> AgentCard:
    """Controllers are A2A servers too, so the notebook and other labs can call them."""
    return build_card(
        service=service,
        name=name,
        description="Travel-desk controller delegating currency, weather and facts to specialists.",
        skills=[
            skill(
                "travel_desk",
                "Travel desk",
                "Answers trip questions by calling currency, weather and city-facts specialists.",
                ["travel", "controller"],
                ["What's 250 GBP in JPY, and what's the weather in Tokyo?"],
            )
        ],
    )


def facts_card(service: str = "lab02-adk-facts") -> AgentCard:
    """Hand-built for the ADK facts agent: ADK's own card lists its tool as a skill (a leak)."""
    return build_card(
        service=service,
        name="City Facts",
        description="Gives interesting facts about a city.",
        skills=[
            skill(
                "facts",
                "City facts",
                "Returns one interesting fact about a named city.",
                ["facts", "travel"],
                ["Tell me a fun fact about Lisbon"],
            )
        ],
    )
