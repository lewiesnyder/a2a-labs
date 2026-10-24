"""Weather specialist: a Strands agent with one private tool, ``get_forecast``."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_strands_model

from lab02_tool.agents.data import get_forecast

# Tuned against llama3.2 (3B): a single `city` parameter and an explicit "never ask questions"
# line took tool use from 0/3 to 3/3 in a direct test (pitfall M-3: small models need simple tools).
INSTRUCTION = (
    "You are a weather reporter. The user message always contains a city. Call the get_forecast "
    "tool with that city, then repeat the forecast it returns in one or two sentences. "
    "Never ask the user questions."
)


def build_weather_agent(context_id: str) -> Any:
    from strands import Agent, tool

    @tool(name="get_forecast")
    def get_forecast_tool(city: str) -> str:
        """Get the weather forecast for a city.

        Args:
            city: The city to look up, for example "Tokyo".
        """
        return get_forecast(city, 3)

    return Agent(
        name="weather_strands",
        description="Weather forecasts for a city.",
        model=get_strands_model("quick"),
        tools=[get_forecast_tool],
        system_prompt=INSTRUCTION,
        callback_handler=None,
    )
