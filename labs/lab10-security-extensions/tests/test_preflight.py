"""The pre-flight screen: scripted in fake mode, calibrated on a real Nimble in the notebook."""

from __future__ import annotations

import pytest
from a2a_labs_common.models.decision import FakeDecisionModel
from lab10_secure.preflight import OPTIONS, screen


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What's on the public team calendar?", "allow"),
        ("Show my private appointments", "needs_auth"),
        ("Delete all calendars", "reject"),
        ("Wipe every event", "reject"),
    ],
)
async def test_the_scripted_screen(text: str, expected: str) -> None:
    verdict = await screen(text)  # DECISION_PROVIDER=fake: reads fake_responses.yaml
    assert verdict.choice == expected and verdict.backend == "fake"
    assert set(verdict.probabilities) == set(OPTIONS) and verdict.probabilities[expected] == 1.0


async def test_an_injected_model_wins() -> None:
    verdict = await screen("hello", FakeDecisionModel(answers={"preflight": "reject"}))
    assert verdict.choice == "reject"
