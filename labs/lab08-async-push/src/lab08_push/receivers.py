"""Where push notifications land: a client-owned receiver, or the shared compose one.

In production the CLIENT owns the webhook. ``EmbeddedReceiver`` is that shape: the receiver app
from ``services/webhook-receiver`` running inside your own process (a notebook, a test). The
agents must be able to reach it, so ``agent_base`` is the address THEY use (``jupyter`` inside
compose, ``host.docker.internal`` from a host-run notebook). ``SharedReceiver`` is the compose
``webhook-receiver`` service for when you cannot open a port.
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
from typing import Any

import httpx
import uvicorn
from a2a_labs_common import config


class Receiver:
    def __init__(self, base: str, agent_base: str, *, network: str = "host") -> None:
        self.base, self.agent_base, self.network = base.rstrip("/"), agent_base.rstrip("/"), network

    def webhook_url(self, channel: str = "default") -> str:
        return f"{self.agent_base}/webhooks/a2a/{channel}"

    def jwks_url(self, service: str) -> str:
        """The agent's JWKS as THIS receiver must reach it (host or compose network)."""
        key = "docker_url" if self.network == "docker" else "host_url"
        return f"{config._endpoints()[service][key]}/.well-known/jwks.json"

    async def expect(self, token: str, agent_jwks_url: str, task_id: str | None = None) -> None:
        async with httpx.AsyncClient(timeout=10) as http:
            body = {"token": token, "agent_jwks_url": agent_jwks_url, "task_id": task_id}
            (await http.post(f"{self.base}/expectations", json=body)).raise_for_status()

    async def notifications(
        self, since: int = 0, task_id: str | None = None, channel: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"since": since}
        if task_id:
            params["task_id"] = task_id
        if channel:
            params["channel"] = channel
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(f"{self.base}/notifications", params=params)
        rows: list[dict[str, Any]] = resp.json()
        return rows

    async def clear(self) -> None:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.delete(f"{self.base}/notifications")


class SharedReceiver(Receiver):
    def __init__(self) -> None:
        super().__init__(
            str(config._endpoints()["webhook-receiver"]["host_url"]),
            str(config._endpoints()["webhook-receiver"]["docker_url"]),
            network="host",
        )


class EmbeddedReceiver(Receiver):
    """Starts ``create_app(require_auth=True)`` on a local port; ``stop()`` simulates an outage."""

    def __init__(self, port: int | None = None, agent_host: str | None = None) -> None:
        self.port = port or int(os.environ.get("LAB08_RECEIVER_PORT", "0")) or _free_port()
        docker = config.settings().runtime == "docker"
        host = agent_host or ("jupyter" if docker else "host.docker.internal")
        super().__init__(
            f"http://127.0.0.1:{self.port}",
            f"http://{host}:{self.port}",
            network="docker" if docker else "host",
        )
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    async def start(self) -> EmbeddedReceiver:
        from a2a_labs_webhook.app import create_app

        app = create_app(require_auth=True)
        self.app = app
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        while not self._server.started:  # noqa: ASYNC110 - uvicorn exposes no awaitable for this
            await asyncio.sleep(0.02)
        return self

    def stop(self) -> None:
        if self._server is not None and self._thread is not None:
            self._server.should_exit = True  # graceful first: no lifespan traceback
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                self._server.force_exit = True
                self._thread.join(timeout=3)
            self._server = self._thread = None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
