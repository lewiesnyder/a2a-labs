"""Unit tests: model role resolution, fakes and decision parsing (no network, no LLM)."""

from __future__ import annotations

import httpx
import pytest
import respx
from a2a_labs_common import config
from a2a_labs_common.models import (
    describe_models,
    get_adk_model,
    get_decision_model,
    get_langchain_model,
    get_strands_model,
)
from a2a_labs_common.models import settings as ms
from a2a_labs_common.models.decision import (
    FakeDecisionModel,
    OllamaSystemOneDecisionModel,
    Question,
    parse_answer,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("LLM_PROVIDER", "DECISION_PROVIDER", "QUICK_MODEL", "REASONING_THINK"):
        monkeypatch.delenv(v, raising=False)
    config.settings.cache_clear()
    ms.reset_cache()


def test_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = {r["role"]: r for r in describe_models()}
    assert rows["reasoning"]["model"] == "qwen3.8:27b"
    assert rows["decision"]["provider"] == "ollama_systemone"
    monkeypatch.setenv("QUICK_MODEL", "phi4-mini:latest")
    ms.reset_cache()
    assert ms.role_config("quick").model == "phi4-mini:latest"


def test_think_is_coerced_to_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ms.role_config("reasoning").think is True
    monkeypatch.setenv("REASONING_THINK", "false")
    ms.reset_cache()
    assert ms.role_config("reasoning").think is False


def test_decision_is_not_a_chat_model() -> None:
    for getter in (get_langchain_model, get_adk_model, get_strands_model):
        with pytest.raises(ValueError, match="get_decision_model"):
            getter("decision")


def test_real_adapters_construct_without_network() -> None:
    lc = get_langchain_model("quick")
    assert lc.model == "llama3.2:latest"
    assert get_langchain_model("reasoning").reasoning is True
    assert get_adk_model("quick").model == "ollama_chat/llama3.2:latest"
    assert get_strands_model("quick").get_config()["model_id"] == "llama3.2:latest"


async def test_fake_langchain_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert (await get_langchain_model("quick").ainvoke("ping")).content == "pong"
    assert "[fake quick]" in (await get_langchain_model("quick").ainvoke("other")).content


async def test_fake_strands_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    from strands import Agent

    result = await Agent(model=get_strands_model("quick"), callback_handler=None).invoke_async(
        "ping"
    )
    assert "pong" in str(result)


async def test_fake_adk_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    req = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="ping")])])
    out = [r async for r in get_adk_model("quick").generate_content_async(req)]
    assert out[0].content.parts[0].text == "pong"


async def test_fake_decision_defaults_and_scripts() -> None:
    m = FakeDecisionModel({"route": "weather", "ok": 0.2})
    choice, probs = await m.choose("s", "which?", {"currency": "c", "weather": "w", "none": "n"})
    assert choice == "currency" and probs["currency"] == 1.0  # unscripted name "q" -> first option
    r = await m.decide(
        "s",
        {
            "route": Question(
                type="choice", instructions="?", criteria={"currency": "c", "weather": "w"}
            ),
            "ok": Question(type="noul", instructions="?"),
        },
    )
    assert r.answers["route"].choice == "weather" and r.answers["ok"].noul == 0.2
    assert await m.check("s", "yes?") == (True, 0.9)


def test_parse_answer_keeps_unknown_keys() -> None:
    a = parse_answer("choice", {"choice": "x", "confidence": 0.9, "extra": 1})
    assert a.confidence == 0.9 and a.raw["extra"] == 1


@respx.mock
async def test_systemone_request_and_response() -> None:
    route = respx.post("http://o:11434/v1/systemone").mock(
        return_value=httpx.Response(
            200,
            json={
                "answers": {
                    "q": {
                        "type": "choice",
                        "choice": "a",
                        "probabilities": {"a": 0.99, "none": 0.01},
                        "confidence": 0.98,
                    }
                }
            },
        )
    )
    m = OllamaSystemOneDecisionModel("http://o:11434", "nimble-v3:latest")
    choice, probs = await m.choose("state", "pick", {"a": "A", "none": "N"})
    assert (choice, probs["a"]) == ("a", 0.99)
    sent = route.calls[0].request
    assert b'"state":"state"' in sent.content.replace(b" ", b"")


async def test_get_decision_model_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECISION_PROVIDER", "fake")
    ms.reset_cache()
    assert isinstance(get_decision_model(), FakeDecisionModel)


def test_host_runtime_rewrites_docker_ollama_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    ms.reset_cache()
    assert ms.role_config("quick").base_url == "http://localhost:11434"
    monkeypatch.setenv("A2A_LABS_RUNTIME", "docker")
    config.settings.cache_clear()
    ms.reset_cache()
    assert ms.role_config("quick").base_url == "http://host.docker.internal:11434"
