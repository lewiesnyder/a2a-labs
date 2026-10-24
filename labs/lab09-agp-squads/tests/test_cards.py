"""Gateways declare the AGP extension (spec §4); squads are plain agents."""

from __future__ import annotations

import pytest
from a2a_labs_common.a2a.cards import validate_card
from a2a_labs_common.a2a.extensions import declared
from a2a_labs_common.agp import AGP_URI
from lab09_agp.cards import gateway_card, squad_card
from lab09_agp.squads import SQUADS


@pytest.mark.parametrize(
    "service", ["lab09-root-gateway", "lab09-finance-gateway", "lab09-travel-gateway"]
)
def test_gateway_cards(service: str) -> None:
    card = gateway_card(service)
    assert validate_card(card) == []
    ext = declared(card)[AGP_URI]
    assert ext.params["agent_role"] == "gateway" and not ext.required


@pytest.mark.parametrize("service", list(SQUADS))
def test_squad_cards_have_one_skill_per_capability_and_no_agp(service: str) -> None:
    card = squad_card(service)
    assert validate_card(card) == []
    assert [s.id for s in card.skills] == [a.capability for a in SQUADS[service].announcements]
    assert AGP_URI not in declared(card)


def test_the_announcements_match_the_spec_table() -> None:
    flat = {
        (a.capability, s.service): (a.cost, a.policy)
        for s in SQUADS.values()
        for a in s.announcements
    }
    assert flat[("finance.fx.quote", "lab09-squad-fx")] == (
        1,
        {"region": ["US", "EU", "APAC"], "pii": False},
    )
    assert flat[("finance.fx.quote", "lab09-squad-hotels")][0] == 4  # the worse alternative route
    assert flat[("finance.compliance.check", "lab09-squad-compliance")][1]["region"] == ["EU"]
    assert len(flat) == 7
