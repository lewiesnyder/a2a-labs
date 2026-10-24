"""Deterministic fakes for unit and fake-mode integration tests (spec 03 §7, 08 §4).

Scripts live in ``config/fake_responses.yaml``::

    <role>:
      - match: "text in the last user message"
        framework: strands            # optional: langchain | strands | adk
        tool_calls:                   # optional: emit these tool calls first
          - {name: ask_currency, args: {request: "{last}"}}
        reply: "Final text: {results}"

First entry whose ``match`` is in the last user message (and whose ``framework`` fits) wins. If the
model is shown tool results it answers with ``reply``; otherwise it makes the scripted
``tool_calls`` (if any) or answers with ``reply`` directly. Templates: ``{last}`` (last user text),
``{results}`` (tool outputs joined with "; "), ``{after:MARKER}`` (text after MARKER in the last
user text), ``{endpoint:SERVICE}`` (that service's URL
from config/endpoints.yaml). With no matching entry, a generic echo reply is returned.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import time
from collections.abc import AsyncGenerator, AsyncIterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml

from a2a_labs_common.config import find_config_dir
from a2a_labs_common.models.decision import FakeDecisionModel

__all__ = [
    "FAKE_CALLS",
    "FakeDecisionModel",
    "FakePlan",
    "asgi_client",
    "clear_fake_calls",
    "fetch_trace",
    "plan_reply",
    "scripted_reply",
    "span_index",
]

# Every prompt a fake chat model received, whole conversation included, so tests can assert
# that turn 2 saw turn 1 (multi-turn memory) without a real LLM.
FAKE_CALLS: list[dict[str, str]] = []
_ids = itertools.count(1)


def clear_fake_calls() -> None:
    FAKE_CALLS.clear()


@dataclass
class FakePlan:
    text: str | None = None
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


def _script() -> dict[str, list[dict[str, Any]]]:
    path = find_config_dir() / "fake_responses.yaml"
    return (yaml.safe_load(path.read_text()) if path.is_file() else {}) or {}


def _render(template: Any, last: str, results: list[str]) -> Any:
    if isinstance(template, dict):
        return {k: _render(v, last, results) for k, v in template.items()}
    if not isinstance(template, str):
        return template

    def after(m: re.Match[str]) -> str:
        marker = m.group(1)
        return last.split(marker, 1)[1] if marker in last else ""

    from a2a_labs_common.config import endpoint

    out = re.sub(r"\{after:([^}]*)\}", after, template)
    out = re.sub(r"\{endpoint:([^}]*)\}", lambda m: endpoint(m.group(1)), out)
    return out.replace("{last}", last).replace("{results}", "; ".join(results))


def plan_reply(
    role: str,
    framework: str,
    last_human: str,
    results: Sequence[str] = (),
    turns: int = 1,
) -> FakePlan:
    """Decide what the fake model does next: call scripted tools, or answer in text."""
    for entry in _script().get(role, []):
        if entry["match"] not in last_human or entry.get("framework", framework) != framework:
            continue
        if results:  # tool results are in: answer
            reply = entry.get("reply", "results: {results}")
            return FakePlan(text=_render(reply, last_human, list(results)))
        if entry.get("tool_calls"):
            calls = [
                (c["name"], _render(c.get("args", {}), last_human, [])) for c in entry["tool_calls"]
            ]
            return FakePlan(tool_calls=calls)
        return FakePlan(text=_render(entry.get("reply", ""), last_human, []))
    if results:
        return FakePlan(text=f"[fake {role} turn={turns}] results: {'; '.join(results)}")
    # `turn=N` lets a test prove, over the wire, that the model saw N user turns (memory).
    return FakePlan(text=f"[fake {role} turn={turns}] {last_human[:80]}")


def scripted_reply(role: str, prompt: str, turns: int = 1) -> str:
    """Text-only convenience (framework-neutral, no tool calls)."""
    plan = plan_reply(role, "any", prompt, (), turns)
    return plan.text or ""


def asgi_client(app: Any, base_url: str = "http://testserver") -> httpx.AsyncClient:
    """An httpx client wired straight into an ASGI app: no ports, no network."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


# ── LangChain ───────────────────────────────────────────────────────────────────────────────
def langchain_fake(role: str) -> Any:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ScriptedChat(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "a2a-labs-fake"

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: Sequence[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            humans = [m for m in messages if getattr(m, "type", "") == "human"]
            last_human = str(humans[-1].content) if humans else ""
            # Tool results that arrived after the last user message.
            tail = messages[messages.index(humans[-1]) + 1 :] if humans else []
            results = [str(m.content) for m in tail if isinstance(m, ToolMessage)]
            FAKE_CALLS.append(
                {"role": role, "conversation": " | ".join(str(m.content) for m in messages)}
            )
            plan = plan_reply(role, "langchain", last_human, results, len(humans))
            if plan.tool_calls:
                msg = AIMessage(
                    content="",
                    tool_calls=[
                        {"name": n, "args": a, "id": f"fake-{next(_ids)}"}
                        for n, a in plan.tool_calls
                    ],
                )
            else:
                msg = AIMessage(content=plan.text or "")
            return ChatResult(generations=[ChatGeneration(message=msg)])

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self  # tool calls come from the script, not from the schemas

    return ScriptedChat()


# ── Strands ─────────────────────────────────────────────────────────────────────────────────
def strands_fake(role: str) -> Any:
    from strands.models import Model

    def block_texts(msg: Any) -> list[str]:
        return [b["text"] for b in msg["content"] if isinstance(b, dict) and "text" in b]

    def tool_result_texts(msg: Any) -> list[str]:
        out = []
        for b in msg["content"]:
            if isinstance(b, dict) and "toolResult" in b:
                for c in b["toolResult"]["content"]:
                    out.append(str(c.get("text", c.get("json", ""))))
        return out

    class ScriptedModel(Model):
        def update_config(self, **model_config: Any) -> None:
            return None

        def get_config(self) -> Any:
            return {"model_id": "fake"}

        async def stream(
            self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
        ) -> AsyncIterable[Any]:
            users = [m for m in messages if m["role"] == "user" and block_texts(m)]
            last_human = " ".join(block_texts(users[-1])) if users else ""
            tail = messages[messages.index(users[-1]) + 1 :] if users else []
            results = [t for m in tail if m["role"] == "user" for t in tool_result_texts(m)]
            # A tool-result message is also a "user" message in Strands; the real human turn
            # count is the messages that contain text.
            FAKE_CALLS.append(
                {
                    "role": role,
                    "conversation": " | ".join(t for m in messages for t in block_texts(m)),
                }
            )
            plan = plan_reply(role, "strands", last_human, results, len(users))
            yield {"messageStart": {"role": "assistant"}}
            if plan.tool_calls:
                for name, args in plan.tool_calls:
                    yield {
                        "contentBlockStart": {
                            "start": {"toolUse": {"toolUseId": f"fake-{next(_ids)}", "name": name}}
                        }
                    }
                    yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
                    yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "tool_use"}}
                return
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": plan.text or ""}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}

        async def structured_output(
            self, output_model: Any, prompt: Any, system_prompt: Any = None, **kwargs: Any
        ) -> AsyncGenerator[Any, None]:
            raise NotImplementedError("structured output is not scripted")
            yield {}  # pragma: no cover

    return ScriptedModel()


# ── ADK ─────────────────────────────────────────────────────────────────────────────────────
def adk_fake(role: str) -> Any:
    from google.adk.models import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    def text_of(content: Any) -> str:
        return " ".join(p.text or "" for p in (content.parts or []) if p.text)

    def response_texts(content: Any) -> list[str]:
        return [
            json.dumps(p.function_response.response, default=str)
            if not isinstance(p.function_response.response.get("result"), str)
            else p.function_response.response["result"]
            for p in (content.parts or [])
            if p.function_response
        ]

    class ScriptedLlm(BaseLlm):
        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            contents = llm_request.contents or []
            users = [c for c in contents if c.role == "user" and text_of(c)]
            last_human = text_of(users[-1]) if users else ""
            tail = contents[contents.index(users[-1]) + 1 :] if users else []
            results = [t for c in tail for t in response_texts(c)]
            FAKE_CALLS.append(
                {
                    "role": role,
                    "conversation": " | ".join(text_of(c) for c in contents),
                }
            )
            plan = plan_reply(role, "adk", last_human, results, len(users))
            if plan.tool_calls:
                parts = [
                    types.Part(function_call=types.FunctionCall(name=n, args=a))
                    for n, a in plan.tool_calls
                ]
                yield LlmResponse(content=types.Content(role="model", parts=parts))
                return
            reply = types.Content(role="model", parts=[types.Part(text=plan.text or "")])
            yield LlmResponse(content=reply)

    return ScriptedLlm(model=f"fake-{role}")


# ── Tempo (e2e trace tests) ─────────────────────────────────────────────────────────────────
TEMPO = os.environ.get("TEMPO_URL", "http://localhost:3200")


def span_index(trace_json: dict[str, Any]) -> list[tuple[str, str]]:
    """(service.name, span name) for every span in a Tempo trace."""
    out = []
    for batch in trace_json.get("batches", []):
        attrs = {a["key"]: a["value"].get("stringValue") for a in batch["resource"]["attributes"]}
        for scope in batch.get("scopeSpans", []):
            out += [(attrs.get("service.name", ""), s["name"]) for s in scope["spans"]]
    return out


async def fetch_trace(
    trace_id: str,
    want: set[str],
    timeout: float = 40,  # noqa: ASYNC109 - plain deadline, not a cancel scope
) -> list[tuple[str, str]]:
    deadline = time.monotonic() + timeout
    spans: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=10) as http:
        while time.monotonic() < deadline:
            resp = await http.get(f"{TEMPO}/api/traces/{trace_id}")
            if resp.status_code == 200:
                spans = span_index(resp.json())
                if want <= {s for _, s in spans}:
                    return spans
            await asyncio.sleep(2)
    return spans
