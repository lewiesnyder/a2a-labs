"""Extension helpers: declare, activate, require, metadata and the response echo."""

from __future__ import annotations

import httpx
import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import AgentCapabilities, AgentCard, Message
from a2a.utils.errors import ExtensionSupportRequiredError
from a2a_labs_common.a2a.extensions import (
    ExtensionEcho,
    activated,
    declared,
    extension,
    metadata_for,
    require_extensions,
    required_uris,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

OPT, REQ = "https://x.test/ext/opt/v1", "https://x.test/ext/req/v1"


def card() -> AgentCard:
    return AgentCard(
        capabilities=AgentCapabilities(
            extensions=[
                extension(OPT, "optional"),
                extension(REQ, "required", required=True, params={"currency": "USD"}),
            ]
        )
    )


def ctx(*requested: str, metadata: dict | None = None) -> RequestContext:
    msg = Message(message_id="m")
    if metadata:
        from google.protobuf.json_format import ParseDict

        ParseDict(metadata, msg.metadata)
    return _ctx(msg, set(requested))


def _ctx(msg: Message, requested: set[str]) -> RequestContext:
    from a2a.types import SendMessageRequest

    return RequestContext(
        call_context=ServerCallContext(requested_extensions=requested),
        request=SendMessageRequest(message=msg),
    )


def test_declared_required_and_activated() -> None:
    c = card()
    assert set(declared(c)) == {OPT, REQ} and required_uris(c) == {REQ}
    assert declared(c)[REQ].params["currency"] == "USD"
    assert activated(c, {OPT, "https://unknown"}) == {OPT}  # unknown URIs are ignored


def test_a_missing_required_extension_raises_and_a_present_one_does_not() -> None:
    with pytest.raises(ExtensionSupportRequiredError, match="req/v1"):
        require_extensions(card(), ctx(OPT))
    require_extensions(card(), ctx(REQ))


def test_metadata_is_namespaced_by_the_extension_uri() -> None:
    c = ctx(REQ, metadata={REQ: {"max_usd": 0.05}, "other": 1})
    assert metadata_for(c, REQ) == {"max_usd": 0.05}
    assert metadata_for(c, OPT) == {}
    assert metadata_for(ctx(), REQ) == {}


async def test_the_echo_header_lists_only_activated_declared_extensions() -> None:
    async def ok(_: object) -> PlainTextResponse:
        return PlainTextResponse("hi")

    app = Starlette(routes=[Route("/", ok)], middleware=[Middleware(ExtensionEcho, card=card())])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert "a2a-extensions" not in (await c.get("/")).headers
        r = await c.get("/", headers={"A2A-Extensions": f"{REQ}, {OPT}, https://unknown"})
        assert r.headers["a2a-extensions"] == f"{OPT},{REQ}"
