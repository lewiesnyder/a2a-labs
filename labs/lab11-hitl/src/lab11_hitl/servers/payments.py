"""Payments delegate (a2a-sdk): decision-model risk gate, human approval."""

from __future__ import annotations

from typing import Any

SERVICE = "lab11-payments"


def build_app(**kw: Any) -> Any:
    from lab11_hitl.agents.payments import PaymentsExecutor
    from lab11_hitl.servers.common import delegate_app

    return delegate_app(SERVICE, PaymentsExecutor(**kw))


def main() -> None:
    from lab11_hitl.servers.common import run_main

    run_main(SERVICE, "a2a-sdk", build_app)


if __name__ == "__main__":
    main()
