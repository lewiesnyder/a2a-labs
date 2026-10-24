"""AGP data structures: policy matching, the table (cost, TTL, failover) and the error codes."""

from __future__ import annotations

import pytest
from a2a.helpers import new_data_part, new_message
from a2a_labs_common.agp import (
    AGP_ERRORS,
    AgpPolicyViolationError,
    AgpRouteNotFoundError,
    AgpTable,
    AgpTableStaleError,
    CapabilityAnnouncement,
    Intent,
    announce_data,
    extract_agp,
    intent_data,
    policy_problems,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def ann(cap: str = "x.y", cost: float | None = 1, **policy: object) -> CapabilityAnnouncement:
    return CapabilityAnnouncement(capability=cap, version="1.0", cost=cost, policy=policy)


def test_policy_matching_rules() -> None:
    assert policy_problems({"region": ["US", "EU"]}, {"region": "EU"}) == []
    assert policy_problems({"region": ["*"]}, {"region": "APAC"}) == []
    assert policy_problems({"region": ["EU"]}, {"region": "US"})
    assert policy_problems({"pii": False}, {"pii": True})  # PII payload, route cannot take it
    assert policy_problems({"pii": True}, {"pii": True}) == []
    assert (
        policy_problems({"pii": False}, {"pii": False}) == []
    )  # asserting "no PII" constrains nothing
    assert (
        policy_problems({"security_level": 5}, {"security_level": 3}) == []
    )  # >= as in the sample
    assert policy_problems({"security_level": 3}, {"security_level": 5})
    assert policy_problems({"tier": "gold"}, {"tier": "gold"}) == []
    assert policy_problems({"tier": "gold"}, {"tier": "silver"})
    assert policy_problems({}, {"tier": "gold"})  # the route declares no such policy
    assert policy_problems({}, {"_hint": 1, "pii_detected": True}) == []


def test_lookup_prefers_the_lowest_cost_and_explains_the_losers() -> None:
    t = AgpTable(clock=Clock())
    t.upsert(ann(cost=5, region=["*"]), ["b"], "http://b")
    t.upsert(ann(cost=2, region=["EU"]), ["a"], "http://a")
    lk = t.lookup("x.y", {"region": "EU"})
    assert lk.best.path == ["a"] and lk.rejected[0]["why"] == ["higher cost"]
    assert t.lookup("x.y", {"region": "US"}).best.path == ["b"]  # the cheap one is not compliant


def test_the_three_error_codes() -> None:
    clock = Clock()
    t = AgpTable(ttl_s=10, clock=clock)
    with pytest.raises(AgpRouteNotFoundError):
        t.lookup("nope")
    t.upsert(ann(region=["EU"]), ["a"], "http://a")
    with pytest.raises(AgpPolicyViolationError, match="AGP_POLICY_VIOLATION"):
        t.lookup("x.y", {"region": "US"})
    clock.now += 11
    with pytest.raises(AgpTableStaleError, match="older than 10s"):
        t.lookup("x.y")
    assert {c for c, _ in AGP_ERRORS.values()} == {-32200, -32201, -32202}


def test_reannouncing_refreshes_and_expiry_falls_back_to_a_fresh_alternative() -> None:
    clock = Clock()
    t = AgpTable(ttl_s=10, clock=clock)
    t.upsert(ann(cost=1), ["cheap"], "http://c")
    t.upsert(ann(cost=4), ["dear"], "http://d")
    clock.now += 8
    t.upsert(ann(cost=4), ["dear"], "http://d")  # refreshed: same key, new timestamp
    clock.now += 5  # cheap is 13s old (stale), dear 5s
    assert t.lookup("x.y").best.path == ["dear"]
    assert [r.path for r in t.fresh_routes()] == [["dear"]]
    assert len(t.routes["x.y"]) == 2


def test_failed_routes_are_skipped_until_they_announce_again() -> None:
    clock = Clock()
    t = AgpTable(ttl_s=10, clock=clock)
    cheap = t.upsert(ann(cost=1), ["cheap"], "http://c")
    t.upsert(ann(cost=4), ["dear"], "http://d")
    t.mark_failed(cheap)
    assert t.lookup("x.y").best.path == ["dear"]
    t.upsert(ann(cost=1), ["cheap"], "http://c")  # it announced again: forgiven
    assert t.lookup("x.y").best.path == ["cheap"]
    t.mark_failed(t.routes["x.y"][0])
    t.mark_failed(t.routes["x.y"][1])
    with pytest.raises(AgpTableStaleError, match="unreachable"):
        t.lookup("x.y")


def test_withdraw_and_purge() -> None:
    clock = Clock()
    t = AgpTable(ttl_s=1, clock=clock)
    t.upsert(ann("a.b"), ["s1"], "u")
    t.upsert(ann("c.d"), ["s2"], "u")
    assert t.withdraw("s1") == 1 and "a.b" not in t.routes
    clock.now += 100
    assert t.purge() == 1 and t.routes == {}


def test_snapshot_reports_age_and_staleness() -> None:
    clock = Clock()
    t = AgpTable(ttl_s=10, clock=clock)
    t.upsert(ann(), ["s"], "http://s", is_gateway=True)
    clock.now += 12
    row = t.snapshot()["capabilities"]["x.y"][0]
    assert row["age_s"] == 12 and row["stale"] and row["is_gateway"]


def test_message_helpers_round_trip() -> None:
    a = ann(cost=2, region=["EU"])
    data = announce_data([a], ["sq"], "http://sq", gateway=False)["agp"]
    assert data["type"] == "announce" and data["announcements"][0]["cost"] == 2
    intent = Intent(target_capability="x.y", payload={"k": 1}, policy_constraints={"region": "EU"})
    msg = new_message([new_data_part(intent_data(intent, pii_detected=True))])
    got = extract_agp(msg)
    assert got["intent"]["target_capability"] == "x.y" and got["pii_detected"] is True
    assert extract_agp(new_message([new_data_part({"other": 1})])) is None
