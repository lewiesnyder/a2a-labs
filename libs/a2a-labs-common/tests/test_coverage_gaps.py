"""Remaining unit tests: telemetry init, client helpers, decision fallbacks, adapters."""

from __future__ import annotations

import atexit
import logging

import httpx
import pytest
import respx
from a2a.client import ClientCallContext
from a2a.types import TaskState
from a2a_labs_common import config
from a2a_labs_common.a2a import cards
from a2a_labs_common.a2a.client import (
    collect,
    connect,
    result_text,
    send_text,
    wait_for_cards,
    wait_for_terminal,
)
from a2a_labs_common.a2a.server import build_a2a_app
from a2a_labs_common.a2a.strands import StrandsA2AExecutor
from a2a_labs_common.models import get_adk_model, get_langchain_model, get_strands_model
from a2a_labs_common.models import settings as ms
from a2a_labs_common.models.decision import (
    LLMFallbackDecisionModel,
    OllamaSystemOneDecisionModel,
    Question,
    _FallingBack,
    get_decision_model,
)
from a2a_labs_common.telemetry import a2a_client as tclient
from a2a_labs_common.telemetry import setup as tsetup
from a2a_labs_common.testing import asgi_client


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in (
        "LLM_PROVIDER",
        "DECISION_PROVIDER",
        "A2A_LABS_OTEL_ENABLED",
        "DECISION_FALLBACK_ON_ERROR",
    ):
        monkeypatch.delenv(v, raising=False)
    config.settings.cache_clear()
    ms.reset_cache()
    tsetup.reset_for_tests()


# ── telemetry init ──────────────────────────────────────────────────────────────────────────
def test_init_disabled_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_LABS_OTEL_ENABLED", "false")
    config.settings.cache_clear()
    h = tsetup.init_telemetry("svc")
    assert not h.enabled and tsetup.init_telemetry("other") is h  # idempotent
    h.flush()
    tsetup.instrument_app(object())  # must not raise when disabled


def test_init_enabled_builds_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")  # nothing listens
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        h = tsetup.init_telemetry("svc-test", framework="service", lab_id="lab99")
        assert h.enabled and h.tracer_provider and h.meter_provider and h.logger_provider
        res = h.tracer_provider.resource.attributes
        assert res["service.namespace"] == "a2a-labs" and res["a2a_labs.lab_id"] == "lab99"
        assert tsetup.init_telemetry("again") is h
        rec = logging.makeLogRecord({"msg": "x"})  # regression: console format needs trace ids
        assert logging.Formatter(tsetup.CONSOLE_FORMAT).format(rec).endswith("x")
    finally:
        atexit.unregister(h.shutdown)  # nothing listens on :1; flush now, inside the test
        h.shutdown()
        for hd in list(root.handlers):
            if hd not in before:
                root.removeHandler(hd)


def test_framework_enablement() -> None:
    from a2a_labs_common.telemetry.frameworks import enable_framework
    from opentelemetry.sdk.trace import TracerProvider

    tp = TracerProvider()
    enable_framework("strands", tp)
    enable_framework("langgraph", tp)
    enable_framework("adk", tp)


# ── client helpers ──────────────────────────────────────────────────────────────────────────
class Agent:
    async def stream_async(self, prompt: str, **_: object):
        yield {"data": "ok"}


def _app():  # type: ignore[no-untyped-def]
    card = cards.build_card(
        service="lab00-protocol-agent",
        name="Echo",
        description="Echoes text back",
        skills=[cards.skill("e", "E", "d", [], [])],
    )
    card.supported_interfaces[0].url = "http://testserver"
    return build_a2a_app(card=card, executor=StrandsA2AExecutor(lambda _c: Agent()))


async def test_wait_for_terminal_get_task_and_extensions_header() -> None:
    http = asgi_client(_app())
    c = await connect(
        "http://testserver",
        httpx_client=http,
        streaming=False,
        extensions=["urn:x"],
        headers={"X-Test": "1"},
    )
    assert http.headers["A2A-Extensions"] == "urn:x" and http.headers["X-Test"] == "1"
    r = await send_text(
        c,
        "hi",
        metadata={"k": "v"},
        data={"n": 1},
        history_length=2,
        reference_task_ids=["t0"],
        return_immediately=False,
    )
    task = await wait_for_terminal(c, r.task_id or "", poll_interval=0.01, timeout=2)
    assert task.status.state == TaskState.TASK_STATE_COMPLETED and result_text(task) == "ok"
    assert r.context_id == task.context_id
    await c.aclose()


async def test_collect_requires_a_result() -> None:
    with pytest.raises(ValueError):
        collect([])


async def test_unknown_task_raises_a2a_error_and_span_finishes() -> None:
    from a2a.types import GetTaskRequest

    http = asgi_client(_app())
    c = await connect("http://testserver", httpx_client=http, streaming=False)
    ctx = ClientCallContext()
    with pytest.raises(Exception) as ei:
        await c.client.get_task(GetTaskRequest(id="nope"), context=ctx)
    tclient.finish(ctx, ei.value)  # idempotent, records the error
    await c.aclose()


@respx.mock
async def test_wait_for_cards_success_and_timeout() -> None:
    respx.get("http://a/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json={})
    )
    await wait_for_cards(["http://a"], timeout=2)
    respx.get("http://b/.well-known/agent-card.json").mock(return_value=httpx.Response(503))
    with pytest.raises(RuntimeError, match="make up LAB="):
        await wait_for_cards(["http://b"], timeout=0.2)


# ── decision fallbacks ──────────────────────────────────────────────────────────────────────
async def test_llm_fallback_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    replies = iter(['noise {"choice": "weather"} tail', '{"p": 0.7}', '{"level": 2}', "not json"])
    monkeypatch.setattr(
        "a2a_labs_common.testing.scripted_reply", lambda role, prompt: next(replies)
    )
    m = LLMFallbackDecisionModel("quick")
    r = await m.decide(
        "evidence",
        {
            "route": Question(
                type="choice",
                instructions="?",
                criteria={"currency": "c", "weather": "w", "none": "n"},
            ),
            "ok": Question(type="noul", instructions="?"),
            "lvl": Question(type="score", instructions="?", criteria=["a", "b", "c"]),
            "bad": Question(type="choice", instructions="?", criteria={"x": "x", "none": "n"}),
        },
    )
    assert r.backend == "llm_fallback"
    assert r.answers["route"].choice == "weather" and r.answers["ok"].noul == 0.7
    assert (
        r.answers["lvl"].score == 2.0 and r.answers["bad"].choice == "none"
    )  # last option = escape


@respx.mock
async def test_systemone_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setattr(
        "a2a_labs_common.testing.scripted_reply", lambda role, prompt: '{"choice": "a"}'
    )
    respx.post("http://o/v1/systemone").mock(return_value=httpx.Response(404))
    m = _FallingBack(
        OllamaSystemOneDecisionModel("http://o", "nimble"), LLMFallbackDecisionModel("quick")
    )
    choice, _ = await m.choose("s", "?", {"a": "A", "none": "N"})
    assert choice == "a" and m.backend == "llm_fallback"
    score, _ = await m.score("s", "?", ["x", "y"])
    assert score == 0.0


def test_get_decision_model_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_decision_model(), _FallingBack)
    monkeypatch.setenv("DECISION_FALLBACK_ON_ERROR", "false")
    assert isinstance(get_decision_model(), OllamaSystemOneDecisionModel)
    monkeypatch.setenv("DECISION_PROVIDER", "llm_fallback")
    ms.reset_cache()
    assert isinstance(get_decision_model(), LLMFallbackDecisionModel)


# ── non-Ollama adapters ─────────────────────────────────────────────────────────────────────
def test_other_providers_construct(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(k, "test-key")
    assert get_langchain_model("quick", provider="openai").model_name == "llama3.2:latest"
    assert get_langchain_model("quick", provider="anthropic").model == "llama3.2:latest"
    assert get_adk_model("quick", provider="openai").model == "openai/llama3.2:latest"
    assert get_adk_model("quick", provider="gemini") == "llama3.2:latest"
    assert (
        get_strands_model("quick", provider="openai").get_config()["model_id"] == "llama3.2:latest"
    )
    assert (
        get_strands_model("quick", provider="anthropic").get_config()["model_id"]
        == "llama3.2:latest"
    )
