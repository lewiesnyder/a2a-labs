#!/usr/bin/env bash
# Runs the official A2A TCK against a running agent (spec 08 §3).
# Usage: scripts/run_tck.sh <service-name | url> [--level must|should|may] [--transport LIST] [-- pytest args]
# The TCK is cloned into .cache/a2a-tck at a pinned commit and run with its own uv environment.
set -euo pipefail

TCK_COMMIT="${TCK_COMMIT:-d4d6fae7887f88ef607fb461c13e3b0e1802f19b}"  # a2a-tck main, 2026-10-22
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/.cache/a2a-tck"

target="${1:?usage: run_tck.sh <service-name|url> [tck args]}"
shift
name="$(echo "$target" | sed -E 's#https?://##; s#[^A-Za-z0-9._-]+#_#g')"
if [[ "$target" != http* ]]; then
  # Resolve a service name from config/endpoints.yaml (host URL; this script runs on the host).
  target="$(cd "$ROOT" && uv run python -c "import sys; from a2a_labs_common.config import host_url; print(host_url(sys.argv[1]))" "$target" 2>/dev/null)"
fi

if [[ ! -d "$CACHE/.git" ]]; then
  git clone https://github.com/a2aproject/a2a-tck.git "$CACHE"
fi
git -C "$CACHE" fetch --depth 1 origin "$TCK_COMMIT" 2>/dev/null || true
git -C "$CACHE" checkout --quiet "$TCK_COMMIT"

cd "$CACHE"
uv sync --quiet
# [VERIFY resolved] current CLI is --sut-host and --level (not --category as in the older spec).
rc=0
uv run ./run_tck.py --sut-host "$target" "$@" || rc=$?   # keep going: reports are written either way

# Keep one report folder per system under test (docs/conformance.md is generated from these).
mkdir -p "$ROOT/reports/tck/$name"
cp -r reports/. "$ROOT/reports/tck/$name/" 2>/dev/null || true
exit "$rc"
