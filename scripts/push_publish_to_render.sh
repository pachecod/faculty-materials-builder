#!/usr/bin/env bash
# Push local publish/ bundle to a running Render viewer (admin sync).
# Requires RENDER_SYNC_URL and ADMIN_SYNC_TOKEN in .env.
# Does NOT create Render resources � scaffold deploy separately when you are ready.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="$(cd "$HERE/.." && pwd)"
cd "$BASE"
export BASE

if [[ -f "$BASE/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$BASE/.env"
  set +a
fi

: "${ADMIN_SYNC_TOKEN:?Set ADMIN_SYNC_TOKEN in .env}"
: "${RENDER_SYNC_URL:?Set RENDER_SYNC_URL in .env (e.g. https://your-service.onrender.com)}"

echo "Building publish bundle..."
"$HERE/build_publish_bundle.sh"

PUBLISH="$BASE/publish"
if [[ ! -f "$PUBLISH/manifest.json" ]]; then
  echo "publish/manifest.json missing" >&2
  exit 1
fi

URL="${RENDER_SYNC_URL%/}/api/admin/sync"
echo "Uploading to $URL ..."

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "Upserting Postgres catalog..."
  python3 "$HERE/sync_catalog_postgres.py"
fi

python3 <<'PY'
import os
from pathlib import Path
import urllib.request

base = Path(os.environ["BASE"])
publish = base / "publish"
url = os.environ["RENDER_SYNC_URL"].rstrip("/") + "/api/admin/sync"
token = os.environ["ADMIN_SYNC_TOKEN"]

boundary = "----PopPublishBoundary7d93"
parts = []

def add_file(name: str, path: Path, content_type: str):
    data = path.read_bytes()
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
        + data
        + b"\r\n"
    )

for key in ("inventory.json", "status.json", "manifest.json"):
    p = publish / key
    if p.is_file():
        add_file(key, p, "application/json")

pdf_root = publish / "pdfs"
for pdf in sorted(pdf_root.rglob("*.pdf")):
    rel = pdf.relative_to(pdf_root).as_posix()
    add_file(rel, pdf, "application/pdf")

body = b"".join(parts) + f"--{boundary}--\r\n".encode()
req = urllib.request.Request(
    url,
    data=body,
    method="POST",
    headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Admin-Token": token,
    },
)
with urllib.request.urlopen(req, timeout=600) as resp:
    print(resp.read().decode())
print("Push complete.")
PY
