"""One place that turns an executor into a push-capable A2A app (spec lab08 §2)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from a2a.types import AgentCard
from a2a_labs_common.a2a.push import (
    JwtPushNotificationSender,
    PushSigner,
    add_jwks_route,
    make_push_url_validator,
)
from a2a_labs_common.a2a.server import build_a2a_app


def sqlite_engine(service: str) -> Any:
    """A SQLite engine under ``LAB08_DB_DIR`` (a compose volume), one file per service."""
    from sqlalchemy.ext.asyncio import create_async_engine

    root = Path(os.environ.get("LAB08_DB_DIR", "/tmp/lab08"))
    root.mkdir(parents=True, exist_ok=True)
    return create_async_engine(f"sqlite+aiosqlite:///{root / (service + '.db')}")


def push_app(
    card: AgentCard,
    executor: Any,
    *,
    service: str,
    durable: bool = False,
    backoff_s: float = 0.2,
) -> Any:
    """``build_a2a_app(push=True)`` with the JWT sender, the SSRF guard and a published JWKS."""
    signer = PushSigner(service)
    validator = make_push_url_validator()
    extra: dict[str, Any] = {}
    if durable:  # tasks AND push configs survive a restart of the agent
        from a2a.server.tasks import DatabasePushNotificationConfigStore, DatabaseTaskStore

        engine = sqlite_engine(service)
        extra = {
            "task_store": DatabaseTaskStore(engine),
            "push_config_store": DatabasePushNotificationConfigStore(engine),
        }

    def sender(client: httpx.AsyncClient, store: Any) -> JwtPushNotificationSender:
        return JwtPushNotificationSender(
            client, store, signer, push_url_validator=validator, backoff_s=backoff_s
        )

    app = build_a2a_app(
        card=card,
        executor=executor,
        push=True,
        push_sender_factory=sender,
        push_url_validator=validator,
        **extra,
    )
    add_jwks_route(app, signer)
    app.state.signer = signer
    return app


def run_main(service: str, framework: Any, build: Any) -> None:
    from a2a_labs_common.a2a.server import run
    from a2a_labs_common.telemetry import init_telemetry

    init_telemetry(service, framework=framework, lab_id="lab08")
    run(build(), service=service)
