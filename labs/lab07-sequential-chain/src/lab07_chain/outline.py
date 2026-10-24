"""The outline that travels from planner to writer as a DataPart (spec lab07 §2)."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError


class Section(BaseModel):
    heading: str
    key_points: list[str] = Field(default_factory=list)


class Outline(BaseModel):
    title: str
    audience: str = "general readers"
    sections: list[Section]


def render(outline: Outline) -> str:
    lines = [f"Title: {outline.title}", f"Audience: {outline.audience}"]
    for i, s in enumerate(outline.sections, 1):
        lines.append(f"{i}. {s.heading}")
        lines += [f"   - {p}" for p in s.key_points]
    return "\n".join(lines)


def fallback_outline(topic: str) -> Outline:
    """A deterministic outline, used when the model does not return valid JSON."""
    short = topic.strip().rstrip(".?!")[:80]
    return Outline(
        title=short,
        sections=[
            Section(heading="Introduction", key_points=[f"Why {short[:40]} matters"]),
            Section(heading="The main ideas", key_points=["The core idea", "A concrete example"]),
            Section(heading="Takeaways", key_points=["What to remember"]),
        ],
    )


def parse_outline(text: str, topic: str) -> Outline:
    start, end = text.find("{"), text.rfind("}")
    try:
        return Outline.model_validate(json.loads(text[start : end + 1]))
    except (ValueError, ValidationError):
        return fallback_outline(topic)


_VAGUE = re.compile(r"\b(it|this|that|something|anything|stuff)\b", re.I)


def is_vague(topic: str) -> bool:
    """Too little to plan from: a few words that point at nothing ("Write about it")."""
    words = topic.split()
    return len(words) < 5 and bool(_VAGUE.search(topic))
