"""Engineering Helper: a Strands agent with MCP tools, exposed over A2A (``StrandsA2AExecutor``)."""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab03_mcp.agents.specialists import build_engineer_agent
    from lab03_mcp.cards import engineer_card

    return build_a2a_app(card=engineer_card(), executor=StrandsA2AExecutor(build_engineer_agent))


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab03-strands-specialist", framework="strands", lab_id="lab03")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab03-strands-specialist")


if __name__ == "__main__":
    main()
