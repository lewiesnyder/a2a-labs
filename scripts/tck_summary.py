"""Summarises the TCK reports under ``reports/tck/*/compatibility.json`` (spec 08 §3).

Usage: ``uv run scripts/tck_summary.py [--markdown]``. Feeds docs/conformance.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load() -> dict[str, dict]:  # type: ignore[type-arg]
    out = {}
    for path in sorted((ROOT / "reports" / "tck").glob("*/compatibility.json")):
        out[path.parent.name] = json.loads(path.read_text())
    return out


def failures(report: dict) -> list[tuple[str, str, str]]:  # type: ignore[type-arg]
    """(requirement id, level, first error) for every requirement whose status is FAIL."""
    return [
        (req_id, info.get("level", ""), (info.get("errors") or [""])[0][:110])
        for req_id, info in report.get("per_requirement", {}).items()
        if info.get("status") == "FAIL"
    ]


def jsonrpc_line(report: dict) -> str:  # type: ignore[type-arg]
    t = report.get("per_transport", {}).get("jsonrpc", {})
    return f"jsonrpc {t.get('passed', 0)}/{t.get('total', 0)} passed, {t.get('failed', 0)} failed"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()
    for name, rep in load().items():
        s = rep["summary"]
        print(
            f"{name}: must={s['must_compatibility']} overall={s['overall_compatibility']}"
            f" | {jsonrpc_line(rep)}"
        )
        for req_id, level, err in failures(rep):
            print(f"   FAIL {req_id} ({level}): {err}")
        if args.markdown:
            print()


if __name__ == "__main__":
    main()
