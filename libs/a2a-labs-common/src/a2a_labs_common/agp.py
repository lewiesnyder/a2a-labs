"""Agent Gateway Protocol (AGP) building blocks: EXPERIMENTAL, not core A2A.

AGP is a proposed routing extension from ``a2aproject/a2a-samples/extensions/agp/spec.md`` (V1).
No SDK supports it, so everything here is ``# CUSTOM`` and follows that document:

* data: ``CapabilityAnnouncement``, ``Intent`` (``IntentPayload`` in the sample), ``RouteEntry``;
* ``AgpTable``: capability -> routes, lowest-cost policy-compliant selection;
* errors -32200 ``AGP_ROUTE_NOT_FOUND``, -32201 ``AGP_POLICY_VIOLATION``,
  -32202 ``AGP_TABLE_STALE``;
* transport (a convention, the spec defines none): ordinary A2A ``SendMessage`` calls with
  ``A2A-Extensions: <AGP_URI>`` and ONE data part ``{"agp": {...}}``.

# CUSTOM: the whole module (DEV-63..66). The error registration patches module-level dictionaries
# of a2a-sdk (see ``register_agp_errors``) because the SDK has no public way to add error codes.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from a2a.helpers import get_data_parts
from a2a.types import Message
from a2a.utils import errors as sdk_errors
from a2a.utils.errors import A2AError
from pydantic import BaseModel, ConfigDict, Field

AGP_URI = "https://github.com/a2aproject/a2a-samples/tree/main/extensions/agp"
AGP_VERSION = "1.0"
DEFAULT_TTL_S = 120.0
PURGE_FACTOR = 10  # an entry older than ttl * 10 is dropped (stale in between)


# ── data structures (spec §1, §2, §3) ─────────────────────────────────────────────────────────
class CapabilityAnnouncement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    version: str
    cost: float | None = None
    policy: dict[str, Any] = Field(default_factory=dict)


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_capability: str
    payload: dict[str, Any]
    policy_constraints: dict[str, Any] | None = None


class RouteEntry(BaseModel):
    """The spec's ``RouteEntry`` {path, cost, policy} plus what a gateway needs to forward."""

    capability: str
    version: str = AGP_VERSION
    path: list[str]  # first element = the next hop (a squad or a child gateway)
    cost: float
    policy: dict[str, Any]
    next_hop_url: str
    is_gateway: bool = False  # forward an Intent (True) or plain text (False)
    announced_at: float  # epoch seconds, set by the receiving gateway

    @property
    def next_hop(self) -> str:
        return self.path[0]


# ── errors (spec §5) ──────────────────────────────────────────────────────────────────────────
class AgpRouteNotFoundError(A2AError):
    message = "AGP_ROUTE_NOT_FOUND: no squad has announced the requested capability"


class AgpPolicyViolationError(A2AError):
    message = "AGP_POLICY_VIOLATION: routes exist but none satisfies the policy constraints"


class AgpTableStaleError(A2AError):
    message = "AGP_TABLE_STALE: the only routes for this capability have expired"


def agp_error(cls: type[A2AError], detail: str, **data: Any) -> A2AError:
    """``cls`` with the code name and a human reason in the message; ``data`` rides as ErrorInfo."""
    name = AGP_ERRORS[cls][1]
    return cls(message=f"{name}: {detail}", data={k: str(v) for k, v in data.items()})


AGP_ERRORS: dict[type[A2AError], tuple[int, str]] = {
    AgpRouteNotFoundError: (-32200, "AGP_ROUTE_NOT_FOUND"),
    AgpPolicyViolationError: (-32201, "AGP_POLICY_VIOLATION"),
    AgpTableStaleError: (-32202, "AGP_TABLE_STALE"),
}


def register_agp_errors() -> None:
    """Teach a2a-sdk the three AGP codes (idempotent).

    # CUSTOM: ``build_error_response`` looks codes up in module-level dicts, and the JSON-RPC
    # client builds its code -> exception table at import time. Adding our classes to those dicts
    # makes AGP errors travel as proper JSON-RPC errors (code, message, ErrorInfo reason) and
    # come back as the same exception class. There is no public registration API (DEV-63).
    """
    from a2a.client.transports import jsonrpc as client_jsonrpc
    from a2a.server.jsonrpc_models import JSONRPCError
    from a2a.server.request_handlers import response_helpers

    for cls, (code, reason) in AGP_ERRORS.items():
        sdk_errors.JSON_RPC_ERROR_CODE_MAP[cls] = code
        sdk_errors.A2A_ERROR_MAPPING[cls] = sdk_errors.ErrorMapping(
            400, "FAILED_PRECONDITION", reason
        )
        sdk_errors.A2A_ERROR_REASONS[cls] = reason
        sdk_errors.A2A_REASON_TO_ERROR[reason] = cls
        response_helpers.EXCEPTION_MAP[cls] = JSONRPCError
        client_jsonrpc._JSON_RPC_ERROR_CODE_TO_A2A_ERROR[code] = cls


register_agp_errors()


# ── policy matching (spec §3; the sample uses equality and >= for security_level) ─────────────
def policy_problems(policy: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[str]:
    """Why a route's ``policy`` does not satisfy the Intent's ``constraints`` (empty = it does).

    * a list-valued policy (``region: ["US","EU"]``) accepts a constraint that is IN the list,
      and ``"*"`` in the list accepts anything;
    * ``pii`` is about the PAYLOAD: if it carries personal data the route must be able to handle
      it (``policy.pii == true``); ``pii: false`` constrains nothing;
    * ``security_level`` is satisfied by an equal or higher level (as in the sample);
    * every other key must exist and be equal. Keys starting with ``_`` are bookkeeping.
    """
    problems: list[str] = []
    for key, wanted in constraints.items():
        if key.startswith("_") or key == "pii_detected":
            continue
        if key not in policy:
            problems.append(f"{key}: the route declares no such policy")
            continue
        have = policy[key]
        if key == "pii":
            if wanted and not have:
                problems.append("pii: the payload has personal data, the route does not take it")
        elif isinstance(have, list):
            if wanted not in have and "*" not in have:
                problems.append(f"{key}: {wanted!r} not in {have}")
        elif key == "security_level" and isinstance(have, int | float):
            if have < wanted:
                problems.append(f"security_level: {have} < {wanted}")
        elif have != wanted:
            problems.append(f"{key}: {have!r} != {wanted!r}")
    return problems


class Lookup(BaseModel):
    best: RouteEntry
    rejected: list[dict[str, Any]]  # other candidates and why they lost


class AgpTable:
    """capability -> routes. ``clock`` is injectable so tests control staleness."""

    def __init__(
        self, ttl_s: float = DEFAULT_TTL_S, clock: Callable[[], float] = time.time
    ) -> None:
        self.ttl_s, self.clock = ttl_s, clock
        self.routes: dict[str, list[RouteEntry]] = {}
        self.failed: dict[tuple[str, tuple[str, ...]], float] = {}  # route key -> retry-after

    # ── writes ────────────────────────────────────────────────────────────────────────────
    def upsert(
        self,
        ann: CapabilityAnnouncement,
        path: list[str],
        next_hop_url: str,
        *,
        is_gateway: bool = False,
    ) -> RouteEntry:
        """Add or refresh a route; the key is (capability, path), so a re-announcement refreshes."""
        entry = RouteEntry(
            capability=ann.capability,
            version=ann.version,
            path=list(path),
            cost=ann.cost or 0.0,
            policy=ann.policy,
            next_hop_url=next_hop_url,
            is_gateway=is_gateway,
            announced_at=self.clock(),
        )
        bucket = self.routes.setdefault(ann.capability, [])
        for i, old in enumerate(bucket):
            if old.path == entry.path:
                bucket[i] = entry
                break
        else:
            bucket.append(entry)
        self.failed.pop((entry.capability, tuple(entry.path)), None)
        return entry

    def withdraw(self, path_prefix: str) -> int:
        """Drop every route whose path starts with ``path_prefix``; returns how many."""
        gone = 0
        for cap in list(self.routes):
            keep = [r for r in self.routes[cap] if r.path[0] != path_prefix]
            gone += len(self.routes[cap]) - len(keep)
            self.routes[cap] = keep
            if not keep:
                del self.routes[cap]
        return gone

    def purge(self) -> int:
        """Drop routes older than ``ttl * PURGE_FACTOR`` (long dead, not merely stale)."""
        limit = self.clock() - self.ttl_s * PURGE_FACTOR
        gone = 0
        for cap in list(self.routes):
            keep = [r for r in self.routes[cap] if r.announced_at >= limit]
            gone += len(self.routes[cap]) - len(keep)
            if keep:
                self.routes[cap] = keep
            else:
                del self.routes[cap]
        return gone

    def mark_failed(self, entry: RouteEntry, for_s: float | None = None) -> None:
        """A forward to this route failed: skip it for a while (a quick failover)."""
        self.failed[(entry.capability, tuple(entry.path))] = self.clock() + (for_s or self.ttl_s)

    # ── reads ─────────────────────────────────────────────────────────────────────────────
    def is_stale(self, entry: RouteEntry) -> bool:
        return self.clock() - entry.announced_at > self.ttl_s

    def _failed(self, entry: RouteEntry) -> bool:
        return self.failed.get((entry.capability, tuple(entry.path)), 0.0) > self.clock()

    def fresh_routes(self) -> list[RouteEntry]:
        return [r for rs in self.routes.values() for r in rs if not self.is_stale(r)]

    def lookup(self, capability: str, constraints: Mapping[str, Any] | None = None) -> Lookup:
        """Lowest-cost route that is fresh, reachable and policy-compliant.

        * nothing announced -> ``AgpRouteNotFoundError``;
        * every route expired -> ``AgpTableStaleError``;
        * fresh routes exist but all break the constraints -> ``AgpPolicyViolationError``.
        """
        routes = self.routes.get(capability, [])
        if not routes:
            raise agp_error(
                AgpRouteNotFoundError, f"nobody announced {capability!r}", capability=capability
            )
        fresh = [r for r in routes if not self.is_stale(r)]
        if not fresh:
            ages = ", ".join(f"{self.clock() - r.announced_at:.0f}s" for r in routes)
            raise agp_error(
                AgpTableStaleError,
                f"every route for {capability!r} is older than {self.ttl_s:g}s ({ages})",
                capability=capability,
            )
        compliant: list[RouteEntry] = []
        rejected: list[dict[str, Any]] = []
        for r in fresh:
            problems = policy_problems(r.policy, constraints or {})
            if problems:
                rejected.append({"path": r.path, "cost": r.cost, "why": problems})
            elif self._failed(r):
                rejected.append({"path": r.path, "cost": r.cost, "why": ["recently unreachable"]})
            else:
                compliant.append(r)
        if not compliant:
            if any(x["why"] != ["recently unreachable"] for x in rejected) or not rejected:
                why = "; ".join(f"{'/'.join(x['path'])}: {x['why']}" for x in rejected)
                raise agp_error(
                    AgpPolicyViolationError,
                    f"no route for {capability!r} satisfies {dict(constraints or {})} ({why})",
                    capability=capability,
                    rejected=why,
                )
            raise agp_error(
                AgpTableStaleError,
                f"every compliant route for {capability!r} is stale or was recently unreachable",
                capability=capability,
            )
        best = min(compliant, key=lambda r: (r.cost, r.announced_at))
        others = [
            {"path": r.path, "cost": r.cost, "why": ["higher cost"]}
            for r in compliant
            if r is not best
        ]
        return Lookup(best=best, rejected=[*rejected, *others])

    def snapshot(self) -> dict[str, Any]:
        """JSON for ``GET /agp/table`` (lab-only debugging)."""
        now = self.clock()
        return {
            "ttl_s": self.ttl_s,
            "capabilities": {
                cap: [
                    {
                        "path": r.path,
                        "cost": r.cost,
                        "policy": r.policy,
                        "next_hop_url": r.next_hop_url,
                        "is_gateway": r.is_gateway,
                        "age_s": round(now - r.announced_at, 1),
                        "stale": self.is_stale(r),
                    }
                    for r in rs
                ]
                for cap, rs in sorted(self.routes.items())
            },
        }


# ── message helpers (the transport convention) ────────────────────────────────────────────────
def announce_data(
    announcements: list[CapabilityAnnouncement],
    path: list[str],
    next_hop_url: str,
    *,
    gateway: bool,
) -> dict[str, Any]:
    return {
        "agp": {
            "type": "announce",
            "announcements": [a.model_dump(exclude_none=True) for a in announcements],
            "path": path,
            "next_hop_url": next_hop_url,
            "gateway": gateway,
        }
    }


def intent_data(intent: Intent, *, pii_detected: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "intent", "intent": intent.model_dump(exclude_none=True)}
    if pii_detected is not None:
        body["pii_detected"] = pii_detected  # the upstream gateway already asked the decision model
    return {"agp": body}


def extract_agp(message: Message) -> dict[str, Any] | None:
    """The ``agp`` object of a message's data part, if it carries one."""
    for data in get_data_parts(message.parts):
        if isinstance(data, dict) and isinstance(data.get("agp"), dict):
            return data["agp"]  # type: ignore[no-any-return]
    return None
