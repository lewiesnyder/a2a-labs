"""Specialist discovery: Agent Cards in, routing options out. Nothing about a specialist is coded.

The supervisor knows only a list of service names or URLs. Everything it routes on (names,
descriptions, skills) is read from the cards, so registering one more specialist at runtime
changes the routing options without a code change.

# CUSTOM: a small TTL cache with stale-on-error. A specialist that is down keeps its last known
# card, so a request for it is routed, fails visibly in the ledger and is answered gracefully,
# instead of the specialist silently disappearing from the options.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from a2a.types import AgentCard
from a2a_labs_common.a2a.remote import fetch_card_sync

logger = logging.getLogger(__name__)

DEFAULT_SERVICES = "lab05-currency,lab05-weather,lab05-facts"


@dataclass(frozen=True)
class Specialist:
    name: str  # card.name: the routing key
    service: str  # endpoint name or URL used to call it
    description: str
    skills: tuple[str, ...]

    @property
    def criteria(self) -> str:
        """The text the decision model sees for this option, built from the card alone."""
        return f"{self.description}. Skills: {', '.join(self.skills)}"

    def row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "service": self.service,
            "description": self.description,
            "skills": list(self.skills),
            "criteria": self.criteria,
        }


def specialist_from_card(service: str, card: AgentCard) -> Specialist:
    return Specialist(
        name=card.name,
        service=service,
        description=card.description,
        skills=tuple(s.name for s in card.skills),
    )


class Directory:
    def __init__(
        self,
        services: list[str] | None = None,
        *,
        ttl: float | None = None,
        fetch: Callable[[str], AgentCard] = fetch_card_sync,
    ) -> None:
        env = os.environ.get("SPECIALISTS", DEFAULT_SERVICES)
        self.services: list[str] = list(
            services or [s.strip() for s in env.split(",") if s.strip()]
        )
        self.ttl = float(os.environ.get("DISCOVERY_TTL", "30")) if ttl is None else ttl
        self._fetch = fetch
        self._known: dict[str, Specialist] = {}  # service -> last good card
        self._fetched_at = 0.0
        self.fetches = 0  # for tests: how many card fetch rounds happened

    def register(self, service: str) -> None:
        if service not in self.services:
            self.services.append(service)
        self._fetched_at = 0.0  # re-discover on the next request

    def unregister(self, service: str) -> None:
        if service in self.services:
            self.services.remove(service)
        self._known.pop(service, None)

    async def discover(self) -> list[Specialist]:
        if time.monotonic() - self._fetched_at > self.ttl:
            self.fetches += 1
            for service in list(self.services):
                try:
                    card = await asyncio.to_thread(self._fetch, service)
                    self._known[service] = specialist_from_card(service, card)
                except Exception as exc:
                    logger.warning("cannot fetch the card of %s: %s", service, exc)
            self._fetched_at = time.monotonic()
        return [self._known[s] for s in self.services if s in self._known]
