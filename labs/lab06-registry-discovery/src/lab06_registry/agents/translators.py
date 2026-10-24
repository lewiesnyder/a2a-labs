"""The two honest translators: Translator A (Strands) and Translator B (ADK), both on ``quick``."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models import get_adk_model, get_strands_model

from lab06_registry.cards import A_LANGUAGES, B_LANGUAGES

PROMPT = (
    "You are a translator. Translate the user's text into the requested language(s) and reply "
    "with only the translations, one per line, labelled with the language. {limit}"
)


def build_strands_translator(context_id: str = "") -> Any:
    from strands import Agent

    return Agent(
        name="translator_a",
        model=get_strands_model("quick"),
        system_prompt=PROMPT.format(limit=f"You only know {', '.join(A_LANGUAGES)}."),
        callback_handler=None,
    )


def build_adk_translator() -> Any:
    from google.adk.agents import Agent

    return Agent(
        name="translator_b",
        model=get_adk_model("quick"),
        description="Translates short texts into many languages.",
        instruction=PROMPT.format(limit=f"You know {', '.join(B_LANGUAGES)}."),
    )
