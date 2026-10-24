"""LangChain controller server: a LangGraph agent with card-derived A2A tools, exposed over A2A.

Implements: spec lab02 §2 (``lab02-langchain-controller``).
"""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor
    from a2a_labs_common.a2a.server import build_a2a_app

    from lab02_tool.agents.controllers import build_langchain_controller
    from lab02_tool.cards import controller_card

    return build_a2a_app(
        card=controller_card("lab02-langchain-controller", "Travel Desk (LangChain)"),
        executor=LangGraphA2AExecutor(build_langchain_controller(), agent_name="travel-desk"),
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab02-langchain-controller", framework="langgraph", lab_id="lab02")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab02-langchain-controller")


if __name__ == "__main__":
    main()
