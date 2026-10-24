"""Strands agent. One instance per ``context_id`` (see ``StrandsA2AExecutor``, DEV-1)."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_strands_model

from lab01_direct.agents.common import DESCRIPTION, INSTRUCTION, local_time_data


def build_strands_agent(context_id: str) -> Any:
    from strands import Agent, tool

    @tool
    def local_time(city: str) -> str:
        """Return the current local time for a city.

        Args:
            city: Name of the city, for example "Lisbon".
        """
        return local_time_data(city)

    return Agent(
        name="trip_tip_strands",
        description=DESCRIPTION,
        model=get_strands_model("quick"),
        tools=[local_time],
        system_prompt=INSTRUCTION,
        callback_handler=None,  # no stdout streaming inside a server
    )
