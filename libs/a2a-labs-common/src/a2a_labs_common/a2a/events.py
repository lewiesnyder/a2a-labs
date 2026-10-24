"""StreamResponse helpers (spec 03 §5)."""

from __future__ import annotations

from typing import Literal, cast

from a2a.helpers import get_stream_response_text
from a2a.types import StreamResponse

from a2a_labs_common.a2a.states import state_name

Kind = Literal["task", "message", "status_update", "artifact_update"]


def kind(ev: StreamResponse) -> Kind:
    which = ev.WhichOneof("payload")
    if which is None:
        raise ValueError("empty StreamResponse")
    return cast(Kind, which)


def state_of(ev: StreamResponse) -> str | None:
    """State name for task / status events, else None."""
    k = kind(ev)
    if k == "task":
        return state_name(ev.task.status.state)
    if k == "status_update":
        return state_name(ev.status_update.status.state)
    return None


def event_text(ev: StreamResponse) -> str:
    return get_stream_response_text(ev)
