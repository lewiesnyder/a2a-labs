"""Unit tests: config, ids, states, methods, cards."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from a2a.types import TaskState
from a2a_labs_common import config
from a2a_labs_common.a2a import cards, ids, methods, states


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in ("A2A_LABS_RUNTIME", "LAB00_PROTOCOL_AGENT_URL", "LAB00_PROTOCOL_AGENT_PORT"):
        monkeypatch.delenv(var, raising=False)
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()  # never leave a cached 'docker' runtime for later tests


def test_endpoint_host_docker_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.endpoint("lab00-protocol-agent") == "http://localhost:10001"
    monkeypatch.setenv("A2A_LABS_RUNTIME", "docker")
    config.settings.cache_clear()
    assert config.endpoint("lab00-protocol-agent") == "http://lab00-protocol-agent:10001"
    monkeypatch.setenv("LAB00_PROTOCOL_AGENT_URL", "http://x:1")
    assert config.endpoint("lab00-protocol-agent") == "http://x:1"
    monkeypatch.setenv("LAB00_PROTOCOL_AGENT_PORT", "1234")
    assert config.port("lab00-protocol-agent") == 1234


def test_unknown_service() -> None:
    with pytest.raises(KeyError, match=r"endpoints\.yaml"):
        config.endpoint("nope")


def test_states() -> None:
    assert states.is_terminal(TaskState.TASK_STATE_COMPLETED)
    assert states.is_interrupted(TaskState.TASK_STATE_INPUT_REQUIRED)
    assert not states.is_terminal(TaskState.TASK_STATE_WORKING)
    assert states.state_name(TaskState.TASK_STATE_WORKING) == "WORKING"


def test_ids_are_uuids_and_derivation_is_stable() -> None:
    uuid.UUID(ids.new_message_id())
    assert ids.derived_context_id("p", "a") == ids.derived_context_id("p", "a")
    assert ids.derived_context_id("p", "a") != ids.derived_context_id("p", "b")


def test_crosswalk_has_seven_requested_and_eleven_total() -> None:
    assert len(methods.METHOD_CROSSWALK) == 11
    assert sum(m.requested for m in methods.METHOD_CROSSWALK) == 7
    assert "tasks/send" in methods.LEGACY_NAMES


def test_card_builder_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A2A_LABS_RUNTIME", "docker")  # inside compose cards advertise docker DNS
    config.settings.cache_clear()
    s = cards.skill("echo", "Echo", "Echoes text back", ["demo"], ["hi"])
    card = cards.build_card(
        service="lab00-protocol-agent",
        name="Playground",
        skills=[s],
        description="A deterministic playground agent",
        bindings=("JSONRPC", "HTTP+JSON", "GRPC"),
    )
    assert cards.validate_card(card) == []
    urls = {i.protocol_binding: i.url for i in card.supported_interfaces}
    assert urls["HTTP+JSON"].endswith("/rest")
    assert urls["GRPC"] == "lab00-protocol-agent:10002"
    assert card.capabilities.streaming


def test_card_advertises_host_urls_when_running_on_the_host() -> None:
    card = cards.build_card(
        service="lab00-protocol-agent",
        name="n",
        description="d" * 25,
        skills=[cards.skill("a", "A", "d", [], [])],
        bindings=("JSONRPC", "GRPC"),
    )
    assert [i.url for i in card.supported_interfaces] == [
        "http://localhost:10001",
        "localhost:10002",
    ]


def test_validate_card_reports_problems() -> None:
    card = cards.build_card(
        service="lab00-protocol-agent",
        name="x",
        description="short",
        skills=[cards.skill("a", "A", "d", [], []), cards.skill("a", "A2", "d", [], [])],
    )
    problems = cards.validate_card(card)
    assert "duplicate skill id a" in problems
    assert any("description" in p for p in problems)
