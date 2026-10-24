"""Decision models (spec 04 §4): Nimble ``/v1/systemone``, LLM fallback and a fake.

Implements: spec 04 §4. Nimble is a decision model, NOT a chat model (M-1): it returns
probabilities over predefined answers. Always include a ``none``/``other`` option (M-2).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Literal, Protocol

import httpx
from opentelemetry import trace
from pydantic import BaseModel

from a2a_labs_common.models.settings import role_config
from a2a_labs_common.telemetry.metrics import instruments

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("a2a_labs_common.decision")

Backend = Literal["ollama_systemone", "llm_fallback", "fake"]


class Question(BaseModel):
    type: Literal["choice", "noul", "score"]
    instructions: str
    criteria: dict[str, Any] | list[str] = {}


class Answer(BaseModel):
    type: Literal["choice", "noul", "score"]
    choice: str | None = None
    noul: float | None = None  # P(true)
    score: float | None = None
    confidence: float | None = None
    probabilities: dict[str, float] | list[float] | None = None
    legend: list[str] | None = None
    raw: dict[str, Any] = {}


class DecisionResult(BaseModel):
    answers: dict[str, Answer]
    model: str
    latency_ms: float
    backend: Backend


def parse_answer(qtype: Literal["choice", "noul", "score"], payload: dict[str, Any]) -> Answer:
    """Tolerant: read known keys, keep everything in ``raw`` (the shape is not fully documented)."""
    legend = payload.get("legend")
    if isinstance(
        legend, dict
    ):  # Nimble sends {"0": "low", "1": "medium", ...} for score questions
        legend = [str(v) for _, v in sorted(legend.items(), key=lambda kv: int(kv[0]))]
    return Answer(
        type=qtype,
        choice=payload.get("choice"),
        noul=payload.get("noul"),
        score=payload.get("score"),
        confidence=payload.get("confidence"),
        probabilities=payload.get("probabilities"),
        legend=legend,
        raw=payload,
    )


class DecisionModel(Protocol):
    async def decide(self, state: str, questions: dict[str, Question]) -> DecisionResult: ...


class _Base:
    """Shared convenience wrappers; subclasses implement ``_decide``."""

    backend: Backend
    model_name: str

    async def _decide(self, state: str, questions: dict[str, Question]) -> dict[str, Answer]:
        raise NotImplementedError

    async def decide(self, state: str, questions: dict[str, Question]) -> DecisionResult:
        t0 = time.perf_counter()
        with _tracer.start_as_current_span(
            "decision.decide",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "gen_ai.request.model": self.model_name,
                "decision.backend": self.backend,
                "decision.questions": ",".join(questions),
            },
        ) as span:
            answers = await self._decide(state, questions)
            for name, a in answers.items():
                _annotate(span, name, a)
                if a.choice is not None:
                    instruments().decision_choice.add(1, {"question": name, "choice": a.choice})
        elapsed = time.perf_counter() - t0
        instruments().decision_duration.record(
            elapsed, {"decision.backend": self.backend, "gen_ai.request.model": self.model_name}
        )
        return DecisionResult(
            answers=answers, model=self.model_name, latency_ms=elapsed * 1000, backend=self.backend
        )

    async def choose(
        self, state: str, instructions: str, options: dict[str, str]
    ) -> tuple[str, dict[str, float]]:
        q = Question(type="choice", instructions=instructions, criteria=options)
        a = (await self.decide(state, {"q": q})).answers["q"]
        probs = a.probabilities if isinstance(a.probabilities, dict) else {}
        return a.choice or "", probs

    async def check(
        self, state: str, instructions: str, threshold: float = 0.5
    ) -> tuple[bool, float]:
        a = (
            await self.decide(state, {"q": Question(type="noul", instructions=instructions)})
        ).answers["q"]
        p = a.noul if a.noul is not None else 0.0
        return p >= threshold, p

    async def score(
        self, state: str, instructions: str, levels: list[str]
    ) -> tuple[float, list[float]]:
        q = Question(type="score", instructions=instructions, criteria=levels)
        a = (await self.decide(state, {"q": q})).answers["q"]
        probs = a.probabilities
        if isinstance(probs, dict):  # live Nimble: {"0": p_low, "1": p_medium, ...}
            probs = [p for _, p in sorted(probs.items(), key=lambda kv: int(kv[0]))]
        return a.score if a.score is not None else 0.0, list(probs or [])


def _annotate(span: trace.Span, name: str, a: Answer) -> None:
    if a.choice is not None:
        span.set_attribute(f"decision.{name}.choice", a.choice)
        if isinstance(a.probabilities, dict) and a.choice in a.probabilities:
            span.set_attribute(f"decision.{name}.p", a.probabilities[a.choice])
    if a.noul is not None:
        span.set_attribute(f"decision.{name}.p", a.noul)
    if a.score is not None:
        span.set_attribute(f"decision.{name}.score", a.score)


class OllamaSystemOneDecisionModel(_Base):
    backend: Backend = "ollama_systemone"

    def __init__(self, base_url: str, model: str, timeout_s: float = 30) -> None:
        self.base_url, self.model_name, self.timeout_s = base_url.rstrip("/"), model, timeout_s

    async def _decide(self, state: str, questions: dict[str, Question]) -> dict[str, Answer]:
        body = {
            "model": self.model_name,
            "state": state,
            "questions": {k: q.model_dump() for k, q in questions.items()},
        }
        # httpx is OTel-instrumented, so this shows as an HTTP child span of decision.decide.
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(f"{self.base_url}/v1/systemone", json=body)
            resp.raise_for_status()
        answers = resp.json()["answers"]
        return {k: parse_answer(questions[k].type, v) for k, v in answers.items()}


def load_decision_script() -> list[dict[str, Any]]:
    """The ``decision:`` section of ``config/fake_responses.yaml`` (empty if absent)."""
    import yaml

    from a2a_labs_common.config import find_config_dir

    path = find_config_dir() / "fake_responses.yaml"
    data = (yaml.safe_load(path.read_text()) if path.is_file() else {}) or {}
    return list(data.get("decision") or [])


class FakeDecisionModel(_Base):
    """First option / ``noul=0.9`` unless scripted (spec 08 §4).

    Two ways to script an answer, both keyed by QUESTION NAME:

    * ``answers={"risk": 2.0}``: explicit, wins always (unit tests).
    * ``script=[{question, match?, answer}, ...]``: the first entry for the question whose
      ``match`` is a case-insensitive substring of the evidence wins (no ``match`` = catch-all).
      ``get_decision_model()`` loads it from ``fake_responses.yaml`` so compose-level runs are
      deterministic. A ``choice`` answer may be a substring of an option key (``weather`` picks
      ``Weather Reporter``).
    """

    backend: Backend = "fake"
    model_name = "fake-decision"

    def __init__(
        self, answers: dict[str, Any] | None = None, script: list[dict[str, Any]] | None = None
    ) -> None:
        self.answers = answers or {}
        self.script = script or []

    def _scripted(self, name: str, state: str) -> Any:
        if name in self.answers:
            return self.answers[name]
        for entry in self.script:
            if entry.get("question") == name and entry.get("match", "").lower() in state.lower():
                return entry["answer"]
        return None

    async def _decide(self, state: str, questions: dict[str, Question]) -> dict[str, Answer]:
        out: dict[str, Answer] = {}
        for name, q in questions.items():
            scripted = self._scripted(name, state)
            if q.type == "choice":
                opts = list(q.criteria)
                pick = opts[0]
                if isinstance(scripted, str):
                    pick = next(
                        (o for o in opts if o == scripted),
                        next((o for o in opts if scripted.lower() in o.lower()), opts[0]),
                    )
                out[name] = Answer(
                    type="choice", choice=pick, probabilities={o: float(o == pick) for o in opts}
                )
            elif q.type == "noul":
                p = float(scripted) if scripted is not None else 0.9
                out[name] = Answer(type="noul", noul=p)
            else:
                levels = list(q.criteria)
                s = float(scripted) if scripted is not None else float(len(levels) - 1)
                out[name] = Answer(type="score", score=s, legend=[str(x) for x in levels])
        return out


class LLMFallbackDecisionModel(_Base):
    """Prompts the fallback chat role and parses JSON (spec 04 §4.3)."""

    backend: Backend = "llm_fallback"

    def __init__(self, fallback_role: str = "quick") -> None:
        self.fallback_role = fallback_role
        self.model_name = role_config(fallback_role).model  # type: ignore[arg-type]

    async def _decide(self, state: str, questions: dict[str, Question]) -> dict[str, Answer]:
        from a2a_labs_common.models.langchain import get_langchain_model

        llm = get_langchain_model(self.fallback_role)  # type: ignore[arg-type]
        out: dict[str, Answer] = {}
        for name, q in questions.items():
            prompt = (
                f"Evidence:\n{state}\n\nQuestion: {q.instructions}\n"
                f"Criteria: {json.dumps(q.criteria)}\n"
                + {
                    "choice": 'Reply ONLY JSON: {"choice": "<option key>"}',
                    "noul": 'Reply ONLY JSON: {"p": <probability 0..1 that the answer is yes>}',
                    "score": 'Reply ONLY JSON: {"level": <0-based index into criteria>}',
                }[q.type]
            )
            text = str((await llm.ainvoke(prompt)).content)
            data = _first_json(text)
            if q.type == "choice":
                opts = list(q.criteria)
                pick = data.get("choice") if data.get("choice") in opts else opts[-1]
                out[name] = Answer(
                    type="choice",
                    choice=pick,
                    probabilities={o: float(o == pick) for o in opts},
                    raw=data,
                )
            elif q.type == "noul":
                out[name] = Answer(type="noul", noul=float(data.get("p", 0.0)), raw=data)
            else:
                out[name] = Answer(
                    type="score",
                    score=float(data.get("level", 0)),
                    legend=[str(x) for x in q.criteria],
                    raw=data,
                )
        return out


def _first_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    try:
        return dict(json.loads(text[start : end + 1]))
    except (ValueError, TypeError):
        logger.warning("fallback decision model returned non-JSON: %r", text[:120])
        return {}


class _FallingBack(_Base):
    """systemone with automatic fallback (DECISION_FALLBACK_ON_ERROR=true)."""

    def __init__(self, primary: OllamaSystemOneDecisionModel, fallback: LLMFallbackDecisionModel):
        self.primary, self.fallback = primary, fallback
        self.backend, self.model_name = primary.backend, primary.model_name

    async def _decide(self, state: str, questions: dict[str, Question]) -> dict[str, Answer]:
        try:
            return await self.primary._decide(state, questions)
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("systemone failed (%s); using LLM fallback", exc)
            self.backend = "llm_fallback"
            return await self.fallback._decide(state, questions)


def get_decision_model() -> DecisionModel:
    cfg = role_config("decision")
    if cfg.provider == "fake":
        return FakeDecisionModel(script=load_decision_script())
    if cfg.provider == "llm_fallback":
        return LLMFallbackDecisionModel(cfg.fallback_role)
    primary = OllamaSystemOneDecisionModel(cfg.base_url, cfg.model, cfg.timeout_s)
    if os.environ.get("DECISION_FALLBACK_ON_ERROR", "true").lower() == "true":
        return _FallingBack(primary, LLMFallbackDecisionModel(cfg.fallback_role))
    return primary
