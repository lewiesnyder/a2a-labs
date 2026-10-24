"""Fixed data and constants shared by the Lab 10 servers, tests and notebook."""

from __future__ import annotations

TRACE_EXT = "https://a2a-labs.dev/ext/trace-summary/v1"
BUDGET_EXT = "https://a2a-labs.dev/ext/cost-budget/v1"

PUBLIC_CALENDAR = [
    ("Mon 10:00", "Team standup"),
    ("Wed 14:00", "Sprint review"),
    ("Fri 16:00", "Demo day"),
]
PRIVATE_CALENDARS = {
    "alice": [("Tue 09:00", "Dentist"), ("Thu 18:30", "Dinner with Sam")],
}
ADMIN_STATS = {"calendars": 42, "events": 1310, "busiest_day": "Wednesday"}

# Prices per call for the cost-budget extension (USD).
UNIT_COST = {"summary": 0.01, "report": 0.08}

API_KEYS = {"lab10-dev-key": ("demo-api-key", ["agent:invoke"])}  # dev only


def render(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"- {when}: {what}" for when, what in rows)
