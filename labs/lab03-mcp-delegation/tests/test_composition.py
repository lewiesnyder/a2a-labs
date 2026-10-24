"""MCP inside / A2A outside, and A2A behind MCP, end to end with fake models (live servers)."""

from __future__ import annotations

from a2a.types import TaskState
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.testing import asgi_client
from lab03_mcp.agents.consumers import build_strands_consumer

QUESTION = "How many feet in 3.2 km, and what time is it in Tokyo when it's 9am in Chicago?"


async def test_strands_specialist_uses_mcp_tools_inside_and_a2a_outside(stack) -> None:  # type: ignore[no-untyped-def]
    async with await connect(stack["lab03-strands-specialist"], streaming=False) as c:
        r = await send_text(c, "How many feet in 3.2 km, and what is 12% of that?")
        assert r.state == TaskState.TASK_STATE_COMPLETED
        assert "10498.6877" in r.text and "1259.84" in r.text, r.text  # two MCP tool results
        # opacity: nothing in the card reveals that MCP (or any tool) is involved
        assert "mcp" not in str(c.card).lower() and "convert_units" not in str(c.card)


async def test_adk_specialist_uses_mcp_tools_inside_and_a2a_outside(stack) -> None:  # type: ignore[no-untyped-def]
    async with await connect(stack["lab03-adk-specialist"], streaming=False) as c:
        r = await send_text(c, "What time is it in Tokyo when it is 9am in Chicago?")
        assert r.state == TaskState.TASK_STATE_COMPLETED
        assert "Asia/Tokyo" in r.text and "America/Chicago" in r.text, r.text
        assert "mcp" not in str(c.card).lower() and "lookup_timezone" not in str(c.card)


def assert_composite_answer(text: str) -> None:
    # feet from the Strands specialist; timezones from the ADK specialist: two A2A hops behind MCP
    assert "10498.6877" in text, text
    assert "Asia/Tokyo" in text and "America/Chicago" in text, text


async def test_langgraph_consumer_answers_through_mcp_tools(stack) -> None:  # type: ignore[no-untyped-def]
    from lab03_mcp.servers import langgraph_mcp_consumer

    app = langgraph_mcp_consumer.build_app()
    async with app.router.lifespan_context(app):
        c = await connect("http://testserver", httpx_client=asgi_client(app), streaming=False)
        r = await send_text(c, QUESTION)
        assert r.state == TaskState.TASK_STATE_COMPLETED
        assert_composite_answer(r.text)
        await c.aclose()


async def test_adk_consumer_answers_through_mcp_tools(stack) -> None:  # type: ignore[no-untyped-def]
    from lab03_mcp.servers import adk_mcp_consumer

    app = adk_mcp_consumer.build_app()
    async with app.router.lifespan_context(app):
        c = await connect("http://testserver", httpx_client=asgi_client(app), streaming=False)
        r = await send_text(c, QUESTION)
        assert r.state == TaskState.TASK_STATE_COMPLETED
        assert_composite_answer(r.text)
        await c.aclose()


async def test_strands_consumer_in_process_answers_through_mcp_tools(stack) -> None:  # type: ignore[no-untyped-def]
    agent, _client = build_strands_consumer()
    result = await agent.invoke_async(QUESTION)
    assert_composite_answer(str(result))


async def test_consumers_only_see_mcp_tools_never_a2a(stack, session) -> None:  # type: ignore[no-untyped-def]
    """The consumer's tool list is plain MCP: ask_* tools with JSON schemas, no A2A concepts."""
    async with session(stack["lab03-mcp-a2a-bridge"]) as s:
        names = {t.name for t in (await s.list_tools()).tools}
    assert {"ask_lab03_strands_specialist", "ask_lab03_adk_specialist"} <= names
