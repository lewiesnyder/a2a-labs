"""Identifier helpers (spec 03). Task IDs are server-generated; clients only make these."""

from __future__ import annotations

import uuid

_NS = uuid.UUID("6f1c2a52-7d0e-4c58-9a57-6a2a1ab5e5a2")


def new_message_id() -> str:
    return str(uuid.uuid4())


def new_context_id() -> str:
    return str(uuid.uuid4())


def derived_context_id(parent: str, name: str) -> str:
    """Stable child context (uuid5), so a supervisor can reuse one context per sub-agent."""
    return str(uuid.uuid5(_NS, f"{parent}:{name}"))
