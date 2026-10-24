"""Agent Cards for the four long-running agents. All declare push and streaming."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill


def _card(service: str, name: str, description: str, sid: str, example: str) -> AgentCard:
    return build_card(
        service=service,
        name=name,
        description=description,
        push_notifications=True,
        streaming=True,
        skills=[skill(sid, name, description, ["long-running", "push"], [example])],
    )


def report_card() -> AgentCard:
    return _card(
        "lab08-sdk-report-agent",
        "Report Agent",
        "Builds a quarterly report in six phases (slow on purpose).",
        "quarterly_report",
        "Build the Q3 report",
    )


def batch_card() -> AgentCard:
    return _card(
        "lab08-strands-batch-agent",
        "Batch Summarizer",
        "Summarises a batch of documents one by one.",
        "summarize_batch",
        "Summarize the batch",
    )


def research_card() -> AgentCard:
    return _card(
        "lab08-adk-research-agent",
        "Research Agent",
        "Researches a topic with a slow tool.",
        "research",
        "Research solid-state batteries",
    )


def pipeline_card() -> AgentCard:
    return _card(
        "lab08-langgraph-pipeline-agent",
        "Pipeline Agent",
        "Runs a four-step data pipeline: extract, transform, validate, load.",
        "run_pipeline",
        "Run the nightly pipeline",
    )
