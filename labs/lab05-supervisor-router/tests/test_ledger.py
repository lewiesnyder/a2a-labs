"""Ledger invariants (spec lab05 §7): the properties that make per-remote tracking safe."""

from __future__ import annotations

import pytest
from lab05_supervisor.ledger import UNREACHABLE, Ledger


def test_one_entry_per_remote_agent_per_context() -> None:
    led = Ledger()
    led.record("ctx", "Currency", "svc-c", "c1", "t1", "WORKING")
    led.record("ctx", "Currency", "svc-c", "c1", "t1", "COMPLETED")
    led.record("ctx", "Weather", "svc-w", "c2", "t2", "COMPLETED")
    assert sorted(led.entries("ctx")) == ["Currency", "Weather"]
    assert led.entries("other") == {}


def test_input_required_keeps_the_task_active_and_a_terminal_state_clears_it() -> None:
    led = Ledger()
    e = led.record("ctx", "Currency", "svc", "c1", "t1", "INPUT_REQUIRED")
    assert (e["active_task_id"], e["last_state"]) == ("t1", "INPUT_REQUIRED")
    # the continuation reports the SAME task id: still one task, now completed and inactive
    e = led.record("ctx", "Currency", "svc", "c1", "t1", "COMPLETED")
    assert e["active_task_id"] is None and e["task_ids"] == ["t1"]


def test_a_new_task_after_a_terminal_state_keeps_the_context_and_the_history() -> None:
    led = Ledger()
    led.record("ctx", "Currency", "svc", "c1", "t1", "COMPLETED")
    e = led.record("ctx", "Currency", "svc", "c1", "t2", "COMPLETED")
    assert e["remote_context_id"] == "c1" and e["task_ids"] == ["t1", "t2"]


def test_a_task_id_is_never_recorded_for_a_different_agent() -> None:
    led = Ledger()
    led.record("ctx", "Currency", "svc-c", "c1", "t1", "INPUT_REQUIRED")
    with pytest.raises(ValueError, match="belongs to 'Currency'"):
        led.record("ctx", "Weather", "svc-w", "c2", "t1", "WORKING")


def test_unreachable_is_recorded_without_a_task() -> None:
    led = Ledger()
    e = led.unreachable("ctx", "Weather", "svc-w", "c2")
    assert e["last_state"] == UNREACHABLE and e["active_task_id"] is None and e["task_ids"] == []


def test_seed_restores_state_but_never_overwrites_a_live_entry() -> None:
    led = Ledger()
    saved = Ledger().record("ctx", "Currency", "svc", "c1", "t1", "COMPLETED")
    led.seed("ctx", saved)
    assert led.entry("ctx", "Currency") == saved
    live = led.record("ctx", "Currency", "svc", "c1", "t2", "WORKING")
    led.seed("ctx", saved)
    assert led.entry("ctx", "Currency") is live
