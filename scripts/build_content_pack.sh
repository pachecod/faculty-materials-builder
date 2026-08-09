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
markdown_files: list[str] = []
attachment_pdfs = 0
packet_pdfs = 0
for rel in sorted(paths, key=lambda p: str(p)):
    src = root / rel
    if not src.exists():
        missing.append(str(rel))
        continue
    dest = stage / rel
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True)
        for f in dest.rglob("*"):
            if not f.is_file():
                continue
            copied += 1
            suf = f.suffix.lower()
            rel_s = f.relative_to(stage).as_posix()
            if suf == ".md":
                markdown_files.append(rel_s)
            elif suf == ".pdf":
                if rel_s.startswith("0_Drafts/_official/") or rel_s.startswith("0_Drafts/_pdf_review/"):
                    packet_pdfs += 1
                else:
                    attachment_pdfs += 1
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied += 1
        suf = src.suffix.lower()
        rel_s = Path(rel).as_posix()
        if suf == ".md":
            markdown_files.append(rel_s)
        elif suf == ".pdf":
            if rel_s.startswith("0_Drafts/_official/") or rel_s.startswith("0_Drafts/_pdf_review/"):
                packet_pdfs += 1
            else:
                attachment_pdfs += 1

# Also count packet registry editMd explicitly for manifest clarity
reg_md = sorted({(p.get("editMd") or "") for p in reg.get("packets", []) if p.get("editMd")})

meta = {
    "version": 2,
    "kind": "pop-content-pack",
    "filesCopied": copied,
    "missing": missing,
    "markdownFiles": sorted(set(markdown_files)),
    "markdownFileCount": len(set(markdown_files)),
    "registryMarkdownCount": len(reg_md),
    "attachmentPdfCount": attachment_pdfs,
    "packetPdfCount": packet_pdfs,
}
(stage / "content_pack_manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
print(f"Staged {copied} files; missing {len(missing)}")
print(
    f"Manifest: markdown={meta['markdownFileCount']} "
    f"attachmentPdfs={attachment_pdfs} packetPdfs={packet_pdfs}"
)
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
