"""A2A extensions: declare, activate, echo and enforce (spec lab10 §2, §6.9-6.11).

What the SDK gives us: ``AgentExtension`` on the card, ``A2A-Extensions`` parsing into
``ServerCallContext.requested_extensions`` (so ``RequestContext.requested_extensions``), the
client's ``with_a2a_extensions`` service parameters and the ``ExtensionSupportRequiredError``
type. What it does not: any server code that RAISES that error or echoes the activated set. So:

* ``required_extension_missing``: the check an executor (or a request handler wrapper) runs;
* ``ExtensionEcho``: pure-ASGI middleware that sets ``A2A-Extensions`` on the response.

# CUSTOM: enforcement and echo (DEV-58). The convention for params and metadata is ours:
# an extension's metadata lives under a key equal to its URI.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from a2a.server.agent_execution import RequestContext
from a2a.types import AgentCard, AgentExtension
from a2a.utils.errors import ExtensionSupportRequiredError
from google.protobuf.json_format import MessageToDict, ParseDict

HEADER = "A2A-Extensions"


def extension(
    uri: str,
    description: str,
    *,
    required: bool = False,
    params: Mapping[str, Any] | None = None,
) -> AgentExtension:
    ext = AgentExtension(uri=uri, description=description, required=required)
    if params:
        ParseDict(dict(params), ext.params)
    return ext


def declared(card: AgentCard) -> dict[str, AgentExtension]:
    return {e.uri: e for e in card.capabilities.extensions}


def required_uris(card: AgentCard) -> set[str]:
    return {uri for uri, e in declared(card).items() if e.required}


def activated(card: AgentCard, requested: Collection[str]) -> set[str]:
    """What the server activates: requested AND declared (unknown URIs are ignored, per spec)."""
    return set(requested) & set(declared(card))


def require_extensions(card: AgentCard, context: RequestContext) -> None:
    """Raise ``ExtensionSupportRequiredError`` unless every ``required`` extension is requested."""
    missing = sorted(required_uris(card) - set(context.requested_extensions))
    if missing:
        raise ExtensionSupportRequiredError(
            message=f"this agent requires the extension(s): {', '.join(missing)}"
        )


def metadata_for(context: RequestContext, uri: str) -> dict[str, Any]:
    """The request metadata an extension owns: ``message.metadata[<extension uri>]``."""
    if context.message is None:
        return {}
    value = MessageToDict(context.message.metadata).get(uri, {})
    return value if isinstance(value, dict) else {}


class ExtensionEcho:
    """Adds ``A2A-Extensions: <activated uris>`` to every HTTP response (including SSE).

    The spec asks a server to tell the client which extensions it ACTIVATED, so the client can
    tell "honoured" from "ignored". The SDK parses the request header but does not echo.
    """

    def __init__(self, app: Any, card: AgentCard) -> None:
        self.app, self.card = app, card

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from a2a.extensions.common import get_requested_extensions

        raw = [v.decode() for k, v in scope["headers"] if k.decode().lower() == HEADER.lower()]
        active = activated(self.card, get_requested_extensions(raw))

        async def send_with_echo(message: Any) -> None:
            if message["type"] == "http.response.start" and active:
                headers = list(message.get("headers", []))
                headers.append((HEADER.lower().encode(), ",".join(sorted(active)).encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_echo)
