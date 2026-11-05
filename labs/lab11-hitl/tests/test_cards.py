"""Every Lab 11 card builds and says what the lab says."""

from __future__ import annotations

import pytest
from a2a_labs_common.a2a.cards import validate_card
from lab11_hitl.cards import CARDS, delegate_card, orchestrator_card


@pytest.mark.parametrize("service", list(CARDS))
def test_delegate_cards(service: str) -> None:
    card = delegate_card(service)
    assert validate_card(card) == [] and not card.capabilities.push_notifications


def test_the_orchestrator_can_push_so_an_inbox_can_hear_about_questions() -> None:
    card = orchestrator_card()
    assert validate_card(card) == [] and card.capabilities.push_notifications
