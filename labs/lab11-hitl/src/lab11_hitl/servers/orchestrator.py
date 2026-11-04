"""Trip Desk orchestrator server."""

from __future__ import annotations

from typing import Any

SERVICE = "lab11-orchestrator"


def build_app(**kw: Any) -> Any:
    from lab11_hitl.servers.common import orchestrator_app

    return orchestrator_app(**kw)


def main() -> None:
    from lab11_hitl.servers.common import run_main

    run_main(SERVICE, "a2a-sdk", build_app)


if __name__ == "__main__":
    main()
