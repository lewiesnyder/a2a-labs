"""Notebook helpers: show what each controller did, in one line per step.

Implements: spec lab02 §4 steps 5-7. Kept out of the notebook so cells stay short.
"""

from __future__ import annotations

from typing import Any


async def adk_events(agent: Any, text: str) -> list[dict[str, str]]:
    """Run an ADK agent once and summarise its events (author, kind, detail)."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent=agent, app_name="lab02-demo")
    session = await runner.session_service.create_session(app_name="lab02-demo", user_id="nb")
    rows: list[dict[str, str]] = []
    async for ev in runner.run_async(
        user_id="nb",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=text)]),
    ):
        for part in (ev.content.parts if ev.content else []) or []:
            if part.function_call:
                call = f"{part.function_call.name}({dict(part.function_call.args or {})})"
                rows.append({"author": ev.author, "kind": "call", "detail": call[:90]})
            elif part.function_response:
                resp = str(part.function_response.response)[:70]
                detail = f"{part.function_response.name} -> {resp}"
                rows.append({"author": ev.author, "kind": "result", "detail": detail})
            elif part.text and not part.thought:
                rows.append({"author": ev.author, "kind": "text", "detail": part.text.strip()[:90]})
    return rows


def langchain_tool_calls(messages: list[Any]) -> list[str]:
    """Names of the tools a LangChain agent called, from its message history."""
    return [f"{m.name}" for m in messages if getattr(m, "type", "") == "tool"]


def strands_tool_calls(agent: Any) -> list[str]:
    """Names of the tools a Strands agent called, from its message history."""
    out = []
    for m in agent.messages:
        for block in m.get("content", []):
            if isinstance(block, dict) and "toolUse" in block:
                out.append(block["toolUse"]["name"])
    return out
