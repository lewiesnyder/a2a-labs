"""The human-task inbox: where an asynchronous HITL question waits for a person.

In production this is a ticket queue, a Slack bot or an approvals app. Here it is a small FastAPI
service that plays two roles:

* the orchestrator's PUSH WEBHOOK: it reads each notification, and when the task is
  ``INPUT_REQUIRED`` / ``AUTH_REQUIRED`` it files one inbox item per open question;
* the person's API: ``GET /items`` lists what waits, ``POST /items/{id}/respond`` sends the answer
  back to the orchestrator as a normal ``SendMessage`` carrying the task id.

The client that started the work does not need to be connected at any point: the task, the push
config and this inbox hold everything.

# CUSTOM: lab-only service (DEV-70). Notifications are checked with the push config's token.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from a2a.types import StreamResponse
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.hitl import Answer, Interrupt, Prompt, extract_interrupts, interpret_reply
from a2a_labs_common.models import get_decision_model
from fastapi import FastAPI, HTTPException, Request
from google.protobuf.json_format import ParseDict

logger = logging.getLogger(__name__)
TOKEN_HEADER = "X-A2A-Notification-Token"


class Inbox:
    def __init__(self, orchestrator: str, token: str) -> None:
        self.orchestrator, self.token = orchestrator, token
        self.items: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []  # every notification, in order

    def file(self, task_id: str, context_id: str, state: str, items: list[Interrupt]) -> None:
        for i in items:
            old = self.items.get(i.id)
            if old and old["status"] == "open":
                continue  # a repeat of the same question
            self.items[i.id] = {
                "id": i.id,
                "task_id": task_id,  # the ORCHESTRATOR's task: the answer goes there
                "context_id": context_id,
                "agent": i.agent,
                "kind": i.prompt.kind,
                "state": i.state,
                "prompt": i.prompt.model_dump(exclude_none=True),
                "deadline_ts": i.deadline_ts,
                "received_at": time.time(),
                "status": "open",
            }

    def close_task(self, task_id: str, state: str) -> None:
        for it in self.items.values():
            if it["task_id"] == task_id and it["status"] == "open":
                it["status"] = "closed"
                it["closed_by"] = state


def create_app(inbox: Inbox | None = None) -> FastAPI:
    app = FastAPI(title="lab11-inbox")
    box = inbox or Inbox(
        os.environ.get("LAB11_ORCHESTRATOR", "lab11-orchestrator"),
        os.environ.get("LAB11_INBOX_TOKEN", "lab11-inbox-token"),
    )
    app.state.inbox = box

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/webhooks/a2a")
    async def webhook(request: Request) -> dict[str, str]:
        if request.headers.get(TOKEN_HEADER) != box.token:
            raise HTTPException(401, "bad notification token")
        payload = await request.json()
        event = ParseDict(payload, StreamResponse(), ignore_unknown_fields=True)
        kind = event.WhichOneof("payload")
        if kind == "status_update":
            su = event.status_update
            task_id, context_id, status = su.task_id, su.context_id, su.status
        elif kind == "task":
            task_id, context_id, status = event.task.id, event.task.context_id, event.task.status
        else:  # artifact updates are progress; nothing for a person to do
            return {"status": "ignored"}
        from a2a_labs_common.a2a.states import state_name

        state = state_name(status.state)
        box.events.append({"task_id": task_id, "state": state, "at": time.time()})
        if state in ("INPUT_REQUIRED", "AUTH_REQUIRED") and status.HasField("message"):
            box.file(task_id, context_id, state, extract_interrupts(list(status.message.parts)))
        elif state in ("COMPLETED", "FAILED", "REJECTED", "CANCELED"):
            box.close_task(task_id, state)
        return {"status": "ok"}

    @app.get("/items")
    def list_items(status: str | None = None) -> list[dict[str, Any]]:
        rows = sorted(box.items.values(), key=lambda r: r["received_at"])
        return [r for r in rows if status is None or r["status"] == status]

    @app.get("/events")
    def events() -> list[dict[str, Any]]:
        return box.events

    @app.post("/items/{item_id}/respond")
    async def respond(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """``{"answer": {...}}`` or ``{"text": "yes, go ahead"}`` (read by the decision model)."""
        item = box.items.get(item_id)
        if item is None:
            raise HTTPException(404, "no such item")
        if item["status"] != "open":
            raise HTTPException(409, f"item is {item['status']}")
        prompt = Prompt(**item["prompt"])
        if "answer" in body:
            answer = Answer(**body["answer"])
        else:
            answer, probs = await interpret_reply(
                str(body.get("text", "")), prompt, get_decision_model()
            )
            if answer is None:
                raise HTTPException(422, f"could not read the reply (reading: {probs})")
        answer.by = answer.by or str(body.get("by", "inbox user"))
        async with await connect(box.orchestrator, streaming=False, timeout=120) as conn:
            res = await send_text(
                conn,
                "human answer",
                task_id=item["task_id"],
                context_id=item["context_id"],
                data={"hitl": {"answers": {item_id: answer.model_dump(exclude_none=True)}}},
            )
        item["status"], item["answered_by"] = "answered", answer.by
        from a2a_labs_common.a2a.states import state_name

        return {"item": item_id, "task_state": state_name(res.state) if res.state else None}

    return app
