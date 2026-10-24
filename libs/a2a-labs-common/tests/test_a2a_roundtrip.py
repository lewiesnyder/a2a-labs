"""In-process round trip: SDK server (JSON-RPC + REST) <-> a2a_labs_common client helpers."""

from __future__ import annotations

from a2a.types import TaskState
from a2a_labs_common.a2a import cards
from a2a_labs_common.a2a.client import bridge_card_urls, connect, send_text, stream_text
from a2a_labs_common.a2a.server import build_a2a_app
from a2a_labs_common.a2a.strands import StrandsA2AExecutor
from a2a_labs_common.testing import asgi_client


class Agent:
    async def stream_async(self, prompt: str, **_: object):
        yield {"data": "echo: "}
        yield {"data": prompt}


def _app(streaming: bool = True):  # type: ignore[no-untyped-def]
    card = cards.build_card(
        service="lab00-protocol-agent",
        name="Echo",
        description="Echoes user text back",
        skills=[cards.skill("echo", "Echo", "Echo text", ["demo"], ["hi"])],
        streaming=streaming,
        bindings=("JSONRPC", "HTTP+JSON"),
    )
    for i in card.supported_interfaces:  # point the card at the in-process server
        i.url = "http://testserver" + ("/rest" if i.protocol_binding == "HTTP+JSON" else "")
    return build_a2a_app(card=card, executor=StrandsA2AExecutor(lambda _c: Agent()), rest=True)


async def test_jsonrpc_blocking_send() -> None:
    async with connect_to(_app(), streaming=False) as c:
        r = await send_text(c, "hello")
        assert r.kind == "task" and r.state == TaskState.TASK_STATE_COMPLETED
        assert r.text == "echo: hello"


async def test_jsonrpc_streaming_event_order() -> None:
    async with connect_to(_app()) as c:
        evs = [ev async for ev in stream_text(c, "x")]
        kinds = [e.WhichOneof("payload") for e in evs]
        assert kinds[0] == "task"  # A2A: Task first
        assert kinds[-1] == "status_update" and "artifact_update" in kinds


async def test_rest_binding() -> None:
    async with connect_to(_app(), transport="HTTP+JSON", streaming=False) as c:
        r = await send_text(c, "over rest")
        assert r.text == "echo: over rest"


async def test_card_discovery_and_bridging() -> None:
    async with connect_to(_app()) as c:
        assert c.card.name == "Echo" and c.card.capabilities.streaming
    card = cards.build_card(
        service="lab00-protocol-agent",
        name="n",
        description="d" * 25,
        skills=[cards.skill("a", "A", "d", ["t"], [])],
    )
    bridge_card_urls(card)
    assert card.supported_interfaces[0].url == "http://localhost:10001"


def connect_to(app, **kw):  # type: ignore[no-untyped-def]
    class _Ctx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            self.c = await connect("http://testserver", httpx_client=asgi_client(app), **kw)
            return self.c

        async def __aexit__(self, *a: object) -> None:
            await self.c.aclose()

    return _Ctx()


async def test_v03_wire_names_served_via_compat() -> None:
    """A real 0.3 client speaks `message/send` with no A2A-Version header (treated as 0.3)."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "messageId": "m1",
                "role": "user",
                "parts": [{"kind": "text", "text": "hi"}],
            }
        },
    }
    async with asgi_client(_app()) as http:
        r = (await http.post("/", json=body)).json()
        legacy = (await http.post("/", json={**body, "method": "tasks/send"})).json()
    assert r["result"]["status"]["state"] == "completed"  # v0.3 lowercase enum
    assert legacy["error"]["code"] == -32601  # D2: legacy v0.1 names are rejected
