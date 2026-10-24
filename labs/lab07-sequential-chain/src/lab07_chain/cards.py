"""Agent Cards for the Lab 07 agents."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill


def planner_card() -> AgentCard:
    return build_card(
        service="lab07-adk-planner",
        name="Outline Planner",
        description="Turns a topic into a structured outline (title, audience, sections).",
        skills=[
            skill(
                "plan_outline",
                "Plan an outline",
                "Returns an `outline` artifact: a DataPart {title, audience, sections} plus text.",
                ["writing", "planning"],
                ["Explain how vaccines work"],
            )
        ],
    )


def writer_card() -> AgentCard:
    return build_card(
        service="lab07-strands-writer",
        name="Article Writer",
        description="Writes a markdown article from an outline; revises it from editor feedback.",
        skills=[
            skill(
                "write_article",
                "Write an article",
                "Takes an outline DataPart (and optional feedback) and streams a `draft` artifact.",
                ["writing"],
                ["Write the article from the attached outline."],
            )
        ],
    )


def editor_card() -> AgentCard:
    return build_card(
        service="lab07-langgraph-editor",
        name="Editor",
        description="Edits a draft and reviews it.",
        skills=[
            skill(
                "edit_draft",
                "Edit and review",
                "Returns a `final` text artifact and a `review` DataPart {issues, summary}.",
                ["writing", "editing"],
                ["Edit this draft"],
            )
        ],
    )


def orchestrator_card() -> AgentCard:
    return build_card(
        service="lab07-chain-orchestrator",
        name="Chain Orchestrator",
        description="Runs planner, writer and editor in order, with a quality loop.",
        skills=[
            skill(
                "write_with_review",
                "Plan, write and edit",
                "Runs the three hops, scores the editor's review and revises up to twice. "
                "Returns `final` and `provenance` artifacts.",
                ["writing", "orchestration"],
                ["Explain how vaccines work in three paragraphs"],
            )
        ],
    )
