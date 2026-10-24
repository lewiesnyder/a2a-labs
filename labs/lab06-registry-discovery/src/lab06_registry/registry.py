"""The curated registry: stores CARDS (not just URLs), verifies on the way in, hides what fails.

# CUSTOM: the A2A spec describes curated registries but defines no registry API. This one follows
# its guidance: a catalog that returns full cards plus a verdict. A card that fails verification
# is stored QUARANTINED and is never returned unless ``include_unverified=true`` is passed.
Implements: spec lab06 §3.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
import yaml
from a2a.utils.signing import canonicalize
from a2a_labs_common import config
from a2a_labs_common.a2a.trust import CardVerifier, TrustStore, card_from_json
from fastapi import FastAPI, HTTPException
from google.protobuf.json_format import MessageToDict

logger = logging.getLogger(__name__)
_NS = uuid.UUID("0b6c8c06-4b5e-4d5a-9a3e-2c1a6a2f5f10")


def etag_of(card_json: dict[str, Any]) -> str:
    return hashlib.sha256(canonicalize(card_json).encode()).hexdigest()[:16]


class Registry:
    def __init__(self, store: TrustStore, *, require_streaming: bool = False) -> None:
        self.verifier = CardVerifier(store)
        self.require_streaming = require_streaming
        self.agents: dict[str, dict[str, Any]] = {}

    @staticmethod
    def base_url(target: str) -> str:
        return (target if "://" in target else config.endpoint(target)).rstrip("/")

    async def _fetch(self, url: str, etag: str | None) -> tuple[dict[str, Any] | None, str | None]:
        """Return (card_json, error); card_json is None when the server said 304."""
        headers = {"If-None-Match": etag} if etag else {}
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(f"{url}/.well-known/agent-card.json", headers=headers)
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}"
        if r.status_code == 304:
            return None, None
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        data: dict[str, Any] = r.json()
        return data, None

    def _entry(self, url: str, card_json: dict[str, Any]) -> dict[str, Any]:
        card = card_from_json(card_json)
        report = self.verifier.verify(card, url, require_streaming=self.require_streaming)
        return {
            "id": str(uuid.uuid5(_NS, url)),
            "url": url,
            "card": MessageToDict(card),
            "verified": report.ok,
            "verification_report": report.model_dump(),
            "fetched_at": time.time(),
            "etag": etag_of(card_json),
            "healthy": True,
        }

    async def register(self, target: str) -> dict[str, Any]:
        url = self.base_url(target)
        card_json, error = await self._fetch(url, None)
        if card_json is None:
            raise HTTPException(502, f"cannot fetch the card of {url}: {error}")
        entry = self.agents[str(uuid.uuid5(_NS, url))] = self._entry(url, card_json)
        return entry

    async def refresh(self, agent_id: str) -> dict[str, Any]:
        old = self.agents.get(agent_id)
        if old is None:
            raise HTTPException(404, "unknown agent")
        card_json, error = await self._fetch(old["url"], old["etag"])
        if error:
            old["healthy"] = False
            return old
        if card_json is None or etag_of(card_json) == old["etag"]:
            old.update(healthy=True, fetched_at=time.time(), changed=False)
            return old
        entry = self.agents[agent_id] = {**self._entry(old["url"], card_json), "changed": True}
        return entry

    def search(
        self, skill: str = "", tag: str = "", q: str = "", include_unverified: bool = False
    ) -> list[dict[str, Any]]:
        out = []
        for e in self.agents.values():
            if not (e["healthy"] and (e["verified"] or include_unverified)):
                continue
            skills = e["card"].get("skills", [])
            if skill and skill not in {s["id"] for s in skills}:
                continue
            if tag and tag not in {t for s in skills for t in s.get("tags", [])}:
                continue
            text = " ".join(
                [e["card"].get("name", ""), e["card"].get("description", "")]
                + [
                    f"{s['id']} {s['name']} {s['description']} {' '.join(s.get('tags', []))}"
                    for s in skills
                ]
            ).lower()
            if q and not all(tok in text for tok in q.lower().split()):
                continue
            out.append(e)
        return out


def create_app(registry: Registry, seed: list[str], *, reverify_s: float = 300) -> FastAPI:
    async def seed_all() -> None:
        for target in seed:
            for attempt in range(10):  # the agents may still be starting
                try:
                    await registry.register(target)
                    break
                except HTTPException:
                    await asyncio.sleep(min(1 + attempt, 5))
            else:
                logger.warning("seed agent %s never answered", target)

    async def reverify_loop() -> None:
        while True:
            await asyncio.sleep(reverify_s)
            for agent_id in list(registry.agents):
                with suppress(HTTPException):
                    await registry.refresh(agent_id)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        tasks = [asyncio.create_task(seed_all()), asyncio.create_task(reverify_loop())]
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="Lab 06 registry", lifespan=lifespan)
    app.state.registry = registry

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "agents": len(registry.agents)}

    @app.post("/agents")
    async def post_agent(body: dict[str, str]) -> dict[str, Any]:
        return await registry.register(body["url"])

    @app.get("/agents")
    async def list_agents(
        skill: str = "", tag: str = "", q: str = "", include_unverified: bool = False
    ) -> list[dict[str, Any]]:
        return registry.search(skill, tag, q, include_unverified)

    @app.get("/agents/{agent_id}")
    async def get_agent(agent_id: str) -> dict[str, Any]:
        if agent_id not in registry.agents:
            raise HTTPException(404, "unknown agent")
        return registry.agents[agent_id]

    @app.post("/agents/{agent_id}/refresh")
    async def refresh_agent(agent_id: str) -> dict[str, Any]:
        return await registry.refresh(agent_id)

    @app.delete("/agents/{agent_id}")
    async def delete_agent(agent_id: str) -> dict[str, bool]:
        return {"deleted": registry.agents.pop(agent_id, None) is not None}

    return app


def load_seed(path: Path) -> list[str]:
    return list((yaml.safe_load(path.read_text()) or {}).get("agents", []))
