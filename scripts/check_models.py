"""Verifies the Ollama models and the nimble-v3 /v1/systemone probe (spec 04 §5).

Implements: spec 04 §5. Exit code is non-zero when --require-all and any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
if os.environ.get("A2A_LABS_RUNTIME") != "docker":  # host runs cannot resolve host.docker.internal
    BASE = BASE.replace("host.docker.internal", "localhost")
REQUIRED = {
    "reasoning": os.environ.get("REASONING_MODEL", "qwen3.8:27b"),
    "quick": os.environ.get("QUICK_MODEL", "llama3.2:latest"),
    "decision": os.environ.get("DECISION_MODEL", "nimble-v3:latest"),
}
PROBE = {
    "state": "What is 100 USD in EUR?",
    "questions": {
        "route": {
            "type": "choice",
            "instructions": "Which specialist should handle the request?",
            "criteria": {
                "currency": "Exchange rates or converting money",
                "weather": "Weather",
                "none": "None apply",
            },
        }
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--require-all", action="store_true")
    ap.add_argument("--pull", action="store_true")
    args = ap.parse_args()
    if os.environ.get("SKIP_MODEL_CHECK", "false").lower() == "true":
        print("SKIP_MODEL_CHECK=true: skipping")
        return 0
    failures = 0
    with httpx.Client(base_url=BASE, timeout=60) as c:
        version = c.get("/api/version").json()["version"]
        print(f"Ollama {version} at {BASE} (nimble-v3 needs >= 0.40.4)")
        tags = {m["name"] for m in c.get("/api/tags").json()["models"]}
        for role, tag in REQUIRED.items():
            ok = tag in tags
            print(f"{'OK     ' if ok else 'MISSING'} {role}: {tag}")
            failures += not ok
            if ok and role != "decision":
                caps = c.post("/api/show", json={"model": tag}).json().get("capabilities", [])
                print(f"        capabilities: {caps}")
        if REQUIRED["decision"] in tags:
            r = c.post("/v1/systemone", json={"model": REQUIRED["decision"], **PROBE})
            print(f"systemone probe -> {r.status_code}\n{json.dumps(r.json(), indent=2)[:1500]}")
            failures += r.status_code != 200
    return 1 if failures and args.require_all else 0


if __name__ == "__main__":
    sys.exit(main())
