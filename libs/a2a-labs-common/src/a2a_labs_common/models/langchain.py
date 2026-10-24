"""role -> LangChain chat model (spec 04 §3.1)."""

from __future__ import annotations

from typing import Any

from a2a_labs_common.models.settings import ModelRole, api_key, provider_config, role_config


def get_langchain_model(role: ModelRole = "quick", **overrides: Any) -> Any:
    if role == "decision":
        raise ValueError("decision role is not a chat model; use get_decision_model()")
    c = role_config(role)
    p = overrides.pop("provider", c.provider)
    temperature = overrides.pop("temperature", c.temperature)
    if p == "fake":
        from a2a_labs_common.testing import langchain_fake

        return langchain_fake(role)
    if p == "ollama":
        from langchain_ollama import ChatOllama

        # `reasoning` toggles qwen3 thinking [VERIFY resolved: field exists in langchain-ollama]
        return ChatOllama(
            model=c.model,
            base_url=c.base_url,
            temperature=temperature,
            num_ctx=c.num_ctx,
            num_predict=c.max_tokens,
            reasoning=c.think,
            **overrides,
        )
    if p in ("openai", "openai_compatible"):
        from langchain_openai import ChatOpenAI

        base = provider_config(p).get("base_url") or None
        return ChatOpenAI(
            model=c.model, base_url=base, api_key=api_key(p), temperature=temperature, **overrides
        )
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=c.model, temperature=temperature, **overrides)
    if p == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=c.model, temperature=temperature, **overrides)
    if p == "bedrock":
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model_id=c.model,  # Bedrock's kwarg is model_id, not model
            region_name=provider_config(p).get("region"),
            temperature=temperature,
            **overrides,
        )
    from langchain.chat_models import init_chat_model

    return init_chat_model(c.model, model_provider=p, temperature=temperature, **overrides)
