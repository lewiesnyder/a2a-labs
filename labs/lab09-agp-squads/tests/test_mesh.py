"""The whole mesh in fake mode: convergence, routing, policy, PII, failover, staleness, fallback."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from a2a.types import GetTaskRequest, TaskState
from a2a.utils.errors import InvalidParamsError
from a2a_labs_common.a2a.client import connect, send_text
from a2a_labs_common.agp import (
    AGP_URI,
    AgpPolicyViolationError,
    AgpRouteNotFoundError,
    AgpTableStaleError,
)
from lab09_agp.clients import agp_client, card_json, control, route_of, send_intent, table

ROOT, FIN, TRAVEL = "lab09-root-gateway", "lab09-finance-gateway", "lab09-travel-gateway"
FX = {"amount": 100, "from": "USD", "to": "EUR"}


async def test_the_root_converges_on_all_seven_routes_with_cost_plus_one(mesh) -> None:
    caps = table(ROOT)["capabilities"]
    assert sum(len(v) for v in caps.values()) == 7 and len(caps) == 6
    fx = sorted(caps["finance.fx.quote"], key=lambda r: r["cost"])
    # cost + 1 per gateway hop: fx squad (1) via finance; hotels' alternative (4) via travel
    assert [(r["cost"], r["path"]) for r in fx] == [
        (2, ["lab09-finance-gateway", "lab09-squad-fx"]),
        (5, ["lab09-travel-gateway", "lab09-squad-hotels"]),
    ]
    assert all(r["is_gateway"] for v in caps.values() for r in v)
    domain = table(FIN)["capabilities"]["finance.fx.quote"][0]
    assert domain["cost"] == 1 and not domain["is_gateway"]


async def test_the_lowest_cost_compliant_route_wins_and_the_task_is_mirrored(mesh) -> None:
    async with await agp_client(ROOT) as c:
        res = await send_intent(c, "finance.fx.quote", FX, {"region": "EU"})
    assert (
        res.state == TaskState.TASK_STATE_COMPLETED and "[FX desk] 100 USD = 92.00 EUR" in res.text
    )
    route = route_of(res)
    assert route["path"] == ["lab09-finance-gateway", "lab09-squad-fx"] and route["cost"] == 2
    assert [a["path"][0] for a in route["alternatives"]] == ["lab09-travel-gateway"]  # lost on cost
    assert route["downstream_task_id"] and route["downstream_task_id"] != res.task_id


async def test_each_hop_owns_its_own_task(mesh) -> None:
    async with await agp_client(ROOT) as c:
        res = await send_intent(c, "travel.flights.search", {"from": "Paris", "to": "Rome"})
    ids = [res.task_id, route_of(res)["downstream_task_id"]]
    async with await connect(mesh.url(TRAVEL), streaming=False) as t:
        travel = await t.client.get_task(GetTaskRequest(id=ids[1]))
    squad_id = route_of_task(travel)["downstream_task_id"]
    async with await connect(mesh.url("lab09-squad-flights"), streaming=False) as s:
        squad = await s.client.get_task(GetTaskRequest(id=squad_id))
    ids.append(squad.id)
    assert len(set(ids)) == 3 and squad.status.state == TaskState.TASK_STATE_COMPLETED
    assert "AF1204" in res.text


def route_of_task(task) -> dict:
    from a2a.helpers import get_data_parts

    art = next(a for a in task.artifacts if a.artifact_id == "agp-route")
    return get_data_parts(art.parts)[0]


async def test_every_squad_framework_answers_through_the_mesh(mesh) -> None:
    cases = {
        "finance.payments.initiate": ({"amount": 50, "to": "ACME"}, "PAY-1042"),  # Strands
        "finance.compliance.check": ({"customer": "ACME"}, "CMP-77"),  # ADK
        "travel.hotels.search": ({"city": "Rome"}, "Hotel Aurora"),  # Strands
        "travel.visa.requirements": (
            {"nationality": "US", "destination": "France"},
            "no visa",
        ),  # LangGraph
    }
    async with await agp_client(ROOT) as c:
        for cap, (payload, expect) in cases.items():
            res = await send_intent(c, cap, payload)
            assert res.state == TaskState.TASK_STATE_COMPLETED and expect in res.text, cap


async def test_policy_violation_when_no_route_accepts_the_region(mesh) -> None:
    async with await agp_client(ROOT) as c:
        with pytest.raises(AgpPolicyViolationError) as exc:
            await send_intent(c, "finance.compliance.check", {"customer": "X"}, {"region": "US"})
    assert "AGP_POLICY_VIOLATION" in str(exc.value)


async def test_route_not_found_for_a_capability_nobody_announced(mesh) -> None:
    async with await agp_client(ROOT) as c:
        with pytest.raises(AgpRouteNotFoundError):
            await send_intent(c, "hr.onboard.new_hire", {})


def raw_intent(url: str, capability: str, constraints: dict | None = None) -> httpx.Response:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [
                    {"text": "AGP intent"},
                    {
                        "data": {
                            "agp": {
                                "type": "intent",
                                "intent": {
                                    "target_capability": capability,
                                    "payload": {},
                                    "policy_constraints": constraints,
                                },
                            }
                        }
                    },
                ],
            }
        },
    }
    return httpx.post(url, json=body, headers={"A2A-Version": "1.0", "A2A-Extensions": AGP_URI})


async def test_agp_errors_are_json_rpc_errors_with_the_agp_codes(mesh) -> None:
    url = mesh.url(ROOT)
    err = raw_intent(url, "hr.nothing").json()["error"]
    assert err["code"] == -32200 and err["data"][0]["reason"] == "AGP_ROUTE_NOT_FOUND"
    err = raw_intent(url, "finance.compliance.check", {"region": "US"}).json()["error"]
    assert err["code"] == -32201 and "compliance" in err["data"][0]["metadata"]["rejected"]


async def test_pii_in_the_payload_is_screened_by_the_decision_model(mesh) -> None:
    passport = {"name": "Ada", "passport": "X1234567", "amount": 10, "from": "USD", "to": "EUR"}
    async with await agp_client(ROOT) as c:
        with pytest.raises(
            AgpPolicyViolationError, match="PII"
        ):  # asserted pii=false, but it has PII
            await send_intent(c, "finance.fx.quote", passport, {"pii": False})
        with pytest.raises(
            AgpPolicyViolationError
        ) as exc:  # not asserted: the PII-free fx squads lose
            await send_intent(c, "finance.fx.quote", passport)
        assert "does not take it" in str(exc.value) or "AGP_POLICY_VIOLATION" in str(exc.value)
        ok = await send_intent(c, "finance.payments.initiate", {"to": "Ada", "passport": "X1"})
    assert ok.state == TaskState.TASK_STATE_COMPLETED and route_of(ok)["pii_detected"] is True


async def test_failover_to_the_worse_route_then_failback_after_reannounce(mesh) -> None:
    async with await agp_client(ROOT) as c:
        before = await send_intent(c, "finance.fx.quote", FX)
        assert "[FX desk]" in before.text
        await mesh.stop("lab09-squad-fx")
        down = await send_intent(c, "finance.fx.quote", FX)  # transport failure -> next best route
        assert "[Hotels desk]" in down.text
        assert route_of(down)["path"] == ["lab09-travel-gateway", "lab09-squad-hotels"]
        assert route_of(down)["cost"] == 5
        await mesh.start_squad("lab09-squad-fx")  # same port; it re-announces on startup
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            back = await send_intent(c, "finance.fx.quote", FX)
            if "[FX desk]" in back.text:
                break
            await asyncio.sleep(0.2)
        assert "[FX desk]" in back.text


async def test_a_dead_squad_is_withdrawn_after_the_ttl_and_the_alternative_stays(mesh) -> None:
    await mesh.stop("lab09-squad-fx")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        fx = table(ROOT)["capabilities"]["finance.fx.quote"]
        if [r["stale"] for r in sorted(fx, key=lambda r: r["cost"])] == [True, False]:
            return
        await asyncio.sleep(0.2)
    raise AssertionError("the dead route never went stale")


async def test_paused_announcements_make_the_table_stale(mesh) -> None:
    assert control("lab09-squad-payments", "pause") == {"paused": True}
    deadline = time.monotonic() + 15
    async with await agp_client(ROOT) as c:
        while time.monotonic() < deadline:
            try:
                await send_intent(c, "finance.payments.initiate", {"to": "X"})
            except AgpTableStaleError as exc:
                assert "AGP_TABLE_STALE" in str(exc)
                break
            await asyncio.sleep(0.3)
        else:
            raise AssertionError("never became stale")
    control("lab09-squad-payments", "resume")
    async with await agp_client(ROOT) as c:  # it re-announces and the route is fresh again
        for _ in range(40):
            try:
                ok = await send_intent(c, "finance.payments.initiate", {"to": "X"})
                break
            except AgpTableStaleError:
                await asyncio.sleep(0.2)
    assert ok.state == TaskState.TASK_STATE_COMPLETED


async def test_a_plain_a2a_client_is_routed_by_the_decision_model(mesh) -> None:
    async with await connect(mesh.url(ROOT), streaming=False) as c:  # no extension, no agp part
        res = await send_text(c, "What's the visa situation for a US citizen visiting France?")
    assert res.state == TaskState.TASK_STATE_COMPLETED and "no visa" in res.text
    route = route_of(res)
    assert route["fallback"] is True and route["path"] == ["lab09-travel-gateway"]


async def test_fallback_reaches_a_different_domain_and_squad(mesh) -> None:
    async with await connect(mesh.url(ROOT), streaming=False) as c:
        res = await send_text(c, "Convert 100 USD to EUR")
    assert "[FX desk]" in res.text


async def test_activating_agp_without_an_agp_part_is_invalid_params(mesh) -> None:
    async with await connect(mesh.url(ROOT), streaming=False, extensions=[AGP_URI]) as c:
        with pytest.raises(InvalidParamsError):
            await send_text(c, "hello")


async def test_gateways_declare_the_extension_and_squads_do_not(mesh) -> None:
    for g in (ROOT, FIN, TRAVEL):
        card = card_json(mesh.url(g))
        (ext,) = card["capabilities"]["extensions"]
        assert ext["uri"] == AGP_URI
        assert ext["params"] == {"agent_role": "gateway", "supported_agp_versions": ["1.0"]}
    for s in ("lab09-squad-fx", "lab09-squad-visa"):
        card = card_json(mesh.url(s))
        assert not card["capabilities"].get("extensions")


async def test_the_gateway_echoes_that_it_activated_agp(mesh) -> None:
    r = raw_intent(mesh.url(ROOT), "hr.nothing")
    assert r.headers["a2a-extensions"] == AGP_URI
