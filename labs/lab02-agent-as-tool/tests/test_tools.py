"""The tool layer: card-derived descriptions, never raising into the LLM loop, the allowlist."""

from __future__ import annotations

from a2a_labs_common.a2a.langchain_tool import a2a_agent_as_langchain_tool
from a2a_labs_common.a2a.remote import RemoteAgent, RemoteReply, card_description
from a2a_labs_common.a2a.strands_tools import make_a2a_client_tool, make_stateful_a2a_tool

CURRENCY = "lab02-langgraph-currency"


async def test_langchain_tool_description_is_generated_from_the_card(  # type: ignore[no-untyped-def]
    specialists, specialist_client
) -> None:
    tool = a2a_agent_as_langchain_tool(CURRENCY, name="ask_currency")
    async with specialist_client(CURRENCY) as c:
        assert tool.description == card_description(c.card)
    assert tool.name == "ask_currency"
    assert "Currency Converter" in tool.description and "Convert 100 USD to EUR" in tool.description
    # the card, not the agent's internals, is the schema source
    assert "get_exchange_rate" not in tool.description
    assert set(tool.args) == {"request", "task_id"}


async def test_langchain_tool_uses_a_stable_remote_context_per_thread(specialists) -> None:  # type: ignore[no-untyped-def]
    tool = a2a_agent_as_langchain_tool(CURRENCY, name="ask_currency")
    cfg = {"configurable": {"thread_id": "thread-A"}}
    first = await tool.ainvoke({"request": "Convert 100 USD"}, config=cfg)
    assert first.startswith("NEEDS_INPUT: Convert to which currency? [task_id=")
    # no task_id passed: the tool remembers the paused remote task for this thread
    done = await tool.ainvoke({"request": "EUR"}, config=cfg)
    assert "100 USD = 92.00 EUR" in done
    other = await tool.ainvoke(
        {"request": "Convert 10 USD to EUR"}, config={"configurable": {"thread_id": "B"}}
    )
    assert "10 USD = 9.20 EUR" in other


async def test_an_unreachable_agent_becomes_an_error_string_not_an_exception() -> None:
    remote = RemoteAgent("http://127.0.0.1:9")  # nothing listens here
    reply = await remote.ask("hello", scope="s")
    assert reply.status == "error" and reply.for_llm().startswith("ERROR: ")


async def test_a_stale_task_id_does_not_wedge_the_tool(specialists) -> None:  # type: ignore[no-untyped-def]
    remote = RemoteAgent(CURRENCY)
    reply = await remote.ask("Convert 1 USD to EUR", scope="s", task_id="no-such-task")
    assert reply.status == "error"
    again = await remote.ask("Convert 1 USD to EUR", scope="s")  # next call starts fresh
    assert again.status == "done" and "1 USD = 0.92 EUR" in again.text
    await remote.aclose()


def test_reply_formatting_for_the_llm() -> None:
    assert RemoteReply("done", "42").for_llm() == "42"
    assert (
        RemoteReply("needs_input", "which?", "t1").for_llm() == "NEEDS_INPUT: which? [task_id=t1]"
    )
    assert RemoteReply("needs_auth", "token?", "t2").for_llm() == "NEEDS_AUTH: token? [task_id=t2]"
    assert RemoteReply("error", "boom").for_llm() == "ERROR: boom"


async def test_strands_allowlist_tool_rejects_other_urls_and_accepts_listed_ones(
    specialists,
) -> None:  # type: ignore[no-untyped-def]
    from a2a_labs_common.config import endpoint

    allowed = endpoint(CURRENCY)
    tool = make_a2a_client_tool(name="call", description="d", allowed_endpoints={allowed: None})
    fn = tool._tool_func
    assert (await fn(url="http://evil.example", message="x")).startswith(
        "ERROR: endpoint not allowed"
    )
    ok = await fn(url=allowed + "/", message="Convert 2 USD to EUR")  # trailing slash is normalised
    assert "2 USD = 1.84 EUR" in ok


async def test_strands_stateful_tool_keeps_one_remote_conversation(specialists) -> None:  # type: ignore[no-untyped-def]
    tool = make_stateful_a2a_tool(name="ask", description="d", service=CURRENCY)
    fn = tool._tool_func
    assert "NEEDS_INPUT" in await fn(question="Convert 100 USD")
    assert "100 USD = 92.00 EUR" in await fn(question="EUR")


async def test_conversations_do_not_share_remote_state_through_the_real_agent_graph(  # type: ignore[no-untyped-def]
    specialists,
) -> None:
    """Regression: the tool must see the run config's thread_id (see langchain_tool.py)."""
    from lab02_tool.agents.controllers import build_langchain_controller

    graph = build_langchain_controller()

    async def ask(thread: str, text: str) -> str:
        out = await graph.ainvoke(
            {"messages": [("user", text)]}, config={"configurable": {"thread_id": thread}}
        )
        return str(out["messages"][-1].content)

    assert "NEEDS_INPUT" in await ask("thread-1", "Convert 100 USD")
    # Another conversation answers "EUR": it must NOT complete thread-1's pending conversion.
    other = await ask("thread-2", "EUR")
    assert "100 USD" not in other and "NEEDS_INPUT: How much" in other, other
    assert "100 USD = 92.00 EUR" in await ask("thread-1", "EUR")  # thread-1 still resumes
