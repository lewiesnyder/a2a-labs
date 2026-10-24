"""LangGraphA2AExecutor: real LangGraph graphs (no LLM) behind the real SDK server, in-process."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import pytest
from a2a.types import CancelTaskRequest, TaskState
from a2a_labs_common.a2a import cards
from a2a_labs_common.a2a.client import connect, send_text, stream_text
from a2a_labs_common.a2a.langgraph import LangGraphA2AExecutor, message_text
from a2a_labs_common.a2a.server import build_a2a_app
from a2a_labs_common.testing import asgi_client
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

S = TaskState


class State(TypedDict):
    messages: Annotated[list[Any], add_messages]


def _client(graph: Any, *, streaming: bool = False, **kw: Any):  # type: ignore[no-untyped-def]
    card = cards.build_card(
        service="lab01-langgraph-agent",
        name="LG",
        description="LangGraph test agent",
        skills=[cards.skill("s", "S", "d", ["t"], [])],
    )
    for i in card.supported_interfaces:
        i.url = "http://testserver"
    app = build_a2a_app(card=card, executor=LangGraphA2AExecutor(graph, agent_name="lg", **kw))
    return connect("http://testserver", httpx_client=asgi_client(app), streaming=streaming)


def echo_graph() -> Any:
    def node(state: State) -> dict[str, Any]:
        return {"messages": [AIMessage(f"echo: {message_text(state['messages'][-1])}")]}

    g = StateGraph(State)
    g.add_node("echo", node)
    g.add_edge(START, "echo")
    g.add_edge("echo", END)
    return g.compile(checkpointer=InMemorySaver())


async def test_final_answer_becomes_artifact_and_completes() -> None:
    c = await _client(echo_graph())
    r = await send_text(c, "hello")
    assert r.state == S.TASK_STATE_COMPLETED and r.text == "echo: hello"
    assert r.task.artifacts[0].name == "answer"
    await c.aclose()


async def test_uses_astream_never_sync_stream() -> None:
    graph = echo_graph()
    calls = {"astream": 0}
    real = graph.astream

    def counting(*a: Any, **k: Any) -> Any:
        calls["astream"] += 1
        return real(*a, **k)

    graph.astream = counting
    graph.stream = lambda *a, **k: pytest.fail("sync graph.stream() blocks the event loop")
    c = await _client(graph)
    await send_text(c, "x")
    assert calls["astream"] == 1
    await c.aclose()


def interrupt_graph() -> Any:
    def ask(state: State) -> dict[str, Any]:
        colour = interrupt("Which colour?")
        return {"messages": [AIMessage(f"you chose {colour}")]}

    g = StateGraph(State)
    g.add_node("ask", ask)
    g.add_edge(START, "ask")
    g.add_edge("ask", END)
    return g.compile(checkpointer=InMemorySaver())


async def test_interrupt_is_input_required_and_resume_completes_same_task() -> None:
    c = await _client(interrupt_graph())
    first = await send_text(c, "pick one")
    assert first.state == S.TASK_STATE_INPUT_REQUIRED
    assert first.task.status.message.parts[0].text == "Which colour?"
    second = await send_text(c, "green", task_id=first.task_id, context_id=first.context_id)
    assert second.task_id == first.task_id
    assert second.state == S.TASK_STATE_COMPLETED and second.text == "you chose green"
    await c.aclose()


async def test_interrupt_ignored_when_disabled_does_not_pause() -> None:
    c = await _client(interrupt_graph(), interrupt_to_input_required=False)
    r = await send_text(c, "pick one")
    assert r.state == S.TASK_STATE_COMPLETED  # nothing emitted INPUT_REQUIRED
    await c.aclose()


async def test_tool_calls_become_working_status_messages() -> None:
    def tool_node(state: State) -> dict[str, Any]:
        return {
            "messages": [AIMessage("", tool_calls=[{"name": "local_time", "args": {}, "id": "1"}])]
        }

    def answer(state: State) -> dict[str, Any]:
        return {"messages": [AIMessage("done")]}

    g = StateGraph(State)
    g.add_node("t", tool_node)
    g.add_node("a", answer)
    g.add_edge(START, "t")
    g.add_edge("t", "a")
    g.add_edge("a", END)
    c = await _client(g.compile(checkpointer=InMemorySaver()), streaming=True)
    r = await send_text(c, "go")
    texts = [
        e.status_update.status.message.parts[0].text
        for e in r.events
        if e.WhichOneof("payload") == "status_update" and e.status_update.status.HasField("message")
    ]
    assert "Calling tool local_time" in texts and r.text == "done"
    await c.aclose()


async def test_failure_marks_task_failed() -> None:
    def boom(state: State) -> dict[str, Any]:
        raise RuntimeError("kaput")

    g = StateGraph(State)
    g.add_node("b", boom)
    g.add_edge(START, "b")
    g.add_edge("b", END)
    c = await _client(g.compile(checkpointer=InMemorySaver()))
    r = await send_text(c, "x")
    assert r.state == S.TASK_STATE_FAILED and "kaput" in r.task.status.message.parts[0].text
    await c.aclose()


async def test_multi_turn_memory_uses_context_id_as_thread_id() -> None:
    seen: list[str] = []

    def node(state: State) -> dict[str, Any]:
        seen.append(" | ".join(message_text(m) for m in state["messages"]))
        return {"messages": [AIMessage("ok")]}

    g = StateGraph(State)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    c = await _client(g.compile(checkpointer=InMemorySaver()))
    t1 = await send_text(c, "My name is Ada")
    await send_text(c, "What is my name?", context_id=t1.context_id)  # same context
    assert "My name is Ada" in seen[1]  # turn 2 saw turn 1
    await send_text(c, "What is my name?")  # new context
    assert "Ada" not in seen[2]
    await c.aclose()


async def test_token_streaming_as_appended_artifact_chunks() -> None:
    from langchain.agents import create_agent

    model = GenericFakeChatModel(messages=iter([AIMessage("alpha beta gamma")]))
    graph = create_agent(model, tools=[], checkpointer=InMemorySaver())
    card = cards.build_card(
        service="lab01-langgraph-agent",
        name="LG",
        description="LangGraph test agent",
        skills=[cards.skill("s", "S", "d", ["t"], [])],
    )
    for i in card.supported_interfaces:
        i.url = "http://testserver"
    app = build_a2a_app(
        card=card, executor=LangGraphA2AExecutor(graph, agent_name="lg", stream_tokens=True)
    )
    c = await connect("http://testserver", httpx_client=asgi_client(app), streaming=True)
    evs = [e async for e in stream_text(c, "hi")]
    chunks = [e.artifact_update for e in evs if e.WhichOneof("payload") == "artifact_update"]
    assert len(chunks) >= 3 and chunks[-1].last_chunk and not chunks[0].append
    assert all(x.append for x in chunks[1:])
    await c.aclose()


async def test_cancel_stops_the_run_and_marks_canceled() -> None:
    started = asyncio.Event()

    async def slow(state: State) -> dict[str, Any]:
        started.set()
        await asyncio.sleep(30)
        return {"messages": [AIMessage("late")]}

    g = StateGraph(State)
    g.add_node("s", slow)
    g.add_edge(START, "s")
    g.add_edge("s", END)
    c = await _client(g.compile(checkpointer=InMemorySaver()))
    r = await send_text(c, "go", return_immediately=True)
    await asyncio.wait_for(started.wait(), 5)
    canceled = await c.client.cancel_task(CancelTaskRequest(id=r.task_id))
    assert canceled.status.state == S.TASK_STATE_CANCELED
    await c.aclose()
