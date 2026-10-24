"""The research agent: an ADK agent whose only tool is slow."""

from __future__ import annotations

import asyncio
from typing import Any

from a2a_labs_common.models import get_adk_model

from lab08_push.agents.data import RESEARCH, seconds


async def research(topic: str) -> str:
    """Research a topic and return what was found.

    Args:
        topic: The subject to research, for example "solid-state batteries".
    """
    await asyncio.sleep(seconds("TOOL_SECONDS", 6))  # the long-running part
    key = next((k for k in RESEARCH if k in topic.lower()), "default")
    return RESEARCH[key]


def build_research_agent() -> Any:
    from google.adk.agents import Agent

    return Agent(
        name="researcher",
        model=get_adk_model("quick"),
        description="Researches a topic with a slow tool.",
        instruction="Call the research tool once with the user's topic, then report its result.",
        tools=[research],
    )
