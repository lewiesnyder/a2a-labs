"""Deterministic fakes for unit and fake-mode integration tests (spec 03 §7, 08 §4).

Scripts live in ``config/fake_responses.yaml``: ``{role: [{match: <substring>, reply: <text>}]}``.
First matching entry wins; otherwise a generic echo reply is returned.
Tool-call scripts are not supported yet (see docs/spec-deviations.md DEV-2).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Sequence
from typing import Any

import httpx
import yaml

from a2a_labs_common.config import find_config_dir
from a2a_labs_common.models.decision import FakeDecisionModel

__all__ = ["FakeDecisionModel", "asgi_client", "scripted_reply"]


def scripted_reply(role: str, prompt: str) -> str:
    path = find_config_dir() / "fake_responses.yaml"
    script: dict[str, list[dict[str, str]]] = (
        yaml.safe_load(path.read_text()) if path.is_file() else {}
    ) or {}
    for entry in script.get(role, []):
        if entry["match"] in prompt:
            return entry["reply"]
    return f"[fake {role}] {prompt[:80]}"


def asgi_client(app: Any, base_url: str = "http://testserver") -> httpx.AsyncClient:
    """An httpx client wired straight into an ASGI app: no ports, no network."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


# ── LangChain ───────────────────────────────────────────────────────────────────────────────
def langchain_fake(role: str) -> Any:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage
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
            prompt = str(messages[-1].content) if messages else ""
            msg = AIMessage(content=scripted_reply(role, prompt))
            return ChatResult(generations=[ChatGeneration(message=msg)])

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self  # no tool calling in fake mode yet

    return ScriptedChat()


# ── Strands ─────────────────────────────────────────────────────────────────────────────────
def strands_fake(role: str) -> Any:
    from strands.models import Model

    class ScriptedModel(Model):
        def update_config(self, **model_config: Any) -> None:
            return None

        def get_config(self) -> Any:
            return {"model_id": "fake"}

        async def stream(
            self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
        ) -> AsyncIterable[Any]:
            last = messages[-1]["content"] if messages else []
            prompt = " ".join(b.get("text", "") for b in last if isinstance(b, dict))
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": scripted_reply(role, prompt)}}}
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

    class ScriptedLlm(BaseLlm):
        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            parts = llm_request.contents[-1].parts if llm_request.contents else []
            prompt = " ".join(p.text or "" for p in parts)
            reply = types.Content(
                role="model", parts=[types.Part(text=scripted_reply(role, prompt))]
            )
            yield LlmResponse(content=reply)

    return ScriptedLlm(model=f"fake-{role}")
