"""Trip request parsing and the deterministic flight and hotel data.

Implements: spec lab04 §2.1. Parsing is plain regex so a missing field triggers an ``interrupt()``
the same way every time, with no small model involved (DEV-29). The tables make totals exact,
so the over-budget / approve / cheaper-hotel story is reproducible.
"""

from __future__ import annotations

import math
import re

from pydantic import BaseModel

CITIES = ["New York", "Chicago", "Lisbon", "Paris", "Tokyo", "London"]
_CITY = "|".join(re.escape(c) for c in CITIES)

# Price per traveller for a flight between two cities (symmetric); everything else: a formula.
FLIGHTS_PP: dict[frozenset[str], int] = {
    frozenset({"chicago", "lisbon"}): 780,
    frozenset({"chicago", "paris"}): 720,
    frozenset({"chicago", "tokyo"}): 1150,
    frozenset({"chicago", "london"}): 690,
    frozenset({"new york", "lisbon"}): 560,
    frozenset({"new york", "paris"}): 540,
}
# (standard, budget) hotel price per night, per room
HOTELS: dict[str, tuple[int, int]] = {
    "lisbon": (160, 95),
    "paris": (240, 130),
    "tokyo": (220, 120),
    "london": (260, 140),
    "chicago": (200, 110),
    "new york": (280, 150),
}


class TripRequest(BaseModel):
    origin: str | None = None
    destination: str | None = None
    dates: str | None = None
    nights: int | None = None
    budget: int | None = None
    travelers: int | None = None

    def missing(self) -> list[str]:
        return [
            f
            for f in ("origin", "destination", "dates", "budget", "travelers")
            if getattr(self, f) is None
        ]


def parse_trip_text(text: str, existing: TripRequest | None = None) -> TripRequest:
    """Merge whatever ``text`` states into ``existing`` (a reply may fill only some fields)."""
    req = (existing or TripRequest()).model_copy()
    found = [m.group(0) for m in re.finditer(_CITY, text)]
    if m := re.search(rf"from\s+({_CITY})", text):
        req.origin = m.group(1)
    if m := re.search(rf"\bto\s+({_CITY})", text):
        req.destination = m.group(1)
    for city in found:  # a bare city in a reply fills the first field still empty
        if city not in (req.origin, req.destination):
            if req.origin is None and req.destination is not None:
                req.origin = city
            elif req.destination is None:
                req.destination = city
    if m := re.search(r"(\d{1,2})\s*[\u2013-]\s*(\d{1,2})\s+([A-Za-z]+)", text):
        start, end, month = int(m.group(1)), int(m.group(2)), m.group(3)
        req.dates, req.nights = f"{start}-{end} {month}", max(end - start, 1)
    if m := re.search(r"\$\s?([\d,]+)", text) or re.search(r"budget\s+([\d,]+)", text):
        req.budget = int(m.group(1).replace(",", ""))
    if m := re.search(r"(\d+)\s+(?:travell?ers|people|adults|passengers)", text):
        req.travelers = int(m.group(1))
    return req


def flight_per_person(origin: str, destination: str) -> int:
    key = frozenset({origin.lower(), destination.lower()})
    return FLIGHTS_PP.get(key, (len(origin) + len(destination)) * 55)


def find_flights(req: TripRequest) -> list[dict[str, object]]:
    pp = flight_per_person(req.origin or "", req.destination or "")
    return [
        {"id": "FL-1", "airline": "Atlantic Air", "price_per_person": pp},
        {"id": "FL-2", "airline": "Skyline", "price_per_person": pp + 90},
    ]


def find_hotels(req: TripRequest, *, cheaper: bool = False) -> list[dict[str, object]]:
    standard, budget = HOTELS.get((req.destination or "").lower(), (180, 100))
    rooms = math.ceil((req.travelers or 1) / 2)
    price = budget if cheaper else standard
    name = "City Hostel Plus" if cheaper else "Grand Central Hotel"
    return [{"id": "HT-1", "name": name, "price_per_night": price, "rooms": rooms}]


def plan_total(
    req: TripRequest, flights: list[dict[str, object]], hotels: list[dict[str, object]]
) -> int:
    flight = int(flights[0]["price_per_person"]) * (req.travelers or 1)  # type: ignore[call-overload]
    hotel = int(hotels[0]["price_per_night"]) * int(hotels[0]["rooms"]) * (req.nights or 1)  # type: ignore[call-overload]
    return flight + hotel
