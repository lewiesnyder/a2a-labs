"""Generate Lab 06's dev signing keys, trust store and injected card (idempotent).

Usage: ``uv run scripts/gen_lab06_keys.py``. Private keys are git-ignored.
"""

from __future__ import annotations

from lab06_registry.keys import generate

if __name__ == "__main__":
    print("lab06 keys ready in", generate())
