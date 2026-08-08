#!/usr/bin/env bash
# Build offline publish/ bundle from 0_Drafts/_official (+ status snapshot).
# Does NOT deploy anywhere.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="$(cd "$HERE/.." && pwd)"
OFFICIAL="$BASE/0_Drafts/_official"
PUBLISH="$BASE/publish"

mkdir -p "$PUBLISH/pdfs"

if [[ ! -d "$OFFICIAL" ]] || ! find "$OFFICIAL" -type f -name '*.pdf' | grep -q .; then
  echo "No official PDFs in 0_Drafts/_official/ yet." >&2
  echo "Open the edit dashboard, Preview -> Save as official for each packet, then re-run." >&2
  exit 1
fi

echo "Copying official PDFs -> publish/pdfs/"
rsync -a --delete --include='*/' --include='*.pdf' --exclude='*' "$OFFICIAL/" "$PUBLISH/pdfs/"

export BASE
python3 <<'PY'
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

base = Path(os.environ["BASE"])
publish = base / "publish"
pdf_root = publish / "pdfs"
status_path = base / "metrics_status.json"
reg_path = base / "0_Drafts" / "packet_registry.json"

status = {}
if status_path.is_file():
    status = json.loads(status_path.read_text(encoding="utf-8"))
reg = {}
if reg_path.is_file():
    reg = {p["name"]: p for p in json.loads(reg_path.read_text(encoding="utf-8")).get("packets", [])}

rows = []
h = hashlib.sha256()
for pdf in sorted(pdf_root.rglob("*.pdf")):
    rel = pdf.relative_to(pdf_root).as_posix()
    data = pdf.read_bytes()
    h.update(rel.encode())
    h.update(data)
    name = pdf.name
    section = rel.split("/")[0] if "/" in rel else "(root)"
    try:
        from pypdf import PdfReader
        pages = len(PdfReader(str(pdf)).pages)
    except Exception:
        pages = 0
    st = status.get(name) or {}
    pkt = reg.get(name) or {}
    rows.append({
        "file": rel,
        "name": name,
        "section": section,
        "pages": pages,
        "needContent": st.get("needContent", "Yes"),
        "level": st.get("level", "Partial"),
        "excludeFromPageTotal": name == "Other_Evidence_of_Impact.pdf",
        "sourceRel": pkt.get("editMd", ""),
        "regenArg": pkt.get("regenArg", ""),
        "editable": False,
        "hasAttachments": False,
        "officialExists": True,
        "officialStale": False,
        "officialAt": st.get("officialAt"),
        "updated": False,
    })

sections = {}
for r in rows:
    sections.setdefault(r["section"], {"pages": 0, "count": 0})
    sections[r["section"]]["pages"] += r["pages"]
    sections[r["section"]]["count"] += 1

tally = sum(r["pages"] for r in rows if not r["excludeFromPageTotal"])
excluded = sum(r["pages"] for r in rows if r["excludeFromPageTotal"])
level_counts = {"Complete": 0, "Partial": 0, "No Content": 0}
need_yes = 0
for r in rows:
    level_counts[r["level"]] = level_counts.get(r["level"], 0) + 1
    if r["needContent"] == "Yes":
        need_yes += 1

inv = {
    "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
    "source": "publish/pdfs",
    "mode": "viewer",
    "editable": False,
    "totals": {
        "pdfs": len(rows),
        "pages": tally,
        "pagesRaw": tally + excluded,
        "pagesExcluded": excluded,
        "needContentYes": need_yes,
        "needContentNo": len(rows) - need_yes,
        "levelComplete": level_counts.get("Complete", 0),
        "levelPartial": level_counts.get("Partial", 0),
        "levelNone": level_counts.get("No Content", 0),
        "pageTotalNote": "Excludes Other_Evidence_of_Impact.pdf (full book packet)",
    },
    "sections": [
        {"id": k, "label": k.replace("_", " "), "pages": v["pages"], "count": v["count"]}
        for k, v in sections.items()
    ],
    "pdfs": rows,
    "updatedFiles": [],
}

manifest = {
    "builtAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    "packetCount": len(rows),
    "contentHash": h.hexdigest(),
    "source": "0_Drafts/_official",
}

(publish / "inventory.json").write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")
(publish / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
(publish / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print("Wrote publish/inventory.json (%d PDFs)" % len(rows))
print("contentHash=%s..." % manifest["contentHash"][:16])
print("Bundle ready at %s" % publish)
PY

echo "Done. Preview with: ./start_viewer.sh"
