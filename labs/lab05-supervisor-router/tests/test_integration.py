"""Integration tests against the running ``lab05`` profile (``make e2e LAB=lab05``).

Exact routing is asserted in fake mode (``LLM_PROVIDER=fake DECISION_PROVIDER=fake``), where the
decision script in ``config/fake_responses.yaml`` makes every choice deterministic. With real
models only protocol-level outcomes are asserted, because Nimble and the LLMs decide.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pytest
from a2a.types import TaskState
from a2a_labs_common.a2a.client import connect, send_text, wait_for_cards
from a2a_labs_common.config import host_url
from lab05_supervisor.prompts import ROUTING_TABLE

pytestmark = pytest.mark.compose
FAKE = os.environ.get("DECISION_PROVIDER") == "fake"
SUPERVISORS = ["lab05-supervisor", "lab05-adk-coordinator", "lab05-strands-graph-supervisor"]
SPECIALISTS = ["lab05-currency", "lab05-weather", "lab05-facts"]


async def get_json(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as http:
        return (await http.get(url)).json()  # type: ignore[no-any-return]


@pytest.fixture(scope="module", autouse=True)
async def _services_up() -> None:
    await wait_for_cards([*SPECIALISTS, *SUPERVISORS], timeout=180)


@pytest.mark.parametrize("supervisor", SUPERVISORS)
@pytest.mark.parametrize("case", ROUTING_TABLE, ids=lambda c: c.prompt[:24])
async def test_the_routing_table_completes_on_every_supervisor(supervisor: str, case) -> None:  # type: ignore[no-untyped-def]
    async with await connect(supervisor, streaming=False) as c:
        res = await send_text(c, case.prompt, context_id=str(uuid.uuid4()))
        assert res.state == TaskState.TASK_STATE_COMPLETED and res.text


@pytest.mark.skipif(not FAKE, reason="exact routing needs the scripted decision model")
@pytest.mark.parametrize("case", ROUTING_TABLE, ids=lambda c: c.prompt[:24])
async def test_the_langgraph_supervisor_routes_and_tracks_each_remote(case) -> None:  # type: ignore[no-untyped-def]
    ctx = str(uuid.uuid4())
    async with await connect("lab05-supervisor", streaming=False) as c:
        await send_text(c, case.prompt, context_id=ctx)
    values = (await get_json(f"{host_url('lab05-supervisor')}/debug/state/{ctx}"))["values"]
    assert set(values.get("ledger", {})) == set(case.langgraph)
    for entry in values.get("ledger", {}).values():
        assert entry["last_state"] == "COMPLETED" and entry["task_ids"]


@pytest.mark.skipif(not FAKE, reason="exact routing needs the scripted decision model")
async def test_the_adk_coordinator_stamps_the_remote_ids_on_its_session() -> None:
    ctx = str(uuid.uuid4())
    async with await connect("lab05-adk-coordinator", streaming=False) as c:
        await send_text(c, ROUTING_TABLE[0].prompt, context_id=ctx)
    implicit = await get_json(f"{host_url('lab05-adk-coordinator')}/debug/ledger/{ctx}")
    assert len(implicit) == 1 and next(iter(implicit.values()))["task_ids"]
