"""City-facts specialist: an ADK agent with one private tool, ``lookup_fact``."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_adk_model

from lab02_tool.agents.data import lookup_fact as _lookup


def lookup_fact(city: str) -> str:
    """Return one interesting fact about a city.

    Args:
        city: City name, for example "Lisbon".
    """
    return _lookup(city)


def build_facts_agent() -> Any:
    from google.adk.agents import Agent

    return Agent(
        name="facts_adk",
        model=get_adk_model("quick"),
        description="Interesting facts about cities.",
        instruction="ALWAYS call lookup_fact with the city the user names, then state the fact.",
        tools=[lookup_fact],
    )
