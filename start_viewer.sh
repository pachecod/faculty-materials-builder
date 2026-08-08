#!/usr/bin/env bash
# Local read-only publish preview (serves publish/ when present).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
if [[ ! -f "$HERE/publish/manifest.json" ]]; then
  echo "No publish/ bundle yet. Run: ./scripts/build_publish_bundle.sh" >&2
  echo "Continuing anyway (viewer will fall back to _official or _pdf_review)." >&2
fi
export POP_MODE=viewer
exec python3 "$HERE/serve_metrics.py"
