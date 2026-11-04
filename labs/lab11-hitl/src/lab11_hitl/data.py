"""Fixed data for the trip desk: prices, hotels and the approval limit."""

from __future__ import annotations

FLIGHT_PRICE = 420
SEATS = [("aisle", "Aisle"), ("window", "Window")]
HOTELS = [
    {"id": "hostel", "label": "Blue Hostel", "per_night": 45},
    {"id": "harbor", "label": "Harbor Inn", "per_night": 110},
    {"id": "palace", "label": "Grand Palace", "per_night": 260},
]
APPROVAL_LIMIT = 1000  # a payment above this "looks risky" to the decision model
DEFAULT_NIGHTS = 4
