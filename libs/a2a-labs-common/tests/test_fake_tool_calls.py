"""The fake models can script tool calls, in all three frameworks (closes DEV-2)."""

from __future__ import annotations

from typing import Any

import pytest
from a2a_labs_common import testing
from a2a_labs_common.models import settings as ms

SCRIPT = {
    "quick": [
        {
            "match": "add 2 and 3",
            "tool_calls": [{"name": "add", "args": {"a": 2, "b": 3}}],
            "reply": "The sum is {results}.",
        },
        {"match": "echo this", "reply": "you said: {after:echo this }"},
    ]
}


@pytest.fixture(autouse=True)
def _script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    ms.reset_cache()
    monkeypatch.setattr(testing, "_script", lambda: SCRIPT)
    testing.clear_fake_calls()


def test_plan_templates_and_defaults() -> None:
    p = testing.plan_reply("quick", "any", "please add 2 and 3")
    assert p.tool_calls == [("add", {"a": 2, "b": 3})] and p.text is None
    done = testing.plan_reply("quick", "any", "please add 2 and 3", ["5"])
    assert done.text == "The sum is 5."
    assert testing.plan_reply("quick", "any", "echo this hello").text == "you said: hello"
    assert "[fake quick turn=1]" in (testing.plan_reply("quick", "any", "zzz").text or "")
    assert "results: x" in (testing.plan_reply("quick", "any", "zzz", ["x"]).text or "")


def test_framework_filter_skips_other_frameworks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        testing, "_script", lambda: {"quick": [{"match": "q", "framework": "adk", "reply": "adk!"}]}
    )
    assert testing.plan_reply("quick", "adk", "q").text == "adk!"
    assert "adk!" not in (testing.plan_reply("quick", "strands", "q").text or "")


async def test_langchain_fake_calls_the_tool_then_answers() -> None:
    from a2a_labs_common.models import get_langchain_model
    from langchain.agents import create_agent
    from langchain_core.tools import tool

    calls: list[tuple[int, int]] = []

    @tool
    def add(a: int, b: int) -> str:
        """Add two numbers."""
        calls.append((a, b))
        return str(a + b)

    agent = create_agent(get_langchain_model("quick"), tools=[add])
    out = await agent.ainvoke({"messages": [("user", "please add 2 and 3")]})
    assert calls == [(2, 3)] and out["messages"][-1].content == "The sum is 5."


async def test_strands_fake_calls_the_tool_then_answers() -> None:
    from a2a_labs_common.models import get_strands_model
    from strands import Agent, tool

    calls: list[tuple[int, int]] = []

    @tool
    def add(a: int, b: int) -> str:
        """Add two numbers.

        Args:
            a: first
            b: second
        """
        calls.append((a, b))
        return str(a + b)

    agent = Agent(model=get_strands_model("quick"), tools=[add], callback_handler=None)
    result = await agent.invoke_async("please add 2 and 3")
    assert calls == [(2, 3)] and "The sum is 5." in str(result)


async def test_adk_fake_calls_the_tool_then_answers() -> None:
    from a2a_labs_common.models import get_adk_model
    from google.adk.agents import Agent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    calls: list[tuple[int, int]] = []

    def add(a: int, b: int) -> str:
        """Add two numbers.

        Args:
            a: first
            b: second
        """
        calls.append((a, b))
        return str(a + b)

    agent = Agent(name="adder", model=get_adk_model("quick"), tools=[add])
    runner = InMemoryRunner(agent=agent, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u")
    final: Any = None
    async for ev in runner.run_async(
        user_id="u",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="please add 2 and 3")]),
    ):
        if ev.is_final_response():
            final = ev
    assert calls == [(2, 3)]
    assert final.content.parts[0].text == "The sum is 5."
