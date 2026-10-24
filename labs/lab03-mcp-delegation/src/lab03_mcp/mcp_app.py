"""Shared plumbing for the two MCP servers in this lab (tools server and bridge).

Implements: spec lab03 §2-3. FastMCP serves MCP over *streamable HTTP*, which is plain HTTP, so
the repo's usual Starlette instrumentation applies and ``traceparent`` propagates through it.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from a2a_labs_common.config import port
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from opentelemetry import trace
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("lab03_mcp")


def new_server(service: str, instructions: str) -> FastMCP:
    """A FastMCP server for ``service`` with a ``/healthz`` route.

    DNS-rebinding protection is on by default and only accepts ``localhost``; inside compose the
    ``Host`` header is the docker service name, so it is turned off here. That is acceptable on
    the private compose network and would NOT be on a public one.
    """
    mcp = FastMCP(
        service,
        instructions=instructions,
        host="0.0.0.0",
        port=port(service),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": service})

    return mcp


def traced(
    tool_name: str,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Wrap an MCP tool handler in an ``mcp.call_tool <tool>`` span (spec 05 §3)."""

    def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _tracer.start_as_current_span(
                f"mcp.call_tool {tool_name}",
                kind=trace.SpanKind.SERVER,
                attributes={"mcp.tool.name": tool_name},
            ):
                return await fn(*args, **kwargs)

        return wrapper

    return deco


def build_asgi(mcp: FastMCP) -> Any:
    from a2a_labs_common.telemetry import instrument_app

    app = mcp.streamable_http_app()
    instrument_app(app)
    return app


def serve(mcp: FastMCP, *, service: str) -> None:
    uvicorn.run(build_asgi(mcp), host="0.0.0.0", port=port(service), log_config=None)
