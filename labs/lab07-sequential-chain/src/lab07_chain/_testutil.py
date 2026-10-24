"""Test helpers shared by the lab's test modules (in the package: no tests/ package needed)."""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any

import uvicorn


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Live:
    def __init__(self, app: Any, port: int | None = None) -> None:
        self.port = port or free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    async def start(self) -> Live:
        self.thread.start()
        while not self.server.started:  # noqa: ASYNC110 - uvicorn exposes no awaitable for this
            await asyncio.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.force_exit = True
        self.server.should_exit = True
        self.thread.join(timeout=5)
