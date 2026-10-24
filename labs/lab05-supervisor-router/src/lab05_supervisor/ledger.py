"""The Delegation Ledger: what the supervisor knows about each remote agent, per conversation.

A supervisor talks to several remote agents at once, and each one has ITS OWN context and its
OWN tasks. The ledger keeps them apart:

* one entry per (supervisor context, remote agent): never two;
* ``remote_context_id`` is stable, so the remote agent keeps the conversation across turns;
* ``active_task_id`` is set only while the remote task is open (working or waiting for input) and
  is the one a new user message continues; after a terminal state the next message starts a NEW
  task in the same context;
* a ``task_id`` belongs to exactly one agent. Sending agent A's task id to agent B is a protocol
  error, so the ledger refuses to record it.

The in-process ``Ledger`` is the live view (it sees a task the moment it pauses); the graph state
carries a serialisable copy of each entry, which survives the checkpointer.
"""

from __future__ import annotations

from typing import TypedDict

OPEN_STATES = frozenset({"SUBMITTED", "WORKING", "INPUT_REQUIRED", "AUTH_REQUIRED"})
WAITING_STATES = frozenset({"INPUT_REQUIRED", "AUTH_REQUIRED"})
UNREACHABLE = "UNREACHABLE"  # not an A2A state: the call never got an answer


class LedgerEntry(TypedDict):
    agent: str
    service: str
    remote_context_id: str
    active_task_id: str | None
    last_state: str
    task_ids: list[str]


class Ledger:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], LedgerEntry] = {}
        self._updates: dict[tuple[str, str], int] = {}

    def entry(self, thread: str, agent: str) -> LedgerEntry | None:
        return self._entries.get((thread, agent))

    def entries(self, thread: str) -> dict[str, LedgerEntry]:
        return {a: e for (t, a), e in self._entries.items() if t == thread}

    def updates(self, thread: str, agent: str) -> int:
        return self._updates.get((thread, agent), 0)

    def seed(self, thread: str, entry: LedgerEntry | None) -> None:
        """Load an entry from graph state (e.g. after a restart) unless a live one exists."""
        if entry and (thread, entry["agent"]) not in self._entries:
            self._entries[(thread, entry["agent"])] = LedgerEntry(**entry)

    def record(
        self,
        thread: str,
        agent: str,
        service: str,
        context_id: str,
        task_id: str | None,
        state: str,
    ) -> LedgerEntry:
        if task_id:
            for (t, other), e in self._entries.items():
                if t == thread and other != agent and task_id in e["task_ids"]:
                    raise ValueError(
                        f"task {task_id} belongs to {other!r}; never send it to {agent!r}"
                    )
        e = self._entries.get((thread, agent)) or LedgerEntry(
            agent=agent,
            service=service,
            remote_context_id=context_id,
            active_task_id=None,
            last_state=state,
            task_ids=[],
        )
        e["remote_context_id"] = context_id
        if task_id and task_id not in e["task_ids"]:
            e["task_ids"].append(task_id)
        e["active_task_id"] = task_id if state in OPEN_STATES else None
        e["last_state"] = state
        self._entries[(thread, agent)] = e
        self._updates[(thread, agent)] = self._updates.get((thread, agent), 0) + 1
        return e

    def unreachable(self, thread: str, agent: str, service: str, context_id: str) -> LedgerEntry:
        return self.record(thread, agent, service, context_id, None, UNREACHABLE)
