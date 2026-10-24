"""The planner: an ADK agent on the reasoning model that answers with outline JSON."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_adk_model

INSTRUCTION = (
    "You plan articles. Reply with ONLY a JSON object: "
    '{"title": str, "audience": str, "sections": [{"heading": str, "key_points": [str, ...]}]}. '
    "Use 3 or 4 sections and 2 or 3 key points each. No prose, no markdown fences."
)


def build_planner() -> Any:
    from google.adk.agents import Agent

    return Agent(
        name="outline_planner",
        model=get_adk_model("reasoning"),
        description="Plans an article outline as JSON.",
        instruction=INSTRUCTION,
    )
