"""Every Lab 10 card builds, validates and says what the lab says it does."""

from __future__ import annotations

import pytest
from a2a_labs_common.a2a.cards import validate_card
from a2a_labs_common.a2a.extensions import declared, required_uris
from lab10_secure import cards
from lab10_secure.data import BUDGET_EXT, TRACE_EXT

ALL = {
    "secure": cards.secure_card,
    "extended": cards.secure_extended_card,
    "adk": lambda: cards.framework_card("lab10-adk-secure", "ADK Calendar Helper", "ADK"),
    "strands": lambda: cards.framework_card("lab10-strands-secure", "S", "Strands"),
    "ext": cards.ext_card,
    "required": cards.required_ext_card,
}


@pytest.mark.parametrize("name", ALL)
def test_every_card_validates(name: str) -> None:
    assert validate_card(ALL[name]()) == []


def test_the_secure_card_declares_both_schemes_with_or_semantics() -> None:
    card = cards.secure_card()
    assert set(card.security_schemes) == {"api_key", "oauth2", "user_consent"}
    # two requirements = OR: either an API key or an OAuth2 token with agent:invoke
    assert [list(r.schemes) for r in card.security_requirements] == [["api_key"], ["oauth2"]]
    oauth = card.security_schemes["oauth2"].oauth2_security_scheme
    assert oauth.flows.client_credentials.token_url.endswith("/token")
    consent = card.security_schemes["user_consent"].oauth2_security_scheme
    assert "calendar:read" in consent.flows.device_code.scopes  # a oneof: a scheme per flow
    key = card.security_schemes["api_key"].api_key_security_scheme
    assert (key.location, key.name) == ("header", "X-API-Key")  # never in the payload


def test_skills_carry_their_own_requirements() -> None:
    by_id = {s.id: s for s in cards.secure_card().skills}
    private = by_id["calendar.read_private"].security_requirements[0]
    assert list(private.schemes["user_consent"].list) == ["calendar:read"]
    assert len(by_id["calendar.read_public"].security_requirements) == 2


def test_the_extended_card_adds_skills_and_admin_is_scope_dependent() -> None:
    public = {s.id for s in cards.secure_card().skills}
    basic = {s.id for s in cards.secure_extended_card().skills}
    admin = {s.id for s in cards.secure_extended_card(admin=True).skills}
    assert public < basic < admin and admin - basic == {"calendar.admin_stats"}
    assert cards.secure_card().capabilities.extended_agent_card


def test_extension_declarations() -> None:
    assert declared(cards.ext_card())[TRACE_EXT].required is False
    assert required_uris(cards.required_ext_card()) == {BUDGET_EXT}
    assert declared(cards.required_ext_card())[BUDGET_EXT].params["currency"] == "USD"


def test_the_ext_card_has_two_json_rpc_interfaces() -> None:
    ifaces = cards.ext_card().supported_interfaces
    assert [(i.protocol_binding, i.protocol_version) for i in ifaces] == [
        ("JSONRPC", "1.0"),
        ("JSONRPC", "0.3"),
    ]
