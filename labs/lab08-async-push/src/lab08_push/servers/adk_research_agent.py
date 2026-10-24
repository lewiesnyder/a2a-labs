"""Research agent (ADK).

``to_a2a`` accepts a ``push_config_store`` but has no parameter for a push SENDER, so it can
store configs and never deliver them. The handler is therefore built with our server stack
around ADK's own ``A2aAgentExecutor`` (see docs/spec-deviations.md DEV-52).
"""

from __future__ import annotations

from typing import Any


def build_app(**kw: Any) -> Any:
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    from lab08_push.agents.research import build_research_agent
    from lab08_push.cards import research_card
    from lab08_push.servers.common import push_app

    agent = build_research_agent()
    runner = Runner(
        app_name=agent.name,
        agent=agent,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    return push_app(
        research_card(),
        A2aAgentExecutor(runner=runner, force_new_version=True),
        service="lab08-adk-research-agent",
        **kw,
    )


def main() -> None:
    from lab08_push.servers.common import run_main

    run_main("lab08-adk-research-agent", "adk", build_app)


if __name__ == "__main__":
    main()
