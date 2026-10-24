"""ADK agent. ADK-2: always pass ``model=`` explicitly (ADK >= 2.2 defaults to Gemini)."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_adk_model

from lab01_direct.agents.common import DESCRIPTION, INSTRUCTION, local_time_data


def local_time(city: str) -> str:
    """Return the current local time for a city.

    Args:
        city: Name of the city, for example "Lisbon".
    """
    return local_time_data(city)


def build_adk_agent() -> Any:
    from google.adk.agents import Agent

    return Agent(
        name="trip_tip_adk",
        model=get_adk_model("quick"),
        description=DESCRIPTION,
        instruction=INSTRUCTION,
        tools=[local_time],
    )
