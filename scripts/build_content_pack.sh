#!/usr/bin/env bash
# Build an offline content-pack zip from the local Final Edit tree.
# Upload the zip via production /admin ? Import content (do not commit the zip).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REGISTRY="$ROOT/0_Drafts/packet_registry.json"
if [[ ! -f "$REGISTRY" ]]; then
  echo "Missing $REGISTRY" >&2
  exit 1
fi

OUT="${1:-$ROOT/content-pack-$(date +%Y%m%d-%H%M%S).zip}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

python3 - <<'PY' "$ROOT" "$STAGE" "$REGISTRY"
import json, shutil, sys
from pathlib import Path

root = Path(sys.argv[1])
stage = Path(sys.argv[2])
reg = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))

paths: set[Path] = set()
for p in reg.get("packets", []):
    md = p.get("editMd") or ""
    if md:
        paths.add(Path(md))
    for r in p.get("attachmentRoots") or []:
        paths.add(Path(r))

# Optional editorial state
for rel in (
    "metrics_status.json",
    "0_Drafts/attachment_order.json",
    "0_Drafts/attachment_bookmarks.json",
    "0_Drafts/packet_order.json",
):
    if (root / rel).exists():
        paths.add(Path(rel))

# Include official + review PDFs so Render can go live without LaTeX rebuild
for base in (root / "0_Drafts" / "_official", root / "0_Drafts" / "_pdf_review"):
    if base.is_dir():
        for pdf in base.rglob("*.pdf"):
            paths.add(pdf.relative_to(root))

# Drafts used by export (supplemental md under 0_Drafts)
drafts = root / "0_Drafts"
for sub in (
    "2_Supplemental_Materials_Teaching",
    "3_Supplemental_Materials_Service",
    "4_Supplemental_Evidence_of_Impact",
):
    d = drafts / sub
    if d.is_dir():
        for f in d.rglob("*"):
            if f.is_file() and f.suffix.lower() in {".md", ".pdf", ".txt"}:
                paths.add(f.relative_to(root))

copied = 0
missing = []
for rel in sorted(paths, key=lambda p: str(p)):
    src = root / rel
    if not src.exists():
        missing.append(str(rel))
        continue
    dest = stage / rel
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
        copied += sum(1 for _ in dest.rglob("*") if _.is_file())
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied += 1

meta = {
    "version": 1,
    "kind": "pop-content-pack",
    "filesCopied": copied,
    "missing": missing,
}
(stage / "content_pack_manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
print(f"Staged {copied} files; missing {len(missing)}")
if missing[:20]:
    print("Missing (first 20):", *missing[:20], sep="\n  ")
PY

rm -f "$OUT"
(
  cd "$STAGE"
  zip -r -q "$OUT" .
)
echo "Wrote $OUT ($(du -h "$OUT" | awk '{print $1}'))"
echo "Keep this zip offline - do not commit it. Upload via /admin -> Import content."
