"""Pipeline agent (LangGraph): node updates become WORKING statuses, each one pushable."""

from __future__ import annotations

from typing import Any


def build_app(**kw: Any) -> Any:
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor

    from lab08_push.agents.pipeline import build_graph
    from lab08_push.cards import pipeline_card
    from lab08_push.servers.common import push_app

    executor = LangGraphA2AExecutor(build_graph(), agent_name="pipeline", emit_node_updates=True)
    return push_app(pipeline_card(), executor, service="lab08-langgraph-pipeline-agent", **kw)


def main() -> None:
    from lab08_push.servers.common import run_main

    run_main("lab08-langgraph-pipeline-agent", "langgraph", build_app)


if __name__ == "__main__":
    main()
