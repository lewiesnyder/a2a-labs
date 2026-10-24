"""Cross-check notebook ``a2a_coverage`` cell tags against appendices/A-coverage-matrix.md.

Every (lab, tag) the matrix claims must appear in a ``metadata.a2a_coverage`` list of that lab's
notebook; tags a notebook uses must be keys the matrix knows (typo guard).

Usage: ``uv run scripts/check_coverage_tags.py [--list]``. Exit 1 on any gap.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "appendices" / "A-coverage-matrix.md"
LABS = [f"L{n:02d}" for n in range(11)]
# Rows whose "labs" cell is prose the generic parser cannot read: tag -> labs.
OVERRIDES: dict[str, set[str]] = {
    "transport:jsonrpc": {"L00"},  # the other labs simply use it; Lab 00 compares all three
    "transport:rest": {"L00"},
    "transport:grpc": {"L00"},
    "part:text": {"L00"},
    "part:raw": {"L00"},
    "part:url": {"L00"},
    "part:data": {"L00", "L04", "L07", "L09", "L10"},
    "otel:traces": set(),  # all labs, toured in the root notebook (not tagged per lab)
    "otel:logs": set(),
    "otel:metrics": set(),
    "state:COMPLETED": set(LABS),
    "taskstore:db": {"L08"},  # Lab 00 only has it as an exercise
    "security": {"L10"},  # Lab 06's extended card is a preview, tagged op:GetExtendedAgentCard
}


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_matrix(text: str) -> dict[str, set[str]]:
    """tag -> set of lab ids ("L05") that must demonstrate it."""
    need: dict[str, set[str]] = {}
    section = ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:6]
            continue
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        row = cells(line)
        if row[0] in ("Operation (v1.0)", "State", "Pattern", "Feature"):
            continue
        tags = re.findall(
            r"`([a-z0-9]+(?:[:_][A-Za-z0-9_]+)+)`", row[2] if section == "A.1" else row[1]
        )
        tags = [
            t
            for t in tags
            if ":" in t
            or t
            in ("security", "errors", "versioning", "signing", "extensions", "decision", "refs")
        ]
        if section == "A.1":
            tags = [t for t in re.findall(r"`([^`]+)`", row[2])]
            labs = {LABS[i] for i, c in enumerate(row[3:14]) if "✔" in c}
        elif section == "A.2":
            labs = set(re.findall(r"L\d\d", row[2])) or set(LABS)
            if row[2] == "all":
                labs = set(LABS)
        elif section == "A.3":
            labs = set(re.findall(r"L\d\d", re.sub(r"\(.*?\)", "", row[-1])))
        else:  # A.4: bare tags like `security`, `errors`
            tags = re.findall(r"`([^`]+)`", row[1])
            plain = re.sub(r"\(.*?\)", "", row[2])
            labs = (
                set(LABS)
                if plain.strip().lower().startswith("all")
                else set(re.findall(r"L\d\d", plain))
            )
        for t in tags:
            need.setdefault(t, set()).update(OVERRIDES.get(t, labs))
    for t, labs in OVERRIDES.items():
        need[t] = set(labs)
    return need


def notebook_tags() -> dict[str, set[str]]:
    """lab id -> tags found in its notebook's cell metadata."""
    found: dict[str, set[str]] = {}
    for nb in sorted(ROOT.glob("labs/lab*/notebooks/*.ipynb")):
        lab = "L" + nb.parts[-3][3:5]
        data = json.loads(nb.read_text())
        for cell in data["cells"]:
            found.setdefault(lab, set()).update(cell.get("metadata", {}).get("a2a_coverage", []))
    return found


def main() -> int:
    need = parse_matrix(MATRIX.read_text())
    have = notebook_tags()
    missing = [
        (lab, tag)
        for tag, labs in sorted(need.items())
        for lab in sorted(labs)
        if tag not in have.get(lab, set())
    ]
    unknown = [
        (lab, t) for lab, tags in sorted(have.items()) for t in sorted(tags) if t not in need
    ]
    if "--list" in sys.argv:
        for tag, labs in sorted(need.items()):
            print(f"{tag:32} {' '.join(sorted(labs))}")
    for lab, tag in missing:
        print(f"MISSING  {lab}  {tag}")
    for lab, tag in unknown:
        print(f"UNKNOWN  {lab}  {tag}  (not a matrix key)")
    total = sum(len(v) for v in need.values())
    print(f"{total - len(missing)}/{total} matrix cells tagged, {len(unknown)} unknown tag(s)")
    return 1 if missing or unknown else 0


if __name__ == "__main__":
    sys.exit(main())
