"""The ADK coordinator: an LLM routes by TRANSFERRING the conversation to a remote sub-agent.

Routing here is the reasoning model's ``transfer_to_agent`` tool call, so there is no probability
to inspect and no explicit ledger: ADK's ``RemoteA2aAgent`` quietly stamps the remote ``context_id``
and ``task_id`` on the session events it produces (``custom_metadata["a2a:context_id"]`` /
``["a2a:task_id"]``, ADK 2.13 ``_remote_a2a_agent.py``), and reads the context back from there on
the next turn. ``implicit_ledger`` below digs those stamps out so the notebook can set them next to
the LangGraph supervisor's explicit ledger.

ADK 2.x sub-agent modes: ``chat`` (the default; the sub-agent takes over the conversation and
answers the user directly), ``task`` and ``single_turn`` (the parent keeps control and gets a
result back). The remote agents are attached as chat-mode sub-agents because the point of this
supervisor is to hand the conversation over, including a remote INPUT_REQUIRED question.
Implements: spec lab05 §4.
"""

from __future__ import annotations

from typing import Any

from a2a_labs_common.a2a.remote import card_description, fetch_card_sync
from a2a_labs_common.models import get_adk_model

# agent name (what the LLM transfers to) -> specialist service. Names are fixed because the LLM
# addresses them; descriptions come from the Agent Cards, so they cannot drift from the specialist.
SUB_AGENTS = {
    "currency_agent": "lab05-currency",
    "weather_agent": "lab05-weather",
    "facts_agent": "lab05-facts",
}

INSTRUCTION = (
    "You are a travel-desk coordinator. Route to the right specialist: use currency_agent for "
    "money, weather_agent for weather and facts_agent for facts about a city. Transfer the "
    "conversation to exactly one specialist. If no specialist fits, answer briefly yourself."
)


def build_coordinator() -> Any:
    from google.adk.agents import Agent
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

    subs = []
    for name, service in SUB_AGENTS.items():
        card = fetch_card_sync(service)
        subs.append(
            RemoteA2aAgent(
                name=name,
                description=card_description(card),
                # A card passed as an object skips ADK's own fetch, which refuses docker DNS
                # names (DEV-20). use_legacy=False as in Lab 02.
                agent_card=card,
                use_legacy=False,
            )
        )
    return Agent(
        name="travel_coordinator",
        model=get_adk_model("reasoning"),
        description="Routes travel questions to remote specialists.",
        instruction=INSTRUCTION,
        sub_agents=subs,
    )


def implicit_ledger(events: list[Any]) -> dict[str, dict[str, Any]]:
    """What ADK remembered per remote sub-agent, read off the session events."""
    out: dict[str, dict[str, Any]] = {}
    for ev in events:
        meta = getattr(ev, "custom_metadata", None) or {}
        if "a2a:context_id" not in meta and "a2a:task_id" not in meta:
            continue
        row = out.setdefault(ev.author, {"agent": ev.author, "context_id": None, "task_ids": []})
        row["context_id"] = meta.get("a2a:context_id") or row["context_id"]
        task = meta.get("a2a:task_id")
        if task and task not in row["task_ids"]:
            row["task_ids"].append(task)
    return out
