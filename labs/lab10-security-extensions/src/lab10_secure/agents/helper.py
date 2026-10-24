"""The calendar helpers for the framework servers (ADK and Strands): one ``quick`` model call.

They exist to show that transport auth is NOT the agent's business: the same middleware protects
a framework agent that knows nothing about OAuth.
"""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_adk_model, get_strands_model

from lab10_secure.data import PUBLIC_CALENDAR, render

INSTRUCTION = (
    "You are a calendar helper. Answer questions about the public team calendar below in one "
    "friendly sentence. If asked about anything else, say you only know the public calendar.\n"
    "Public team calendar:\n" + render(PUBLIC_CALENDAR)
)


def build_adk_helper() -> Any:
    from google.adk.agents import Agent

    return Agent(
        name="calendar_helper",
        model=get_adk_model("quick"),
        description="Answers questions about the public team calendar.",
        instruction=INSTRUCTION,
    )


def build_strands_helper(context_id: str = "") -> Any:
    from strands import Agent

    return Agent(
        name="calendar_helper",
        model=get_strands_model("quick"),
        system_prompt=INSTRUCTION,
        callback_handler=None,
    )
