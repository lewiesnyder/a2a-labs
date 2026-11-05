"""Hotels delegate (LangGraph): interrupt() for the choice."""

from __future__ import annotations

from typing import Any

SERVICE = "lab11-hotels"


def build_app(**kw: Any) -> Any:
    from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor

    from lab11_hitl.agents.hotels import build_graph, extra
    from lab11_hitl.servers.common import delegate_app

    return delegate_app(
        SERVICE,
        LangGraphA2AExecutor(build_graph(), agent_name="hotels", extra_artifacts=extra, **kw),
    )


def main() -> None:
    from lab11_hitl.servers.common import run_main

    run_main(SERVICE, "langgraph", build_app)


if __name__ == "__main__":
    main()
