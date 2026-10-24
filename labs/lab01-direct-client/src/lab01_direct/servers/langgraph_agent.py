"""LangGraph server: a compiled graph behind ``LangGraphA2AExecutor`` (the shared bridge).

Pattern 1, the Task side; ``context_id`` becomes the LangGraph ``thread_id``.
Implements: spec lab01 §2.3.
"""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor
    from a2a_labs_common.a2a.server import build_a2a_app

    from lab01_direct.agents.langgraph_agent import build_graph
    from lab01_direct.cards import langgraph_card

    return build_a2a_app(
        card=langgraph_card(),
        executor=LangGraphA2AExecutor(build_graph(), agent_name="trip-tip-langgraph"),
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab01-langgraph-agent", framework="langgraph", lab_id="lab01")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab01-langgraph-agent")


if __name__ == "__main__":
    main()
