"""Waits until a lab's services answer on their host ports (used by ``make e2e``).

Usage: ``uv run scripts/wait_for.py --lab lab00 [--timeout 120]``

Services come from ``config/endpoints.yaml``: every ``<lab>-*`` entry except gRPC ports, plus
the shared webhook receiver for the labs that use it. A service is ready when its agent card
(or, for non-agents, any non-5xx HTTP response) is reachable.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx
from a2a_labs_common.config import _endpoints, host_url

SHARED = {
    "lab00": ["webhook-receiver"],
    "lab08": ["webhook-receiver"],
    "lab10": ["webhook-receiver"],
}


def services_for(lab: str) -> list[str]:
    names = [n for n in _endpoints() if n.startswith(f"{lab}-") and not n.endswith("-grpc")]
    return names + SHARED.get(lab, [])


def ready(name: str) -> bool:
    base = host_url(name)
    for path in ("/.well-known/agent-card.json", "/healthz", "/"):
        try:
            if httpx.get(base + path, timeout=3).status_code < 500:
                return True
        except httpx.HTTPError:
            continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", required=True)
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()
    # Lab 04's Agent Server and similar optional services have their own profiles.
    pending = [s for s in services_for(args.lab) if s != "lab04-langgraph-agent-server"]
    deadline = time.monotonic() + args.timeout
    while pending and time.monotonic() < deadline:
        pending = [s for s in pending if not ready(s)]
        if pending:
            time.sleep(2)
    if pending:
        print(f"not ready: {', '.join(pending)}  (try: make up LAB={args.lab})", file=sys.stderr)
        return 1
    print(f"{args.lab}: all services ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
