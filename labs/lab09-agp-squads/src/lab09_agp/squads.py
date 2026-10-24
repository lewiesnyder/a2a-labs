"""The six squads: what each announces and what it answers (deterministic, no LLM needed).

The squad agents do not know AGP. They are plain A2A agents; the only AGP thing about them is that
``servers/common.py`` announces their capabilities to a gateway at startup and periodically.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from a2a_labs_common.agp import CapabilityAnnouncement

FX = {("USD", "EUR"): 0.92, ("EUR", "USD"): 1.09, ("USD", "JPY"): 151.0, ("EUR", "JPY"): 164.5}


@dataclass
class Squad:
    service: str
    name: str
    framework: str
    parent: str  # the gateway it announces to
    description: str
    announcements: list[CapabilityAnnouncement] = field(default_factory=list)


def ann(cap: str, cost: float, region: list[str], pii: bool) -> CapabilityAnnouncement:
    return CapabilityAnnouncement(
        capability=cap, version="1.0", cost=cost, policy={"region": region, "pii": pii}
    )


SQUADS: dict[str, Squad] = {
    s.service: s
    for s in [
        Squad(
            "lab09-squad-payments",
            "Payments",
            "strands",
            "lab09-finance-gateway",
            "Initiates payments.",
            [ann("finance.payments.initiate", 2, ["US", "EU"], True)],
        ),
        Squad(
            "lab09-squad-fx",
            "FX",
            "langgraph",
            "lab09-finance-gateway",
            "Quotes currency conversions.",
            [ann("finance.fx.quote", 1, ["US", "EU", "APAC"], False)],
        ),
        Squad(
            "lab09-squad-compliance",
            "Compliance",
            "adk",
            "lab09-finance-gateway",
            "Runs compliance checks (EU only).",
            [ann("finance.compliance.check", 3, ["EU"], True)],
        ),
        Squad(
            "lab09-squad-flights",
            "Flights",
            "adk",
            "lab09-travel-gateway",
            "Searches flights.",
            [ann("travel.flights.search", 1, ["*"], False)],
        ),
        Squad(
            "lab09-squad-hotels",
            "Hotels",
            "strands",
            "lab09-travel-gateway",
            "Searches hotels; also quotes currency, at a worse price.",
            [
                ann("travel.hotels.search", 1, ["*"], False),
                # a deliberately worse alternative route for finance.fx.quote (the failover demo)
                ann("finance.fx.quote", 4, ["*"], False),
            ],
        ),
        Squad(
            "lab09-squad-visa",
            "Visa",
            "langgraph",
            "lab09-travel-gateway",
            "Explains visa requirements.",
            [ann("travel.visa.requirements", 2, ["EU", "US"], True)],
        ),
    ]
}


def payload_of(text: str) -> dict[str, Any]:
    """The JSON object embedded in a forwarded Intent (``<capability> {...}``), or ``{}``."""
    start = text.find("{")
    if start < 0:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _fx(text: str, desk: str = "FX desk") -> str:
    p = payload_of(text)
    amount = float(p.get("amount", 100))
    src, dst = str(p.get("from", "USD")).upper(), str(p.get("to", "EUR")).upper()
    m = re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z]{3})\s*(?:to|in|into)\s*([A-Za-z]{3})", text)
    if not p and m:
        amount, src, dst = float(m[1]), m[2].upper(), m[3].upper()
    rate = FX.get((src, dst), 1.0)
    if desk != "FX desk":
        rate = round(rate * 0.98, 4)  # the worse alternative route
    return f"[{desk}] {amount:g} {src} = {amount * rate:.2f} {dst} (rate {rate})"


def handle_payments(text: str) -> str:
    p = payload_of(text)
    return (
        f"Payment of {p.get('amount', '?')} {p.get('currency', 'USD')} to "
        f"{p.get('to', 'the payee')} initiated (ref PAY-1042)."
    )


def handle_fx(text: str) -> str:
    return _fx(text)


def handle_compliance(text: str) -> str:
    p = payload_of(text)
    return (
        f"Compliance check for {p.get('customer', 'the customer')}: clear (EU rules, ref CMP-77)."
    )


def handle_flights(text: str) -> str:
    p = payload_of(text)
    a, b = p.get("from", "Paris"), p.get("to", "Rome")
    return f"Flights {a} -> {b}: AF1204 08:10 (EUR 140), LH1836 13:45 (EUR 120)."


def handle_hotels(text: str) -> str:
    if "finance.fx.quote" in text or re.search(r"\d\s*[A-Za-z]{3}\s*(to|in)", text):
        return _fx(text, desk="Hotels desk")
    p = payload_of(text)
    return f"Hotels in {p.get('city', 'Rome')}: Hotel Aurora (EUR 120), Casa Verde (EUR 95)."


VISA = {
    ("US", "FRANCE"): "A US citizen needs no visa for stays under 90 days in France (Schengen).",
    ("US", "JAPAN"): "A US citizen needs no visa for stays under 90 days in Japan.",
}


def handle_visa(text: str) -> str:
    p = payload_of(text)
    who = str(p.get("nationality", "")).upper()
    where = str(p.get("destination", "")).upper()
    if not p:
        up = text.upper()
        who = "US" if "US " in up or "AMERICAN" in up else who
        where = next((c for c in ("FRANCE", "JAPAN") if c in up), where)
    return VISA.get(
        (who, where), f"Check the embassy for {who or 'your'} travellers to {where or 'there'}."
    )


HANDLERS = {
    "lab09-squad-payments": handle_payments,
    "lab09-squad-fx": handle_fx,
    "lab09-squad-compliance": handle_compliance,
    "lab09-squad-flights": handle_flights,
    "lab09-squad-hotels": handle_hotels,
    "lab09-squad-visa": handle_visa,
}
