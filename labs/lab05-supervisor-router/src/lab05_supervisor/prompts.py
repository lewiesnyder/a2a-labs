"""The six-prompt routing table used by the tests and the notebook's router comparison.

``expected`` is what each supervisor SHOULD route to. The ADK and Strands supervisors pick ONE
specialist per request, so for the multi-intent prompt they are expected to take only the first
intent; that gap is part of what the comparison teaches.
"""

from __future__ import annotations

from dataclasses import dataclass

CURRENCY, WEATHER, FACTS = "Currency Converter", "Weather Reporter", "City Facts"


@dataclass(frozen=True)
class RoutingCase:
    prompt: str
    langgraph: frozenset[str]  # agents the fan-out supervisor delegates to ({} = answers itself)
    single_route: frozenset[str]  # agents a one-specialist-per-request supervisor delegates to

    def accepts_single(self, chosen: set[str]) -> bool:
        """A one-specialist supervisor passes if it took the first intent. For the multi-intent
        prompt a real decision model may also answer `none` (no ONE specialist fits), which makes
        the supervisor answer directly; that is the same loss, so it is accepted too."""
        return chosen == set(self.single_route) or (
            self.single_route != self.langgraph and not chosen
        )


def _c(prompt: str, lg: set[str], single: set[str] | None = None) -> RoutingCase:
    return RoutingCase(prompt, frozenset(lg), frozenset(lg if single is None else single))


ROUTING_TABLE: list[RoutingCase] = [
    _c("Convert 300 CAD to EUR", {CURRENCY}),
    _c("What is the weather in Tokyo?", {WEATHER}),
    _c("Tell me some facts about Lisbon.", {FACTS}),
    _c("How much is 250 GBP in JPY?", {CURRENCY}),
    _c(
        "What is the weather in Tokyo and convert 50 EUR to USD, plus one fun fact about Tokyo.",
        {WEATHER, CURRENCY, FACTS},
        {WEATHER},
    ),
    _c("Tell me a joke about routers.", set()),
]
