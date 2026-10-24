"""Report agent (a2a-sdk): durable task and push-config stores in SQLite."""

from __future__ import annotations

from typing import Any


def build_app(**kw: Any) -> Any:
    from lab08_push.agents.report import ReportExecutor
    from lab08_push.cards import report_card
    from lab08_push.servers.common import push_app

    return push_app(report_card(), ReportExecutor(), service="lab08-sdk-report-agent", **kw)


def main() -> None:
    from lab08_push.servers.common import run_main

    run_main("lab08-sdk-report-agent", "a2a-sdk", lambda: build_app(durable=True))


if __name__ == "__main__":
    main()
