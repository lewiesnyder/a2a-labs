"""Human-in-the-loop conventions on top of A2A's interrupted states (spec lab11 §3).

A2A gives the state machine: ``INPUT_REQUIRED`` and ``AUTH_REQUIRED`` pause a task, and a new
``SendMessage`` carrying the same ``task_id`` resumes it. It does NOT say what the question or the
answer look like. This module is that convention: one data part per message, keyed ``hitl``, so a
UI can render an approval, a choice or a consent screen while the text part stays readable.

* agent -> human : ``{"hitl": {"kind", "prompt", "options", "proposal", "deadline"}}``
* human -> agent : ``{"hitl": {"answer": {...}}}``
* orchestrator -> human (all open questions): ``{"hitl": {"interrupts": [...]}}``
* human -> orchestrator: ``{"hitl": {"answers": {"<interrupt id>": {...}}}}``

# CUSTOM: the convention and the reply reader are ours; A2A only defines the states (DEV-70).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from a2a.helpers import get_data_parts, new_data_part
from a2a.types import Part
from pydantic import BaseModel, Field

from a2a_labs_common.models.decision import DecisionModel, Question

Kind = Literal["approval", "choice", "input", "consent"]


class Option(BaseModel):
    id: str
    label: str = ""


class Prompt(BaseModel):
    """What an agent asks a person."""

    kind: Kind
    prompt: str
    options: list[Option] = Field(default_factory=list)
    proposal: dict[str, Any] = Field(default_factory=dict)  # what the agent will do if approved
    deadline: str | None = None  # ISO 8601, informational: the enforcer is the orchestrator


class Answer(BaseModel):
    """What a person (or the clock) replies."""

    decision: str | None = None  # approval / consent: approve | reject | edit
    choice: str | None = None  # choice: an option id
    edit: dict[str, Any] | None = None  # edit: replacement values for the proposal
    text: str | None = None  # input: free text; also the reason on a reject
    by: str | None = None  # who answered: a person, "timeout", ...


class Interrupt(BaseModel):
    """One open question, as the orchestrator lists it to the human."""

    id: str
    agent: str
    task_id: str  # the DELEGATE's task: the answer is routed there
    context_id: str
    state: Literal["INPUT_REQUIRED", "AUTH_REQUIRED"]
    prompt: Prompt
    deadline_ts: float | None = None  # epoch seconds, enforced by the reaper


# ── building parts ────────────────────────────────────────────────────────────────────────────
def prompt_part(prompt: Prompt) -> Part:
    return new_data_part({"hitl": prompt.model_dump(exclude_none=True)})


def answer_part(answer: Answer) -> Part:
    return new_data_part({"hitl": {"answer": answer.model_dump(exclude_none=True)}})


def answers_part(answers: dict[str, Answer]) -> Part:
    body = {k: v.model_dump(exclude_none=True) for k, v in answers.items()}
    return new_data_part({"hitl": {"answers": body}})


def interrupts_part(items: Iterable[Interrupt]) -> Part:
    return new_data_part({"hitl": {"interrupts": [i.model_dump(exclude_none=True) for i in items]}})


# ── reading parts ─────────────────────────────────────────────────────────────────────────────
def _hitl(parts: Sequence[Part]) -> dict[str, Any] | None:
    for data in get_data_parts(parts):
        if isinstance(data, dict) and isinstance(data.get("hitl"), dict):
            return data["hitl"]  # type: ignore[no-any-return]
    return None


def extract_prompt(parts: Sequence[Part]) -> Prompt | None:
    body = _hitl(parts)
    if body is None or "prompt" not in body:
        return None
    return Prompt(**{k: v for k, v in body.items() if k in Prompt.model_fields})


def extract_answer(parts: Sequence[Part]) -> Answer | None:
    body = _hitl(parts)
    if body is None or not isinstance(body.get("answer"), dict):
        return None
    return Answer(**body["answer"])


def extract_answers(parts: Sequence[Part]) -> dict[str, Answer] | None:
    body = _hitl(parts)
    if body is None or not isinstance(body.get("answers"), dict):
        return None
    return {k: Answer(**v) for k, v in body["answers"].items()}


def extract_interrupts(parts: Sequence[Part]) -> list[Interrupt]:
    body = _hitl(parts)
    return [Interrupt(**i) for i in (body or {}).get("interrupts", [])]


# ── reading a free-text reply ─────────────────────────────────────────────────────────────────
REPLY_OPTIONS = {
    "approve": "the person agrees, allows or confirms the proposal",
    "reject": "the person refuses, declines or cancels",
    "unclear": "anything else: a question, an unrelated remark, gibberish",
}


async def interpret_reply(
    text: str, prompt: Prompt, decision: DecisionModel
) -> tuple[Answer | None, dict[str, float]]:
    """Turn what a person typed into an ``Answer``, or ``None`` when it cannot be read.

    * ``choice``: an option id or label mentioned in the text (no model needed);
    * ``approval`` / ``consent``: a decision-model ``choice`` over approve / reject / unclear.
    """
    low = text.lower()
    if prompt.kind == "choice":
        for opt in prompt.options:
            if re.search(rf"\b{re.escape(opt.id.lower())}\b", low) or (
                opt.label and opt.label.lower() in low
            ):
                return Answer(choice=opt.id, text=text), {}
        return None, {}
    if prompt.kind == "input":
        return (Answer(text=text), {}) if text.strip() else (None, {})
    res = await decision.decide(
        f"Reply: {text}\n(Question to the person: {prompt.prompt})",
        {
            "reply": Question(
                type="choice",
                instructions="What does the person's reply mean?",
                criteria=REPLY_OPTIONS,
            )
        },
    )
    ans = res.answers["reply"]
    probs = ans.probabilities if isinstance(ans.probabilities, dict) else {}
    if ans.choice in ("approve", "reject"):
        return Answer(decision=ans.choice, text=text), {k: float(v) for k, v in probs.items()}
    return None, {k: float(v) for k, v in probs.items()}
