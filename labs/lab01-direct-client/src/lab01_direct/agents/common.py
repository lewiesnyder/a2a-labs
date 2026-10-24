"""What the four Lab 01 agents share: one skill ("Trip Tip") and one deterministic tool.

Implements: spec lab01 §2. The tool returns fake data from a static dict so the tool call is
visible in traces without any network dependency.
"""

from __future__ import annotations

LOCAL_TIMES: dict[str, str] = {
    "lisbon": "Lisbon: 14:05 (WEST, UTC+1)",
    "paris": "Paris: 15:05 (CEST, UTC+2)",
    "tokyo": "Tokyo: 23:05 (JST, UTC+9)",
    "new york": "New York: 09:05 (EDT, UTC-4)",
    "sydney": "Sydney: 00:05 next day (AEDT, UTC+11)",
}

INSTRUCTION = (
    "You are Trip Tip, a concise travel assistant. When the user names a city, ALWAYS call the "
    "local_time tool first, then reply with exactly ONE practical travel tip for that city and "
    "mention the local time. For follow-up questions, answer from the conversation so far."
)
DESCRIPTION = "Gives one practical travel tip for a city, using the local time."


def local_time_data(city: str) -> str:
    """Deterministic fake clock used by every framework's ``local_time`` tool."""
    return LOCAL_TIMES.get(city.strip().lower(), f"{city}: time unknown (not in the demo table)")
