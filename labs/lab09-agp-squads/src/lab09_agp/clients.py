"""Client side of AGP for the notebook and tests: send an Intent, read a gateway's table."""

from __future__ import annotations

from typing import Any

import httpx
from a2a.helpers import get_data_parts
from a2a_labs_common.a2a.client import Connected, SendResult, connect, send_text
from a2a_labs_common.agp import AGP_URI, Intent, intent_data
from a2a_labs_common.config import endpoint


async def agp_client(gateway: str) -> Connected:
    """A client that activates the AGP extension on every request."""
    return await connect(gateway, extensions=[AGP_URI], streaming=False)


async def send_intent(
    conn: Connected,
    capability: str,
    payload: dict[str, Any],
    constraints: dict[str, Any] | None = None,
) -> SendResult:
    intent = Intent(target_capability=capability, payload=payload, policy_constraints=constraints)
    return await send_text(conn, "AGP intent", data=intent_data(intent))


def route_of(result: SendResult) -> dict[str, Any]:
    """The ``agp-route`` artifact a gateway attached: path, cost, losers, downstream task."""
    assert result.task is not None
    for art in result.task.artifacts:
        if art.artifact_id == "agp-route":
            return get_data_parts(art.parts)[0]  # type: ignore[no-any-return]
    raise LookupError("no agp-route artifact")


def table(gateway: str) -> dict[str, Any]:
    """``GET /agp/table`` (lab-only debug endpoint)."""
    resp = httpx.get(f"{endpoint(gateway)}/agp/table", timeout=10)
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


def card_json(url: str) -> dict[str, Any]:
    return httpx.get(f"{url}/.well-known/agent-card.json", timeout=10).json()  # type: ignore[no-any-return]


def control(service: str, action: str) -> dict[str, Any]:
    """``POST /agp/pause`` or ``/agp/resume`` on a squad or gateway (lab-only)."""
    return httpx.post(f"{endpoint(service)}/agp/{action}", timeout=10).json()  # type: ignore[no-any-return]
