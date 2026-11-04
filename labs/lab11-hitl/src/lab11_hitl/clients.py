"""Notebook and test helpers: the human's side of the conversation."""

from __future__ import annotations

from typing import Any

import httpx
from a2a.types import Task
from a2a_labs_common.a2a.client import Connected, SendResult, send_text
from a2a_labs_common.config import endpoint
from a2a_labs_common.hitl import Answer, Interrupt, extract_interrupts


def open_questions(task: Task) -> list[Interrupt]:
    """The questions a paused orchestrator task is waiting on (from its status message)."""
    return (
        extract_interrupts(list(task.status.message.parts))
        if task.status.HasField("message")
        else []
    )


async def answer(
    conn: Connected, res: SendResult, answers: dict[str, Answer] | None = None, text: str = ""
) -> SendResult:
    """Resume the orchestrator task: structured ``answers`` by question id, or plain ``text``."""
    assert res.task is not None
    data = (
        {"hitl": {"answers": {k: v.model_dump(exclude_none=True) for k, v in answers.items()}}}
        if answers
        else None
    )
    return await send_text(
        conn, text or "human answer", task_id=res.task_id, context_id=res.context_id, data=data
    )


def inbox(path: str = "/items", **params: Any) -> Any:
    return httpx.get(f"{endpoint('lab11-inbox')}{path}", params=params, timeout=10).json()


def inbox_respond(item_id: str, body: dict[str, Any]) -> httpx.Response:
    return httpx.post(f"{endpoint('lab11-inbox')}/items/{item_id}/respond", json=body, timeout=60)


def ledger(task_id: str) -> list[dict[str, Any]]:
    """The orchestrator's per-delegate ledger for a trip (``GET /hitl/ledger/{task_id}``)."""
    return httpx.get(f"{endpoint('lab11-orchestrator')}/hitl/ledger/{task_id}", timeout=10).json()  # type: ignore[no-any-return]
