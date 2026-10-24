"""Strands server: a Strands agent behind the standard a2a-sdk server.

Pattern 1, the Task side. ``StrandsA2AExecutor`` is our ``# CUSTOM`` stand-in for Strands'
``A2AServer`` (which cannot import on a2a-sdk 1.x, DEV-1). ``agent_factory`` gives one agent
per ``context_id`` with LRU eviction, which is the multi-tenant-safe behaviour pitfall S-1 asks for.
Implements: spec lab01 §2.2.
"""

from __future__ import annotations


def build_app():  # type: ignore[no-untyped-def]
    from a2a_labs_common.a2a.server import build_a2a_app
    from a2a_labs_common.a2a.strands import StrandsA2AExecutor

    from lab01_direct.agents.strands_agent import build_strands_agent
    from lab01_direct.cards import strands_card

    return build_a2a_app(
        card=strands_card(), executor=StrandsA2AExecutor(build_strands_agent, max_contexts=32)
    )


def main() -> None:
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry("lab01-strands-agent", framework="strands", lab_id="lab01")
    from a2a_labs_common.a2a.server import run

    run(build_app(), service="lab01-strands-agent")


if __name__ == "__main__":
    main()
