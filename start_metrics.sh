#!/usr/bin/env bash
# Start the PoP metrics web app (Folder Dump + PDF regenerate + inventory).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
exec python3 "$HERE/serve_metrics.py"
