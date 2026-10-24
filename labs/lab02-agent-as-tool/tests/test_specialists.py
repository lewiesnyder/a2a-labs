"""The three opaque specialists, each built on a different framework (fake models)."""

from __future__ import annotations

from a2a.types import ListTasksRequest, TaskState
from a2a_labs_common.a2a.client import send_text

S = TaskState


async def test_currency_converts_with_the_static_rate_table(specialist_client) -> None:  # type: ignore[no-untyped-def]
    async with specialist_client("lab02-langgraph-currency") as c:
        r = await send_text(c, "Convert 250 GBP to JPY")
        assert r.state == S.TASK_STATE_COMPLETED
        assert "250 GBP = 49,050.63 JPY" in r.text


async def test_currency_asks_for_the_missing_target_then_resumes_the_same_task(  # type: ignore[no-untyped-def]
    specialist_client,
) -> None:
    async with specialist_client("lab02-langgraph-currency") as c:
        first = await send_text(c, "Convert 100 USD")
        assert first.state == S.TASK_STATE_INPUT_REQUIRED
        assert "which currency" in first.task.status.message.parts[0].text.lower()
        second = await send_text(c, "EUR", task_id=first.task_id, context_id=first.context_id)
        assert second.task_id == first.task_id and second.state == S.TASK_STATE_COMPLETED
        assert "100 USD = 92.00 EUR" in second.text
        tasks = (await c.client.list_tasks(ListTasksRequest())).tasks
        assert len(tasks) == 1  # one remote task served both user turns


async def test_weather_calls_its_private_tool(specialist_client) -> None:  # type: ignore[no-untyped-def]
    async with specialist_client("lab02-strands-weather") as c:
        r = await send_text(c, "What is the weather in Tokyo?")
        assert r.state == S.TASK_STATE_COMPLETED and "Tokyo forecast" in r.text


async def test_facts_calls_its_private_tool(specialist_client) -> None:  # type: ignore[no-untyped-def]
    async with specialist_client("lab02-adk-facts") as c:
        r = await send_text(c, "Tell me facts about Lisbon")
        assert r.state == S.TASK_STATE_COMPLETED and "oldest cities" in r.text


async def test_cards_do_not_reveal_the_tools_inside(specialist_client) -> None:  # type: ignore[no-untyped-def]
    """Opacity: skills describe WHAT the agent does, never its tools, prompts or model."""
    secrets = ("get_exchange_rate", "get_forecast", "lookup_fact", "llama", "qwen")
    for svc in ("lab02-langgraph-currency", "lab02-strands-weather"):
        async with specialist_client(svc) as c:
            blob = str(c.card).lower()
            assert not any(s in blob for s in secrets), svc
