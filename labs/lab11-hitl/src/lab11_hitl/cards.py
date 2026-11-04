"""Agent Cards for the Lab 11 services."""

from __future__ import annotations

from a2a.types import AgentCard
from a2a_labs_common.a2a.cards import build_card, skill

CARDS = {
    "lab11-flights": (
        "Flights",
        "Books flights. Asks which seat you want.",
        "flights.book",
        "Book a flight to Lisbon",
    ),
    "lab11-hotels": (
        "Hotels",
        "Books hotels (LangGraph). Asks which hotel.",
        "hotels.book",
        "Book a hotel for 4 nights",
    ),
    "lab11-docs": (
        "Documents",
        "Checks travel documents. Needs your consent to read the passport.",
        "docs.check",
        "Check my travel documents",
    ),
    "lab11-payments": (
        "Payments",
        "Pays. A risky total needs a person's approval.",
        "payments.pay",
        "Pay for the trip",
    ),
}


def delegate_card(service: str) -> AgentCard:
    name, desc, sid, example = CARDS[service]
    return build_card(
        service=service,
        name=name,
        description=desc,
        streaming=False,
        skills=[skill(sid, name, desc, ["hitl", "delegate"], [example])],
    )


def orchestrator_card() -> AgentCard:
    return build_card(
        service="lab11-orchestrator",
        name="Trip Desk",
        description=(
            "Books a trip through flights, hotels, documents and payments. When a delegate "
            "needs a person, this task pauses and lists every open question."
        ),
        streaming=False,
        push_notifications=True,
        skills=[
            skill(
                "trip.book",
                "Book a trip",
                "Plans and books a trip; relays questions from the delegates to the human.",
                ["hitl", "orchestrator"],
                ["Book a trip to Lisbon for 4 nights, check my documents"],
            )
        ],
    )
