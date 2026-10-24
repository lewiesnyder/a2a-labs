"""LangGraph coordinator: tools from ``MultiServerMCPClient`` (the bridge), served over A2A."""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor
    from a2a_labs_common.a2a.server import build_a2a_app

    from lab03_mcp.agents.consumers import build_langgraph_consumer
    from lab03_mcp.cards import consumer_card

    return build_a2a_app(
        card=consumer_card("lab03-langgraph-mcp-consumer", "MCP Coordinator (LangGraph)"),
        # graph_factory: the MCP tool list is fetched asynchronously, on the first request
        executor=LangGraphA2AExecutor(
            graph_factory=build_langgraph_consumer, agent_name="mcp-consumer-langgraph"
        ),
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab03-langgraph-mcp-consumer", framework="langgraph", lab_id="lab03")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab03-langgraph-mcp-consumer")


if __name__ == "__main__":
    main()
