"""Flights delegate (a2a-sdk)."""

from __future__ import annotations

from typing import Any

SERVICE = "lab11-flights"


def build_app(**kw: Any) -> Any:
    from lab11_hitl.agents.flights import FlightsExecutor
    from lab11_hitl.servers.common import delegate_app

    return delegate_app(SERVICE, FlightsExecutor(**kw))


def main() -> None:
    from lab11_hitl.servers.common import run_main

    run_main(SERVICE, "a2a-sdk", build_app)


if __name__ == "__main__":
    main()
