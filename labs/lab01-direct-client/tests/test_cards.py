"""Cards for all four servers are valid and declare what the lab says they do."""

from __future__ import annotations

import pytest
from a2a_labs_common.a2a.cards import validate_card

SERVERS = ["sdk-echo", "adk", "strands", "langgraph"]


@pytest.mark.parametrize("name", SERVERS)
async def test_card_is_valid_and_streams(server, name) -> None:  # type: ignore[no-untyped-def]
    async with server(name) as c:
        assert validate_card(c.card) == []
        assert c.card.capabilities.streaming
        assert c.card.supported_interfaces[0].protocol_binding == "JSONRPC"


async def test_hand_built_cards_advertise_the_trip_tip_skill(server) -> None:  # type: ignore[no-untyped-def]
    for name in ("strands", "langgraph"):
        async with server(name) as c:
            assert [s.id for s in c.card.skills] == ["trip_tip"]
