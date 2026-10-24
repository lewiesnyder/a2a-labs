"""Squad `lab09-squad-compliance` (adk). A plain A2A agent that announces itself to its gateway."""

from __future__ import annotations

from typing import Any

SERVICE = "lab09-squad-compliance"


def build_app(**kw: Any) -> Any:
    from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
    from google.adk.artifacts import InMemoryArtifactService
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    from lab09_agp.agents.squad_agents import build_adk

    agent = build_adk(SERVICE)
    runner = Runner(
        app_name=agent.name,
        agent=agent,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    executor = A2aAgentExecutor(runner=runner, force_new_version=True)

    from lab09_agp.servers.common import squad_app

    return squad_app(SERVICE, executor, **kw)


def main() -> None:
    from lab09_agp.servers.common import run_main

    run_main(SERVICE, "adk", build_app)


if __name__ == "__main__":
    main()
