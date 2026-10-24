"""Deterministic data behind the specialists' tools. Opaque to every controller.

Implements: spec lab02 §2. Static tables keep every run reproducible and make tool calls visible
in traces without any network dependency.
"""

from __future__ import annotations

RATE_DATE = "2026-10-01"
# Units of each currency per 1 USD.
PER_USD: dict[str, float] = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "JPY": 155.0,
    "CHF": 0.88,
    "CAD": 1.37,
    "AUD": 1.52,
}

FORECASTS: dict[str, list[str]] = {
    "tokyo": ["18C partly cloudy", "20C sunny", "17C light rain"],
    "lisbon": ["24C sunny", "25C sunny", "22C breezy"],
    "paris": ["14C overcast", "13C light rain", "15C cloudy"],
    "london": ["12C drizzle", "11C rain", "13C cloudy"],
}

FACTS: dict[str, str] = {
    "tokyo": "Tokyo is the most populous metropolitan area on Earth, with about 37 million people.",
    "lisbon": "Lisbon is one of the oldest cities in Western Europe, older than Rome.",
    "paris": "Paris has more than 400 parks and gardens inside its city limits.",
    "london": "London has been the capital for nearly a thousand years and has over 170 museums.",
}


def get_exchange_rate(from_ccy: str, to_ccy: str, date: str | None = None) -> float:
    """Cross rate from the static table; the date is accepted but the table is fixed."""
    try:
        return round(PER_USD[to_ccy.upper()] / PER_USD[from_ccy.upper()], 6)
    except KeyError as exc:
        raise ValueError(f"unknown currency: {exc.args[0]}") from exc


def get_forecast(city: str, days: int = 3) -> str:
    key = city.strip().lower()
    days = max(1, min(int(days), 3))
    if key not in FORECASTS:
        return f"No forecast for {city} in the demo table."
    lines = [f"day {i + 1}: {d}" for i, d in enumerate(FORECASTS[key][:days])]
    return f"{city.title()} forecast - " + "; ".join(lines)


def lookup_fact(city: str) -> str:
    return FACTS.get(city.strip().lower(), f"No fact about {city} in the demo table.")
