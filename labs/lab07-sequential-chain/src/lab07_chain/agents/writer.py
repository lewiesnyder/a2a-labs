"""The writer: a Strands agent on the reasoning model, one agent (= one memory) per context."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_strands_model

PROMPT = (
    "You are an article writer. Write clear markdown that follows the outline headings. "
    "When asked to revise, keep what works and fix exactly what the feedback says. "
    "Reply with the article only."
)


def build_writer(context_id: str = "") -> Any:
    from strands import Agent

    return Agent(
        name="article_writer",
        model=get_strands_model("reasoning"),
        system_prompt=PROMPT,
        callback_handler=None,
    )
