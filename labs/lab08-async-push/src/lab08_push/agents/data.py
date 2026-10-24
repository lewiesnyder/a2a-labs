"""Small bundled data for the agents. Durations come from env so tests can set them to ~0."""

from __future__ import annotations

import os

PHASES = ["collect data", "clean it", "aggregate", "chart", "draft", "summarize"]
DOCS = {
    "Q3 sales memo": "Sales grew 12 percent, led by the EU. Churn fell to 2.1 percent.",
    "Incident 4411": "A cache outage lasted 18 minutes. Root cause: an expired certificate.",
    "Hiring plan": "Five engineers and two designers join in Q4. Budget is unchanged.",
    "Roadmap v2": "Push notifications ship first, then agent registries, then billing.",
    "Customer note": "The customer wants weekly reports and a shorter onboarding.",
}
RESEARCH = {
    "default": "No strong sources found; the topic needs more specific keywords.",
    "batteries": "Solid-state cells promise higher energy density; mass production is years away.",
    "vaccines": "mRNA vaccines teach cells a harmless protein that trains the immune system.",
}
PIPELINE = ["extract", "transform", "validate", "load"]


def seconds(name: str, default: float) -> float:
    return float(os.environ.get(name, default))
