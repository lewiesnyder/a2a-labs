"""The three controllers: each one turns remote A2A agents into tools its own way.

Pattern 2: the controller's LLM decides when to call a remote agent; the remote agent keeps its
own task lifecycle and stays opaque (only its Agent Card and Artifacts cross the boundary).
Implements: spec lab02 §2, §3.
"""

from __future__ import annotations

from typing import Any

from a2a_labs_common.config import endpoint
from a2a_labs_common.models import get_adk_model, get_langchain_model, get_strands_model

CURRENCY = "lab02-langgraph-currency"
WEATHER = "lab02-strands-weather"
FACTS = "lab02-adk-facts"

INSTRUCTION = (
    "You are a travel-desk controller. You have specialist agents available as tools: currency, "
    "weather and city facts. Call the right specialist for each part of the user's request, then "
    "write ONE final answer that includes every result. If a tool replies NEEDS_INPUT, ask the "
    "user that exact question and wait; when they answer, call the same tool again with their "
    "answer. If a tool replies ERROR, say so plainly instead of guessing."
)


# ── LangChain ───────────────────────────────────────────────────────────────────────────────
def build_langchain_controller() -> Any:
    """LangGraph agent whose tools are generated from the remote Agent Cards."""
    from a2a_labs_common.a2a.langchain_tool import a2a_agent_as_langchain_tool
    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import InMemorySaver

    tools = [
        a2a_agent_as_langchain_tool(CURRENCY, name="ask_currency"),
        a2a_agent_as_langchain_tool(WEATHER, name="ask_weather"),
        a2a_agent_as_langchain_tool(FACTS, name="ask_facts"),
    ]
    return create_agent(
        get_langchain_model("reasoning"),
        tools=tools,
        system_prompt=INSTRUCTION,
        checkpointer=InMemorySaver(),  # thread_id (= A2A context_id) keys the remote contexts too
        name="travel_desk_langchain",
    )


# ── Strands ─────────────────────────────────────────────────────────────────────────────────
def build_strands_controller(context_id: str) -> Any:
    """One agent per context, so the stateful tools keep one remote conversation each."""
    from a2a_labs_common.a2a.strands_tools import make_a2a_client_tool, make_stateful_a2a_tool
    from strands import Agent

    # Stateless and multi-endpoint, with an ALLOWLIST: the LLM cannot call arbitrary URLs.
    call_specialist = make_a2a_client_tool(
        name="call_specialist",
        description=(
            "Call a remote specialist agent by URL with a plain-language message. "
            f"Allowed: currency {endpoint(CURRENCY)}, weather {endpoint(WEATHER)}, "
            f"city facts {endpoint(FACTS)}."
        ),
        allowed_endpoints={
            endpoint(CURRENCY): None,
            endpoint(WEATHER): None,
            endpoint(FACTS): None,
        },
    )
    # Stateful: remembers its remote context and any task waiting for input.
    ask_weather = make_stateful_a2a_tool(
        name="ask_weather", service=WEATHER, description="Ask the weather specialist (stateful)."
    )
    ask_currency = make_stateful_a2a_tool(
        name="ask_currency",
        service=CURRENCY,
        description="Ask the currency specialist (stateful; use it to answer its questions).",
    )
    return Agent(
        name="travel_desk_strands",
        description="Travel desk controller.",
        model=get_strands_model("reasoning"),
        tools=[call_specialist, ask_weather, ask_currency],
        system_prompt=INSTRUCTION,
        callback_handler=None,
    )


# ── ADK ─────────────────────────────────────────────────────────────────────────────────────
def remote_agent(name: str, description: str, service: str) -> Any:
    from a2a_labs_common.a2a.remote import fetch_card_sync
    from google.adk.agents.remote_a2a_agent import RemoteA2aAgent

    return RemoteA2aAgent(
        name=name,
        description=description,
        # ADK refuses to FETCH a card from a non-https, non-loopback URL (SSRF guard), which
        # rules out docker DNS names. A card passed in as an object is left to the caller to
        # trust, which is right inside a private compose network (DEV-20).
        agent_card=fetch_card_sync(service),
        # ADK-1: use_legacy defaults to True and use_legacy=False is ineffective on a2a-sdk 1.x
        # (adk-python #7334); the verification log records what this build actually does.
        use_legacy=False,
    )


def build_adk_controller() -> Any:
    """Currency and weather as TOOLS (controller stays in charge); facts as a SUB-AGENT."""
    from google.adk.agents import Agent
    from google.adk.tools.agent_tool import AgentTool

    currency = remote_agent("currency_agent", "Converts amounts between currencies.", CURRENCY)
    weather = remote_agent("weather_agent", "Reports the weather forecast for a city.", WEATHER)
    facts = remote_agent("facts_agent", "Gives interesting facts about a city.", FACTS)
    # The SAME remote currency agent, wired a second way. AgentTool discards the nested session,
    # so a remote agent that pauses (INPUT_REQUIRED) cannot be resumed through it; as a
    # sub-agent the conversation is handed over and the follow-up reaches the paused task.
    currency_desk = remote_agent(
        "currency_desk",
        "Currency conversion conversations that may need follow-up questions.",
        CURRENCY,
    )
    return Agent(
        name="travel_desk_adk",
        model=get_adk_model("reasoning"),
        description="Travel desk controller.",
        instruction=INSTRUCTION
        + " Use the tools for weather and for complete one-shot currency conversions. If a currency"
        " request is missing any detail (for example the target currency), do NOT ask the user"
        " yourself: transfer to currency_desk immediately and let it ask. For city facts,"
        " transfer to facts_agent.",
        tools=[AgentTool(agent=currency), AgentTool(agent=weather)],
        sub_agents=[facts, currency_desk],
    )
