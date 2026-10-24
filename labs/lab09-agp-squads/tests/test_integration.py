"""Against the running compose profile (`make e2e LAB=lab09`)."""

from __future__ import annotations

import asyncio
import time

import pytest
from a2a.types import TaskState
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.agp import AgpPolicyViolationError, AgpRouteNotFoundError
from lab09_agp.clients import agp_client, route_of, send_intent, table

pytestmark = pytest.mark.compose
ROOT = "lab09-root-gateway"


async def test_the_root_table_converges() -> None:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        caps = table(ROOT)["capabilities"]
        if sum(len(v) for v in caps.values()) >= 7:
            break
        await asyncio.sleep(1)
    assert sum(len(v) for v in caps.values()) == 7
    assert sorted(r["cost"] for r in caps["finance.fx.quote"]) == [2, 5]


async def test_an_intent_takes_the_cheapest_route() -> None:
    async with await agp_client(ROOT) as c:
        res = await send_intent(
            c, "finance.fx.quote", {"amount": 100, "from": "USD", "to": "EUR"}, {"region": "EU"}
        )
    assert res.state == TaskState.TASK_STATE_COMPLETED and "[FX desk]" in res.text
    assert route_of(res)["cost"] == 2


async def test_policy_and_missing_routes_are_agp_errors() -> None:
    async with await agp_client(ROOT) as c:
        with pytest.raises(AgpPolicyViolationError):
            await send_intent(c, "finance.compliance.check", {"customer": "X"}, {"region": "US"})
        with pytest.raises(AgpRouteNotFoundError):
            await send_intent(c, "hr.onboard", {})


async def test_pii_screen_with_the_decision_model() -> None:
    async with await agp_client(ROOT) as c:
        with pytest.raises(AgpPolicyViolationError):
            await send_intent(
                c, "finance.fx.quote", {"passport": "X1234567", "amount": 1}, {"pii": False}
            )


async def test_a_plain_client_is_routed_without_agp() -> None:
    async with await connect(ROOT, streaming=False) as c:
        res = await send_text(c, "What's the visa situation for a US citizen visiting France?")
    assert res.state == TaskState.TASK_STATE_COMPLETED and "visa" in res.text.lower()
