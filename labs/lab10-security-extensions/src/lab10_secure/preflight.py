"""The pre-flight screen: a Nimble ``choice`` decides allow / needs_auth / reject.

A2A: REJECTED is a POLICY decision made by the agent ("I will not do this"), unlike FAILED (the
agent tried and broke) and CANCELED (the client asked to stop). A decision model suits it: it
returns calibrated probabilities over a closed set, so the refusal carries evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from a2a_labs_common.models import get_decision_model
from a2a_labs_common.models.decision import DecisionModel, Question

OPTIONS = {
    "allow": "an ordinary read-only question about the shared team calendar",
    "needs_auth": "asks for someone's private appointments, which needs the user's consent",
    "reject": "asks to delete, wipe or destroy calendars or events, which this agent never does",
}


@dataclass
class Verdict:
    choice: str
    probabilities: dict[str, float]
    backend: str


async def screen(text: str, decision: DecisionModel | None = None) -> Verdict:
    model = decision or get_decision_model()
    result = await model.decide(
        f"Request to a calendar assistant: {text}",
        {
            "preflight": Question(
                type="choice",
                instructions="Which kind of request is this?",
                criteria=OPTIONS,
            )
        },
    )
    ans = result.answers["preflight"]
    probs = ans.probabilities if isinstance(ans.probabilities, dict) else {}
    return Verdict(ans.choice or "reject", {k: float(v) for k, v in probs.items()}, result.backend)
