"""role -> ADK model (spec 04 §3.2).

ADK-2: every Agent MUST pass ``model=`` explicitly. ADK-3: Ollama uses ``ollama_chat/``.
"""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models.settings import ModelRole, role_config


def get_adk_model(role: ModelRole = "quick", **overrides: Any) -> Any:
    if role == "decision":
        raise ValueError("decision role is not a chat model; use get_decision_model()")
    c = role_config(role)
    p = overrides.pop("provider", c.provider)
    if p == "fake":
        from a2a_labs_common.testing import adk_fake

        return adk_fake(role)
    if p == "gemini":
        return c.model  # native string
    from google.adk.models.lite_llm import LiteLlm

    if p == "ollama":
        # Explicit api_base: .env may carry the container-side URL in OLLAMA_API_BASE, and the
        # environment variable would win over setdefault on a host run (DEV-5).
        return LiteLlm(model=f"ollama_chat/{c.model}", api_base=c.base_url, **overrides)
    prefix = {
        "openai": "openai",
        "openai_compatible": "openai",
        "anthropic": "anthropic",
        "bedrock": "bedrock",
    }.get(p, p)
    return LiteLlm(model=f"{prefix}/{c.model}", **overrides)
