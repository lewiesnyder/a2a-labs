"""role -> Strands model (spec 04 §3.3). S-3: Ollama needs ``model_id``."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models.settings import ModelRole, api_key, provider_config, role_config


def get_strands_model(role: ModelRole = "quick", **overrides: Any) -> Any:
    if role == "decision":
        raise ValueError("decision role is not a chat model; use get_decision_model()")
    c = role_config(role)
    p = overrides.pop("provider", c.provider)
    if p == "fake":
        from a2a_labs_common.testing import strands_fake

        return strands_fake(role)
    if p == "ollama":
        from strands.models.ollama import OllamaModel

        options: dict[str, Any] = {"num_ctx": c.num_ctx} if c.num_ctx else {}
        # additional_args reaches the Ollama chat request, where `think` toggles thinking
        # [VERIFY with a real run in M3]
        return OllamaModel(
            c.base_url,
            model_id=c.model,
            temperature=c.temperature,
            max_tokens=c.max_tokens,
            options=options,
            additional_args={"think": c.think},
            **overrides,
        )
    if p in ("openai", "openai_compatible"):
        from strands.models.openai import OpenAIModel

        client_args = {
            "api_key": api_key(p),
            "base_url": provider_config(p).get("base_url") or None,
        }
        return OpenAIModel(client_args=client_args, model_id=c.model, **overrides)
    if p == "anthropic":
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(model_id=c.model, max_tokens=c.max_tokens or 1024, **overrides)
    if p == "bedrock":
        from strands.models import BedrockModel

        return BedrockModel(model_id=c.model, **overrides)
    from strands.models.litellm import LiteLLMModel

    return LiteLLMModel(model_id=f"{p}/{c.model}", **overrides)
