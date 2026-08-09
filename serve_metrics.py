#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PoP Final Edit dashboard: local shells vs Render shells (app_pages/).

  POP_MODE=edit        python3 serve_metrics.py
      /edit            local.html — Edit & Append
      /admin           local.html — local Admin (build pack, import, site access)
      /preview         portal.html — test Render public /
      /render-admin    hosted-admin.html — test Render /admin

  POP_MODE=production  gunicorn …
      /                portal.html
      /admin           hosted-admin.html

  POP_MODE=viewer      portal.html at /
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    redirect,
    request,
    send_file,
    send_from_directory,
    session,
    Response,
)
from pypdf import PdfReader
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
DRAFTS = BASE / "0_Drafts"
EXPORT_SCRIPT = DRAFTS / "export_pdfs.sh"
REGISTRY_FILE = DRAFTS / "packet_registry.json"
ENV_FILE = BASE / ".env"


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        os.environ.setdefault(key, val)


load_dotenv()

POP_MODE = os.environ.get("POP_MODE", "edit").strip().lower()
if POP_MODE not in {"edit", "viewer", "production"}:
    POP_MODE = "edit"

# View password: VIEW_PASSWORD preferred; SITE_PASSWORD kept as alias
VIEW_PASSWORD = (
    os.environ.get("VIEW_PASSWORD", "").strip()
    or os.environ.get("SITE_PASSWORD", "").strip()
)
SITE_PASSWORD = VIEW_PASSWORD  # back-compat alias
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
ADMIN_SYNC_TOKEN = os.environ.get("ADMIN_SYNC_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "8765"))

_pub_env = os.environ.get("PUBLISH_DATA_ROOT", "").strip()
USE_DATA_LAYOUT = bool(_pub_env)
PUBLISH = Path(_pub_env).resolve() if USE_DATA_LAYOUT else (BASE / "publish").resolve()

# Content + PDF roots: persistent disk on Render; local tree otherwise
CONTENT_ROOT = (PUBLISH / "workspace") if USE_DATA_LAYOUT else BASE
REVIEW = (PUBLISH / "_pdf_review") if USE_DATA_LAYOUT else (DRAFTS / "_pdf_review")
OFFICIAL = (PUBLISH / "_official") if USE_DATA_LAYOUT else (DRAFTS / "_official")
CONTENT_DRAFTS = CONTENT_ROOT / "0_Drafts"
SITE_AUTH_FILE = (PUBLISH / "site_auth.json") if USE_DATA_LAYOUT else (BASE / "site_auth.json")
STATUS_FILE = (
    (PUBLISH / "metrics_status.json") if USE_DATA_LAYOUT else (BASE / "metrics_status.json")
)
ATTACHMENT_ORDER_FILE = (
    (CONTENT_DRAFTS / "attachment_order.json")
    if USE_DATA_LAYOUT
    else (DRAFTS / "attachment_order.json")
)
ATTACHMENT_BOOKMARKS_FILE = (
    (CONTENT_DRAFTS / "attachment_bookmarks.json")
    if USE_DATA_LAYOUT
    else (DRAFTS / "attachment_bookmarks.json")
)
PACKET_ORDER_FILE = (
    (CONTENT_DRAFTS / "packet_order.json")
    if USE_DATA_LAYOUT
    else (DRAFTS / "packet_order.json")
)

COURSES_DIR = CONTENT_ROOT / "2_Teaching" / "2_Courses"
EVIDENCE_ROOT = CONTENT_ROOT / "5_Other Evidence of Impact"
CREATIVE_WORK_DIR = CONTENT_ROOT / "3_Connections to the Profession" / "3_Creative Work"

COURSE_KIND_FOLDERS = {
    "syllabus": "1_Syllabi",
    "teaching_examples": "teachingexamples",
    "student_work": "2_Student_Work",
    "assessments": "3_Assessments",
    "other": "4_Other_Course_Materials",
}

EVIDENCE_KIND_FOLDERS = {
    "student_correspondence": "3_Student_Correspondence",
    "academic_correspondence": "4_Academic_Correspondence",
    "profession_correspondence": "5_Profession_and_Industry_Correspondence",
    "other_evidence": "7_Other_Evidence_of_Impact",
    "creative_work": None,  # special: Connections Creative Work folder
}

COURSE_LABELS = {
    "1": "Course 1 - JNL 221",
    "2a": "Course 2a - MND 413/613 Residential",
    "2b": "Course 2b - MND 613 Online",
    "3": "Course 3 - MMI 680",
    "4": "Course 4 - MND 545",
    "5": "Course 5 - MND 505",
    "6": "Course 6 - MND 600",
}

app = Flask(__name__, static_folder=None)
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512MB content packs

_regen_lock = threading.Lock()
_regen_state = {
    "running": False,
    "log": "",
    "ok": None,
    "mode": None,
    "updatedFiles": [],
}
_last_updated_files: list[str] = []
_import_lock = threading.Lock()
_import_state = {"running": False, "log": "", "ok": None}


def ensure_data_dirs() -> dict:
    """Create persistent disk dirs (gunicorn never calls main())."""
    info = {
        "useDataLayout": USE_DATA_LAYOUT,
        "publish": str(PUBLISH),
        "contentRoot": str(CONTENT_ROOT),
        "review": str(REVIEW),
        "official": str(OFFICIAL),
        "writable": False,
        "error": None,
    }
    if not USE_DATA_LAYOUT:
        info["writable"] = os.access(CONTENT_ROOT, os.W_OK)
        return info
    try:
        for path in (PUBLISH, CONTENT_ROOT, REVIEW, OFFICIAL, CONTENT_DRAFTS, PUBLISH / "pdfs"):
            path.mkdir(parents=True, exist_ok=True)
        probe = PUBLISH / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        info["writable"] = True
    except Exception as exc:
        info["error"] = str(exc)
        info["writable"] = False
    return info


# Important on Render: create /data subdirs when the worker boots
_DATA_DIR_STATUS = ensure_data_dirs()


def is_production() -> bool:
    return POP_MODE == "production"


def is_viewer() -> bool:
    """Read-only public surface (viewer mode or production non-admin)."""
    return POP_MODE in {"viewer", "production"}


def is_admin_session() -> bool:
    return session.get("role") == "admin"


def can_edit() -> bool:
    """Authoring (markdown, rebuild, attachments) — local edit mode only."""
    return POP_MODE == "edit"


def can_manage_viewer() -> bool:
    """Viewer ops: import pack, view password. Production admin or local edit."""
    if POP_MODE == "edit":
        return True
    if is_production():
        return is_admin_session()
    return False


def load_site_auth() -> dict:
    if SITE_AUTH_FILE.is_file():
        try:
            return json.loads(SITE_AUTH_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_site_auth(data: dict) -> None:
    SITE_AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    SITE_AUTH_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def view_password_hash() -> str | None:
    """Runtime hash from disk overrides env bootstrap."""
    stored = load_site_auth().get("viewPasswordHash")
    if stored:
        return str(stored)
    return None


def view_password_configured() -> bool:
    if view_password_hash():
        return True
    return bool(VIEW_PASSWORD)


def check_view_password(pw: str) -> bool:
    stored = view_password_hash()
    if stored:
        return check_password_hash(stored, pw)
    if VIEW_PASSWORD:
        return hmac.compare_digest(pw, VIEW_PASSWORD)
    return False


def viewer_auth_ok() -> bool:
    if POP_MODE == "edit":
        return True
    if is_admin_session():
        return True
    if not view_password_configured():
        return True
    return session.get("view_authed") is True or session.get("authed") is True


def require_edit(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not can_edit():
            if is_production():
                return jsonify(
                    {
                        "ok": False,
                        "error": (
                            "Content editing is not available on the hosted viewer. "
                            "Install and run this app locally (POP_MODE=edit) to edit, "
                            "rebuild PDFs, then upload a content pack from /admin."
                        ),
                    }
                ), 403
            return jsonify({"ok": False, "error": "Read-only viewer mode"}), 403
        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    """Viewer-management ops (import, site access) — not content authoring."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not can_manage_viewer():
            if is_production() and not is_admin_session():
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Admin login required"}), 401
                return redirect("/admin/login")
            return jsonify({"ok": False, "error": "Admin only"}), 403
        if is_production() and not ADMIN_PASSWORD:
            return jsonify({"ok": False, "error": "ADMIN_PASSWORD not configured"}), 503
        return fn(*args, **kwargs)

    return wrapper


def require_viewer_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not viewer_auth_ok():
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Login required"}), 401
            nxt = "/admin/login" if request.path.startswith("/admin") else "/login"
            return redirect(nxt)
        return fn(*args, **kwargs)

    return wrapper


def content_has_sources() -> bool:
    for p in load_registry().get("packets", []):
        md = p.get("editMd") or ""
        if md and (CONTENT_ROOT / md).is_file():
            return True
    return False


def load_registry() -> dict:
    if REGISTRY_FILE.is_file():
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return {"version": 1, "packets": []}


def packet_by_pdf(pdf: str) -> dict | None:
    """pdf may be reviewRel or basename."""
    pdf = (pdf or "").strip().lstrip("/")
    reg = load_registry()
    for p in reg.get("packets", []):
        if p.get("reviewRel") == pdf or p.get("name") == pdf or p.get("name") == Path(pdf).name:
            return p
    return None


def safe_under(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    root_r = root.resolve()
    if not str(target).startswith(str(root_r) + os.sep) and target != root_r:
        raise ValueError("Path escapes base")
    return target


def snapshot_review_mtimes(root: Path = REVIEW) -> dict[str, float]:
    out: dict[str, float] = {}
    if not root.is_dir():
        return out
    for pdf in root.rglob("*.pdf"):
        try:
            out[pdf.relative_to(root).as_posix()] = pdf.stat().st_mtime
        except OSError:
            continue
    return out


def changed_since(before: dict[str, float], root: Path = REVIEW) -> list[str]:
    after = snapshot_review_mtimes(root)
    changed = []
    for rel, mtime in after.items():
        prev = before.get(rel)
        if prev is None or mtime > prev + 0.001:
            changed.append(rel)
    return sorted(changed)


def packet_key(folder_name: str) -> str | None:
    m = re.match(r"\d+[a-z]?_Course_([0-9]+[a-z]?)_", folder_name)
    return m.group(1) if m else None


def course_folders() -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not COURSES_DIR.is_dir():
        return out
    for folder in sorted(COURSES_DIR.iterdir()):
        if not folder.is_dir():
            continue
        key = packet_key(folder.name)
        if key and key in COURSE_LABELS:
            out[key] = folder
    return out


def load_status() -> dict:
    if STATUS_FILE.is_file():
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    return {}


def save_status(status: dict) -> None:
    STATUS_FILE.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")


NEED_VALUES = {"Yes", "No"}
LEVEL_VALUES = {"Complete", "Partial", "No Content"}


def default_status_for(name: str) -> dict:
    partial = {
        "Pacheco_Daniel_Teaching_Schedule.pdf",
        "Pacheco_Daniel_Introduction_to_Teaching.pdf",
        "Student_Correspondence.pdf",
    }
    if name in partial:
        return {"needContent": "Yes", "level": "Partial"}
    return {"needContent": "No", "level": "Complete"}


def load_attachment_orders() -> dict:
    if ATTACHMENT_ORDER_FILE.is_file():
        try:
            return json.loads(ATTACHMENT_ORDER_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_attachment_orders(orders: dict) -> None:
    ATTACHMENT_ORDER_FILE.write_text(
        json.dumps(orders, indent=2) + "\n", encoding="utf-8"
    )


def load_packet_orders() -> dict:
    """section id -> ordered list of reviewRel paths."""
    if PACKET_ORDER_FILE.is_file():
        try:
            data = json.loads(PACKET_ORDER_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_packet_orders(orders: dict) -> None:
    PACKET_ORDER_FILE.write_text(
        json.dumps(orders, indent=2) + "\n", encoding="utf-8"
    )


def section_sort_key(section_id: str):
    m = re.match(r"^(\d+)_", section_id or "")
    if m:
        return (0, int(m.group(1)), section_id)
    return (1, 0, section_id or "")


def apply_packet_order(rows: list[dict]) -> list[dict]:
    orders = load_packet_orders()
    by_sec: dict[str, list[dict]] = {}
    for r in rows:
        by_sec.setdefault(r["section"], []).append(r)
    result: list[dict] = []
    for sec in sorted(by_sec.keys(), key=section_sort_key):
        items = by_sec[sec]
        by_file = {r["file"]: r for r in items}
        preferred = orders.get(sec) or []
        ordered: list[dict] = []
        for f in preferred:
            f = str(f).replace("\\", "/").strip()
            if f in by_file:
                ordered.append(by_file.pop(f))
        ordered.extend(sorted(by_file.values(), key=lambda r: r["file"].lower()))
        result.extend(ordered)
    return result


def load_attachment_bookmarks() -> dict:
    if ATTACHMENT_BOOKMARKS_FILE.is_file():
        try:
            return json.loads(ATTACHMENT_BOOKMARKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_attachment_bookmarks(bookmarks: dict) -> None:
    ATTACHMENT_BOOKMARKS_FILE.write_text(
        json.dumps(bookmarks, indent=2) + "\n", encoding="utf-8"
    )


def default_bookmark_title(path: Path) -> str:
    """Mirror build_attachments.label_for for UI defaults."""
    parent = path.parent.name
    if parent == "1_Syllabi":
        return "Appendix: Syllabus"
    if parent == "2_Student_Work":
        stem = path.stem.replace("_", " ").strip()
        return f"Appendix: Student Work — {stem}"
    if parent == "3_Assessments":
        stem = path.stem
        m = re.search(r"(FALL|SPRING|SUMMER)\d{2}", stem, re.I)
        term = m.group(0) if m else stem[:40]
        return f"Appendix: Assessment — {term}"
    if parent == "4_Other_Course_Materials":
        stem = path.stem.replace("_", " ").strip()
        return f"Appendix: Other Course Materials — {stem}"
    if path.name == "teaching_examples.md" or path.stem == "teaching_examples":
        return "Appendix: Teaching Examples"
    if parent == "3_Creative Work":
        stem = path.stem.replace("_", " ").strip()
        return f"Appendix: Exhibit — {stem}"
    stem = path.stem.replace("_redacted", "").replace("_", " ").strip()
    return f"Appendix: {stem.title()}"


def order_key_for_packet(pkt: dict) -> str:
    return pkt.get("reviewRel") or pkt.get("name") or ""


def sort_attachments(items: list[dict], preferred: list[str] | None) -> list[dict]:
    if not preferred:
        return items
    by_rel = {a["rel"]: a for a in items}
    by_name = {}
    for a in items:
        by_name.setdefault(a["name"], []).append(a)
    ordered = []
    seen = set()
    for key in preferred:
        item = by_rel.get(key)
        if not item:
            matches = by_name.get(Path(key).name) or []
            item = matches[0] if len(matches) == 1 else None
            if not item:
                for a in items:
                    if a["rel"].endswith("/" + key) or a["rel"] == key:
                        item = a
                        break
        if item and item["rel"] not in seen:
            ordered.append(item)
            seen.add(item["rel"])
    for a in items:
        if a["rel"] not in seen:
            ordered.append(a)
            seen.add(a["rel"])
    return ordered


def list_attachments(pkt: dict) -> list[dict]:
    items = []
    for root_rel in pkt.get("attachmentRoots") or []:
        root = CONTENT_ROOT / root_rel
        if not root.is_dir():
            continue
        for f in sorted(root.iterdir()):
            if not f.is_file():
                continue
            if f.name.startswith("."):
                continue
            if f.name in {"append_order.json", "attachment_order.json"}:
                continue
            if f.parent.name == "teachingexamples":
                if f.name == "teaching_examples.md" or f.name.endswith("_redacted.pdf"):
                    pass
                else:
                    continue
            elif not f.name.lower().endswith(".pdf"):
                continue
            pages = 0
            if f.suffix.lower() == ".pdf":
                try:
                    pages = len(PdfReader(str(f)).pages)
                except Exception:
                    pages = 0
            rel = f.relative_to(CONTENT_ROOT).as_posix()
            default_title = default_bookmark_title(f)
            custom = load_attachment_bookmarks().get(rel)
            items.append(
                {
                    "rel": rel,
                    "name": f.name,
                    "root": root_rel,
                    "pages": pages,
                    "size": f.stat().st_size,
                    "defaultBookmarkTitle": default_title,
                    "bookmarkTitle": custom or default_title,
                    "bookmarkCustom": bool(custom),
                }
            )
    preferred = load_attachment_orders().get(order_key_for_packet(pkt)) or []
    return sort_attachments(items, preferred)


def pdf_root_for_mode() -> Path:
    """Primary root hint (edit uses review; viewers prefer official when present)."""
    if is_viewer():
        if OFFICIAL.is_dir() and any(OFFICIAL.rglob("*.pdf")):
            return OFFICIAL
        pub = PUBLISH / "pdfs"
        if pub.is_dir() and any(pub.rglob("*.pdf")):
            return pub
    return REVIEW


def iter_viewable_pdfs() -> dict[str, Path]:
    """Map reviewRel -> file path. Official wins over review over publish/pdfs.

    Important for Render imports: section packets may exist only under _pdf_review
    until Save as official; still show them in the viewer.
    """
    found: dict[str, Path] = {}
    for root in (PUBLISH / "pdfs", REVIEW, OFFICIAL):
        if not root.is_dir():
            continue
        for pdf in root.rglob("*.pdf"):
            try:
                rel = pdf.relative_to(root).as_posix()
            except ValueError:
                continue
            found[rel] = pdf
    return found


def resolve_viewable_pdf(rel: str) -> Path | None:
    rel = (rel or "").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    for root in (OFFICIAL, REVIEW, PUBLISH / "pdfs"):
        path = root / rel
        if path.is_file():
            return path
    return None


def content_has_pdfs() -> bool:
    return bool(iter_viewable_pdfs())


def build_inventory(*, as_viewer: bool | None = None) -> dict:
    """Build PDF inventory.

    as_viewer=True uses the same official-preferring file set as Render's public
    portal (and local /view Preview). Default follows POP_MODE.
    """
    status = load_status()
    updated_set = set(_last_updated_files)
    rows = []
    viewer_lens = is_viewer() if as_viewer is None else bool(as_viewer)
    pdf_root = (
        (OFFICIAL if OFFICIAL.is_dir() and any(OFFICIAL.rglob("*.pdf")) else REVIEW)
        if viewer_lens
        else REVIEW
    )
    viewable = iter_viewable_pdfs() if viewer_lens else None
    reg_by_name = {p["name"]: p for p in load_registry().get("packets", [])}

    # Frozen publish inventory is opt-in (production snapshot). Default is a
    # live scan so Save as official shows up immediately in local viewer mode.
    use_frozen = os.environ.get("POP_FROZEN_PUBLISH", "").strip() in {"1", "true", "yes"}
    if (
        viewer_lens
        and use_frozen
        and (PUBLISH / "inventory.json").is_file()
    ):
        inv = json.loads((PUBLISH / "inventory.json").read_text(encoding="utf-8"))
        inv["mode"] = POP_MODE
        inv["editable"] = False
        return inv

    if viewable is not None:
        pdf_items = sorted(viewable.items(), key=lambda kv: kv[0].lower())
    elif pdf_root.is_dir():
        pdf_items = [
            (pdf.relative_to(pdf_root).as_posix(), pdf)
            for pdf in sorted(pdf_root.rglob("*.pdf"))
        ]
    else:
        pdf_items = []

    for rel, pdf in pdf_items:
        name = pdf.name
        section = rel.split("/")[0] if "/" in rel else "(root)"
        try:
            pages = len(PdfReader(str(pdf)).pages)
        except Exception:
            pages = 0
        st = status.get(name) or default_status_for(name)
        pkt = reg_by_name.get(name, {})
        src_rel = pkt.get("editMd", "")
        official_path = OFFICIAL / rel
        official_exists = official_path.is_file()
        official_stale = False
        review_path = REVIEW / rel
        if official_exists and review_path.is_file():
            try:
                official_stale = (
                    official_path.stat().st_mtime + 0.001 < review_path.stat().st_mtime
                )
            except OSError:
                official_stale = False
        official_at = st.get("officialAt")
        if official_exists and not official_at:
            official_at = datetime.fromtimestamp(
                official_path.stat().st_mtime
            ).astimezone().isoformat(timespec="seconds")
        atts = list_attachments(pkt) if pkt else []
        rows.append(
            {
                "file": rel,
                "name": name,
                "section": section,
                "pages": pages,
                "needContent": st.get("needContent", "Yes"),
                "level": st.get("level", "Partial"),
                "excludeFromPageTotal": name == "Other_Evidence_of_Impact.pdf",
                "sourceRel": src_rel,
                "sourceAbs": str(CONTENT_ROOT / src_rel) if src_rel else "",
                "sourceFolder": str((CONTENT_ROOT / src_rel).parent) if src_rel else "",
                "regenArg": pkt.get("regenArg", ""),
                "editable": bool(pkt.get("editable", False)) and can_edit(),
                "hasAttachments": bool(pkt.get("attachmentRoots")),
                "attachmentCount": len(atts),
                "attachments": [
                    {
                        "name": a["name"],
                        "pages": a["pages"],
                        "root": a["root"],
                        "rel": a["rel"],
                    }
                    for a in atts
                ],
                "officialExists": official_exists,
                "officialStale": official_stale,
                "officialAt": official_at,
                "updated": rel in updated_set,
            }
        )

    rows = apply_packet_order(rows)

    sections: dict[str, dict] = {}
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

    return {
        "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "source": str(pdf_root.relative_to(BASE)) if pdf_root.is_relative_to(BASE) else str(pdf_root),
        "mode": POP_MODE,
        "editable": can_edit() and not viewer_lens,
        "asViewer": viewer_lens,
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
            {
                "id": k,
                "label": k.replace("_", " "),
                "pages": sections[k]["pages"],
                "count": sections[k]["count"],
            }
            for k in sorted(sections.keys(), key=section_sort_key)
        ],
        "pdfs": rows,
        "updatedFiles": list(_last_updated_files),
    }


# ---------- routes ----------

def _login_html(
    title: str,
    action: str,
    blurb: str,
    err: bool,
    *,
    with_username: bool = False,
) -> Response:
    err_html = (
        '<p class="err">Incorrect username or password.</p>'
        if err and with_username
        else ('<p class="err">Incorrect password.</p>' if err else "")
    )
    user_field = (
        '<label>Username'
        '<input type=text name=username autocomplete=username required autofocus '
        "placeholder=\"Same as password\">"
        "</label>"
        if with_username
        else ""
    )
    pw_autofocus = "" if with_username else " autofocus"
    html = (
        "<!doctype html><html><head><meta charset=utf-8>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui;max-width:24rem;margin:4rem auto;padding:0 1rem}"
        "label{display:block;font-size:.85rem;margin-top:.65rem}"
        "input,button{font:inherit;padding:.5rem;width:100%;margin:.35rem 0;box-sizing:border-box}"
        ".err{color:#b91c1c}</style></head><body>"
        f"<h1>{title}</h1>"
        f"<p>{blurb}</p>"
        f"<form method=post action={action}>"
        f"{user_field}"
        f"<label>Password"
        f'<input type=password name=password autocomplete=current-password '
        f'placeholder=Password required{pw_autofocus}>'
        "</label>"
        "<button type=submit>Sign in</button>"
        "</form>"
        f"{err_html}"
        "</body></html>"
    )
    return Response(html, mimetype="text/html")


@app.get("/login")
def login_page():
    if POP_MODE == "edit" or not view_password_configured():
        return redirect("/")
    if viewer_auth_ok():
        return redirect("/")
    return _login_html(
        "PoP Renewal Viewer",
        "/login",
        "Enter the public access credentials (set under Admin → Site access). "
        "Use the same value for username and password. This is not the admin login.",
        bool(request.args.get("e")),
        with_username=True,
    )


@app.post("/login")
def login_post():
    if POP_MODE == "edit":
        return redirect("/")
    user = (request.form.get("username") or "").strip()
    pw = (request.form.get("password") or "").strip()
    # Username mirrors password so password managers have a username field to save.
    if user and user == pw and check_view_password(pw):
        session["view_authed"] = True
        session["authed"] = True  # back-compat
        return redirect("/")
    return redirect("/login?e=1")


@app.get("/admin/login")
def admin_login_page():
    # Local edit: /admin needs no password (mirrors Render URL shape for testing)
    if POP_MODE == "edit":
        return redirect("/admin")
    if not is_production():
        return redirect("/")
    if is_admin_session():
        return redirect("/admin")
    return _login_html(
        "PoP Admin",
        "/admin/login",
        "Enter the Render ADMIN_PASSWORD (Environment variable). "
        "This is not the public view password visitors use on /.",
        bool(request.args.get("e")),
    )


@app.post("/admin/login")
def admin_login_post():
    if POP_MODE == "edit":
        return redirect("/admin")
    if not is_production():
        return redirect("/")
    pw = (request.form.get("password") or "").strip()
    if ADMIN_PASSWORD and hmac.compare_digest(pw, ADMIN_PASSWORD):
        session["role"] = "admin"
        session["view_authed"] = True
        session["authed"] = True
        return redirect("/admin")
    return redirect("/admin/login?e=1")


PAGES = BASE / "app_pages"


def send_app_page(name: str):
    """Serve an HTML shell from app_pages/ (local vs Render separation)."""
    return send_from_directory(PAGES, name)


@app.get("/local-switcher.js")
def local_switcher_js():
    """Local-only top toggle script (no-op on Render via /api/config)."""
    return send_from_directory(PAGES, "local-switcher.js", mimetype="application/javascript")


@app.get("/logout")
def logout():
    session.clear()
    # Optional: /logout?next=/login (used by admin "test as visitor")
    nxt = (request.args.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//") and "://" not in nxt:
        return redirect(nxt)
    if is_production():
        return redirect("/login" if view_password_configured() else "/")
    if POP_MODE == "edit":
        return redirect("/edit")
    return redirect("/login" if view_password_configured() else "/")


@app.get("/")
@require_viewer_auth
def index():
    # Local edit: authoring home. Production/viewer: public portal shell.
    if POP_MODE == "edit":
        return redirect("/edit")
    return send_app_page("portal.html")


@app.get("/edit")
@require_viewer_auth
def edit_index():
    """Local-only authoring shell (Edit & Append)."""
    if POP_MODE != "edit":
        return redirect("/")
    return send_app_page("local.html")


@app.get("/admin")
@require_admin
def admin_index():
    # Local edit: local Admin tools shell. Production: hosted admin shell.
    if POP_MODE == "edit":
        return send_app_page("local.html")
    return send_app_page("hosted-admin.html")


@app.get("/preview")
@require_viewer_auth
def preview_portal():
    """Local-only: exact Render public portal page (app_pages/portal.html)."""
    if POP_MODE != "edit":
        return redirect("/")
    return send_app_page("portal.html")


@app.get("/render-admin")
@require_viewer_auth
def preview_hosted_admin():
    """Local-only: exact Render /admin page (app_pages/hosted-admin.html)."""
    if POP_MODE != "edit":
        return redirect("/admin")
    return send_app_page("hosted-admin.html")


@app.get("/view")
@require_viewer_auth
def view_index_legacy():
    """Back-compat: old local /view → /preview."""
    if POP_MODE == "edit":
        return redirect("/preview")
    return redirect("/")


@app.get("/metrics.html")
@require_viewer_auth
def metrics_html():
    # Legacy bookmark: send to the appropriate shell
    if POP_MODE == "edit":
        return redirect("/edit")
    return send_app_page("portal.html")


@app.get("/vendor/pdfjs/<path:subpath>")
@require_viewer_auth
def pdfjs_assets(subpath: str):
    """Mozilla PDF.js viewer (used so preview can default to the outline pane)."""
    return send_from_directory(BASE / "vendor" / "pdfjs", subpath)


@app.get("/api/config")
def api_config():
    return jsonify(
        {
            "mode": POP_MODE,
            "editable": can_edit(),
            "isAdmin": can_manage_viewer(),
            "isProduction": is_production(),
            "authRequired": bool(
                is_viewer()
                and view_password_configured()
                and not viewer_auth_ok()
            ),
            "authed": viewer_auth_ok(),
            "viewPasswordSet": view_password_configured(),
            "hasContent": content_has_sources(),
            "hasPdfs": content_has_pdfs(),
            "adminPath": "/admin",
            "previewPath": "/preview" if can_edit() else "/",
            "renderAdminPath": "/render-admin" if can_edit() else "/admin",
            "localSurfaces": can_edit(),
            "pdfRebuildAvailable": _pdf_toolchain_ok()[0] if can_edit() else False,
            "contentPackExportAvailable": can_edit(),
        }
    )


@app.get("/pdfs/<path:subpath>")
@require_viewer_auth
def serve_pdfs(subpath: str):
    path = resolve_viewable_pdf(subpath)
    if not path:
        return jsonify({"ok": False, "error": "PDF not found"}), 404
    return send_from_directory(path.parent, path.name)


def _safe_pdf_under(root: Path, subpath: str) -> Path | None:
    rel = (subpath or "").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    path = root / rel
    return path if path.is_file() else None


@app.get("/0_Drafts/_pdf_review/<path:subpath>")
@require_viewer_auth
def review_files(subpath: str):
    # Always serve the working preview from _pdf_review (rebuild target).
    # Do not fall through to _official — that hid fresh rebuilds after Save as official.
    path = _safe_pdf_under(REVIEW, subpath)
    if not path:
        return jsonify({"ok": False, "error": "PDF not found"}), 404
    return send_from_directory(path.parent, path.name)


@app.get("/0_Drafts/_official/<path:subpath>")
@require_viewer_auth
def official_files(subpath: str):
    path = _safe_pdf_under(OFFICIAL, subpath)
    if not path:
        return jsonify({"ok": False, "error": "PDF not found"}), 404
    return send_from_directory(path.parent, path.name)


@app.get("/api/courses")
@require_viewer_auth
def api_courses():
    folders = course_folders()
    return jsonify(
        {
            "courses": [
                {
                    "key": key,
                    "label": COURSE_LABELS[key],
                    "folder": str(folders[key].relative_to(BASE)),
                }
                for key in COURSE_LABELS
                if key in folders
            ]
        }
    )


@app.get("/api/inventory")
@require_viewer_auth
def api_inventory():
    # Local /view Preview: ?as=viewer matches Render public portal file set
    as_arg = (request.args.get("as") or "").strip().lower()
    as_viewer = True if as_arg == "viewer" else (False if as_arg == "edit" else None)
    return jsonify(build_inventory(as_viewer=as_viewer))


@app.get("/api/packet")
@require_viewer_auth
def api_packet():
    pdf = request.args.get("pdf") or ""
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    return jsonify({"ok": True, "packet": pkt, "attachments": list_attachments(pkt)})


@app.get("/api/md")
@require_edit
def api_md_get():
    pdf = request.args.get("pdf") or ""
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    path = safe_under(CONTENT_ROOT, pkt["editMd"])
    if not path.is_file():
        return jsonify({"ok": False, "error": f"Missing file: {pkt['editMd']}"}), 404
    return jsonify(
        {
            "ok": True,
            "path": pkt["editMd"],
            "content": path.read_text(encoding="utf-8"),
        }
    )


@app.put("/api/md")
@require_edit
def api_md_put():
    data = request.get_json(silent=True) or {}
    pdf = data.get("pdf") or ""
    content = data.get("content")
    if content is None:
        return jsonify({"ok": False, "error": "Missing content"}), 400
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    path = safe_under(CONTENT_ROOT, pkt["editMd"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    # Keep twin drafts copies in sync when they exist
    drafts_root = CONTENT_DRAFTS if USE_DATA_LAYOUT else DRAFTS
    twins = {
        "1_Candidate Information/1_Personal Statement/Personal_Statement.md": drafts_root
        / "1_Candidate_Information"
        / "Personal_Statement.md",
        "1_Candidate Information/4_Teaching Schedule/Pacheco_Daniel_Teaching_Schedule.md": drafts_root
        / "1_Candidate_Information"
        / "Teaching_Schedule.md",
    }
    twin = twins.get(pkt["editMd"])
    if twin:
        twin.parent.mkdir(parents=True, exist_ok=True)
        twin.write_text(content, encoding="utf-8")
    if pkt["editMd"].startswith("4_Service/"):
        # Mirror into 0_Drafts service copies by basename
        draft = drafts_root / "3_Supplemental_Materials_Service" / Path(pkt["editMd"]).name
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(content, encoding="utf-8")
    return jsonify({"ok": True, "path": pkt["editMd"], "bytes": len(content.encode("utf-8"))})


@app.get("/api/attachments")
@require_viewer_auth
def api_attachments_get():
    pdf = request.args.get("pdf") or ""
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    return jsonify({"ok": True, "attachments": list_attachments(pkt)})


@app.post("/api/attachments")
@require_edit
def api_attachments_post():
    pdf = (request.form.get("pdf") or "").strip()
    root_rel = (request.form.get("root") or "").strip()
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    roots = pkt.get("attachmentRoots") or []
    if root_rel not in roots:
        return jsonify({"ok": False, "error": "Invalid attachment root for packet"}), 400
    uploads = [f for f in request.files.getlist("file") if f and f.filename]
    if not uploads:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    dest_dir = safe_under(CONTENT_ROOT, root_rel)
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    warnings = []
    for f in uploads:
        raw = Path(f.filename).name
        safe = secure_filename(raw) or "upload.pdf"
        if dest_dir.name == "teachingexamples" and safe.endswith(".pdf") and not Path(safe).stem.endswith("_redacted"):
            safe = f"{Path(safe).stem}_redacted.pdf"
            warnings.append(f"Renamed to {safe}")
        dest = dest_dir / safe
        if dest.exists():
            warnings.append(f"Replaced {safe}")
        f.save(str(dest))
        saved.append(str(dest.relative_to(CONTENT_ROOT)).as_posix())
    # Append new files to saved order (keep existing sequence)
    key = order_key_for_packet(pkt)
    orders = load_attachment_orders()
    current_order = list(orders.get(key) or [a["rel"] for a in list_attachments(pkt)])
    for rel in saved:
        if rel not in current_order:
            current_order.append(rel)
    # Drop missing
    alive = _raw_attachment_rels(pkt)
    orders[key] = [r for r in current_order if r in alive] + sorted(
        r for r in alive if r not in current_order
    )
    save_attachment_orders(orders)
    return jsonify({"ok": True, "saved": saved, "warnings": warnings, "attachments": list_attachments(pkt)})


def _attachment_in_packet(pkt: dict, rel: str) -> Path:
    path = safe_under(CONTENT_ROOT, rel)
    for root_rel in pkt.get("attachmentRoots") or []:
        root = (CONTENT_ROOT / root_rel).resolve()
        try:
            path.resolve().relative_to(root)
            if path.is_file():
                return path
        except ValueError:
            continue
    raise FileNotFoundError("File not in packet attachment roots")


@app.delete("/api/attachments")
@require_edit
def api_attachments_delete():
    data = request.get_json(silent=True) or {}
    rel = (data.get("rel") or "").strip().replace("\\", "/")
    pdf = (data.get("pdf") or "").strip()
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    try:
        path = _attachment_in_packet(pkt, rel)
    except (FileNotFoundError, ValueError):
        return jsonify({"ok": False, "error": "File not in packet attachment roots"}), 400
    removed = path.parent / "_removed"
    removed.mkdir(parents=True, exist_ok=True)
    dest = removed / path.name
    if dest.exists():
        dest = removed / f"{path.stem}_{int(datetime.now().timestamp())}{path.suffix}"
    shutil.move(str(path), str(dest))
    # Drop from saved order + bookmark title
    orders = load_attachment_orders()
    key = order_key_for_packet(pkt)
    if key in orders:
        orders[key] = [r for r in orders[key] if r != rel and not r.endswith("/" + path.name)]
        save_attachment_orders(orders)
    bookmarks = load_attachment_bookmarks()
    if rel in bookmarks:
        del bookmarks[rel]
        save_attachment_bookmarks(bookmarks)
    return jsonify({"ok": True, "movedTo": dest.relative_to(CONTENT_ROOT).as_posix()})


def _raw_attachment_rels(pkt: dict) -> set[str]:
    """Filesystem set of attachment rels (no saved-order applied)."""
    rels: set[str] = set()
    for root_rel in pkt.get("attachmentRoots") or []:
        root = CONTENT_ROOT / root_rel
        if not root.is_dir():
            continue
        for f in root.iterdir():
            if not f.is_file() or f.name.startswith("."):
                continue
            if f.parent.name == "teachingexamples":
                if f.name != "teaching_examples.md" and not f.name.endswith("_redacted.pdf"):
                    continue
            elif not f.name.lower().endswith(".pdf"):
                continue
            rels.add(f.relative_to(CONTENT_ROOT).as_posix())
    return rels


@app.put("/api/packets/order")
@require_edit
def api_packets_order():
    """Persist display order of packets within a section."""
    data = request.get_json(silent=True) or {}
    section = (data.get("section") or "").strip()
    order = data.get("order") or []
    if not section:
        return jsonify({"ok": False, "error": "section required"}), 400
    if not isinstance(order, list):
        return jsonify({"ok": False, "error": "order must be a list of file paths"}), 400
    inv = build_inventory()
    current = {r["file"] for r in inv["pdfs"] if r["section"] == section}
    if not current:
        return jsonify({"ok": False, "error": "Unknown section"}), 404
    cleaned = []
    for rel in order:
        rel = str(rel).replace("\\", "/").strip()
        if rel in current and rel not in cleaned:
            cleaned.append(rel)
    for rel in sorted(current):
        if rel not in cleaned:
            cleaned.append(rel)
    orders = load_packet_orders()
    orders[section] = cleaned
    save_packet_orders(orders)
    return jsonify({"ok": True, "section": section, "order": cleaned})


@app.put("/api/attachments/order")
@require_edit
def api_attachments_order():
    data = request.get_json(silent=True) or {}
    pdf = (data.get("pdf") or "").strip()
    order = data.get("order") or []
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    if not isinstance(order, list):
        return jsonify({"ok": False, "error": "order must be a list of rel paths"}), 400
    current = _raw_attachment_rels(pkt)
    cleaned = []
    for rel in order:
        rel = str(rel).replace("\\", "/").strip()
        if rel in current and rel not in cleaned:
            cleaned.append(rel)
    for rel in sorted(current):
        if rel not in cleaned:
            cleaned.append(rel)
    orders = load_attachment_orders()
    orders[order_key_for_packet(pkt)] = cleaned
    save_attachment_orders(orders)
    return jsonify({"ok": True, "order": cleaned, "attachments": list_attachments(pkt)})


@app.post("/api/attachments/rename")
@require_edit
def api_attachments_rename():
    data = request.get_json(silent=True) or {}
    pdf = (data.get("pdf") or "").strip()
    rel = (data.get("rel") or "").strip().replace("\\", "/")
    new_name = (data.get("newName") or "").strip()
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    if not new_name or "/" in new_name or "\\" in new_name:
        return jsonify({"ok": False, "error": "Invalid newName"}), 400
    try:
        path = _attachment_in_packet(pkt, rel)
    except (FileNotFoundError, ValueError):
        return jsonify({"ok": False, "error": "File not in packet attachment roots"}), 400
    safe = secure_filename(new_name) or new_name
    if path.suffix and not Path(safe).suffix:
        safe = safe + path.suffix
    if (
        path.parent.name == "teachingexamples"
        and safe.endswith(".pdf")
        and not Path(safe).stem.endswith("_redacted")
        and safe != "teaching_examples.md"
    ):
        safe = f"{Path(safe).stem}_redacted.pdf"
    dest = path.parent / safe
    if dest.exists() and dest.resolve() != path.resolve():
        return jsonify({"ok": False, "error": f"A file named {safe} already exists"}), 409
    # Capture visual order before rename
    before_order = [a["rel"] for a in list_attachments(pkt)]
    path.rename(dest)
    new_rel = dest.relative_to(CONTENT_ROOT).as_posix()
    cur = _raw_attachment_rels(pkt)
    prev = [new_rel if r == rel else r for r in before_order]
    cleaned = []
    for r in prev:
        if r in cur and r not in cleaned:
            cleaned.append(r)
    for r in sorted(cur):
        if r not in cleaned:
            cleaned.append(r)
    orders = load_attachment_orders()
    orders[order_key_for_packet(pkt)] = cleaned
    save_attachment_orders(orders)
    bookmarks = load_attachment_bookmarks()
    if rel in bookmarks:
        bookmarks[new_rel] = bookmarks.pop(rel)
        save_attachment_bookmarks(bookmarks)
    return jsonify({"ok": True, "rel": new_rel, "name": dest.name, "attachments": list_attachments(pkt)})


def reveal_in_file_manager(path: Path) -> None:
    """Open Finder/Explorer/file manager showing this file or folder."""
    path = path.resolve()
    target = path if path.exists() else path.parent
    if not target.exists():
        raise FileNotFoundError(str(path))
    if sys.platform == "darwin":
        if target.is_file():
            subprocess.run(["open", "-R", str(target)], check=False)
        else:
            subprocess.run(["open", str(target)], check=False)
    elif sys.platform == "win32":
        if target.is_file():
            subprocess.run(["explorer", "/select,", str(target)], check=False)
        else:
            subprocess.run(["explorer", str(target)], check=False)
    else:
        folder = target if target.is_dir() else target.parent
        subprocess.run(["xdg-open", str(folder)], check=False)


@app.post("/api/reveal")
@require_viewer_auth
def api_reveal():
    """Reveal a review PDF or any path under BASE in the OS file manager."""
    data = request.get_json(silent=True) or {}
    pdf = (data.get("pdf") or "").strip().lstrip("/")
    rel = (data.get("rel") or "").strip().replace("\\", "/").lstrip("/")
    try:
        if pdf:
            path = (REVIEW / pdf).resolve()
            review_root = REVIEW.resolve()
            if not (str(path).startswith(str(review_root) + os.sep) or path == review_root):
                # also allow official copies
                path = (OFFICIAL / pdf).resolve()
                off_root = OFFICIAL.resolve()
                if not (str(path).startswith(str(off_root) + os.sep) or path == off_root):
                    return jsonify({"ok": False, "error": "PDF path escapes review/official"}), 400
        elif rel:
            path = safe_under(CONTENT_ROOT, rel)
        else:
            return jsonify({"ok": False, "error": "Provide pdf or rel"}), 400
        reveal_in_file_manager(path)
        return jsonify({"ok": True, "path": str(path)})
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404


@app.post("/api/attachments/bookmark")
@require_edit
def api_attachments_bookmark():
    """Set or clear the PDF outline/bookmark title for an appended file."""
    data = request.get_json(silent=True) or {}
    pdf = (data.get("pdf") or "").strip()
    rel = (data.get("rel") or "").strip().replace("\\", "/")
    title = (data.get("title") if data.get("title") is not None else "")
    if isinstance(title, str):
        title = title.strip()
    else:
        title = str(title).strip()
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    try:
        path = _attachment_in_packet(pkt, rel)
    except (FileNotFoundError, ValueError):
        return jsonify({"ok": False, "error": "File not in packet attachment roots"}), 400
    bookmarks = load_attachment_bookmarks()
    default_title = default_bookmark_title(path)
    if not title or title == default_title:
        bookmarks.pop(rel, None)
    else:
        bookmarks[rel] = title
    save_attachment_bookmarks(bookmarks)
    return jsonify(
        {
            "ok": True,
            "rel": rel,
            "bookmarkTitle": bookmarks.get(rel, default_title),
            "bookmarkCustom": rel in bookmarks,
            "defaultBookmarkTitle": default_title,
            "attachments": list_attachments(pkt),
        }
    )


@app.post("/api/status")
@require_edit
def api_status():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Missing name"}), 400
    need = data.get("needContent")
    level = data.get("level")
    if need is not None and need not in NEED_VALUES:
        return jsonify({"ok": False, "error": f"Invalid needContent: {need}"}), 400
    if level is not None and level not in LEVEL_VALUES:
        return jsonify({"ok": False, "error": f"Invalid level: {level}"}), 400
    if need is None and level is None:
        return jsonify({"ok": False, "error": "Provide needContent and/or level"}), 400
    status = load_status()
    current = status.get(name) or default_status_for(name)
    if need is not None:
        current["needContent"] = need
    if level is not None:
        current["level"] = level
    # Preserve promote timestamp when editing Need/Level
    kept = {"needContent": current["needContent"], "level": current["level"]}
    if current.get("officialAt"):
        kept["officialAt"] = current["officialAt"]
    status[name] = kept
    save_status(status)
    return jsonify({"ok": True, "name": name, **status[name]})


@app.get("/api/regen/status")
@require_viewer_auth
def api_regen_status():
    return jsonify(_regen_state)


def _dest_dir_for(kind: str, course: str) -> Path:
    if kind == "creative_work":
        return CREATIVE_WORK_DIR
    if kind in EVIDENCE_KIND_FOLDERS and EVIDENCE_KIND_FOLDERS[kind]:
        return EVIDENCE_ROOT / EVIDENCE_KIND_FOLDERS[kind]
    if course not in COURSE_LABELS:
        raise ValueError(f"Unknown course: {course}")
    folders = course_folders()
    if course not in folders:
        raise ValueError(f"Course folder not found for {course}")
    return folders[course] / COURSE_KIND_FOLDERS[kind]


def _save_one_pdf(f, dest_dir: Path, kind: str) -> tuple[str, list[str]]:
    raw_name = Path(f.filename).name
    safe = secure_filename(raw_name) or "upload.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    warnings: list[str] = []
    if kind == "teaching_examples" and not Path(safe).stem.endswith("_redacted"):
        stem = Path(safe).stem
        safe = f"{stem}_redacted.pdf"
        warnings.append(f"{raw_name}: renamed to {safe}")
    dest = dest_dir / safe
    if dest.exists():
        warnings.append(f"Replaced existing file: {safe}")
    f.save(str(dest))
    return str(dest.relative_to(CONTENT_ROOT).as_posix()), warnings


@app.post("/api/upload")
@require_edit
def api_upload():
    kind = (request.form.get("kind") or "").strip()
    course = (request.form.get("course") or "").strip()
    if kind not in COURSE_KIND_FOLDERS and kind not in EVIDENCE_KIND_FOLDERS:
        return jsonify({"ok": False, "error": f"Unknown kind: {kind}"}), 400
    uploads = [f for f in request.files.getlist("file") if f and f.filename]
    if not uploads:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    for f in uploads:
        if not f.filename.lower().endswith(".pdf"):
            return jsonify({"ok": False, "error": f"Only PDF files ({f.filename})"}), 400
    try:
        dest_dir = _dest_dir_for(kind, course)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    warnings: list[str] = []
    for f in uploads:
        rel, warn = _save_one_pdf(f, dest_dir, kind)
        saved.append(rel)
        warnings.extend(warn)
    return jsonify({"ok": True, "saved": saved, "savedAs": saved[0], "count": len(saved), "warnings": warnings})


def _pdf_toolchain_ok() -> tuple[bool, str]:
    """Rebuild needs pandoc + a LaTeX engine (xelatex). Missing on stock Render."""
    missing = []
    if shutil.which("pandoc") is None:
        missing.append("pandoc")
    if shutil.which("xelatex") is None:
        missing.append("xelatex")
    if not missing:
        return True, ""
    return False, (
        "PDF rebuild is not available on this host (missing: "
        + ", ".join(missing)
        + "). Rebuild locally with POP_MODE=edit, then run "
        "scripts/build_content_pack.sh and re-upload the zip under Import content."
    )


def _run_regen(mode: str) -> None:
    global _regen_state, _last_updated_files
    ok_tools, tool_msg = _pdf_toolchain_ok()
    if not ok_tools:
        _regen_state = {
            "running": False,
            "log": tool_msg,
            "ok": False,
            "mode": mode,
            "exitCode": 127,
            "updatedFiles": [],
        }
        return
    cmd = ["bash", str(EXPORT_SCRIPT)]
    if mode not in {"incremental", "full", ""}:
        cmd.append(mode)
    elif mode == "full":
        cmd.append("full")
    before = snapshot_review_mtimes(REVIEW)
    env = os.environ.copy()
    env["POP_CONTENT_ROOT"] = str(CONTENT_ROOT)
    env["POP_REVIEW_DIR"] = str(REVIEW)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(DRAFTS),
            capture_output=True,
            text=True,
            timeout=60 * 30,
            env=env,
        )
        log = (proc.stdout or "") + (proc.stderr or "")
        lines = [
            ln
            for ln in log.splitlines()
            if not re.search(
                r"Underfull|Overfull|Warning|Font shape|Package |wrong pointing|Ignoring wrong|Requested font|Missing character",
                ln,
            )
        ]
        updated = changed_since(before, REVIEW) if proc.returncode == 0 else []
        _last_updated_files = updated
        summary = ""
        if updated:
            summary = f"\n\nUpdated {len(updated)} PDF(s):\n" + "\n".join(f"  - {p}" for p in updated)
        elif proc.returncode == 0:
            summary = "\n\nNo review PDFs changed (everything already up to date)."
        _regen_state = {
            "running": False,
            "log": "\n".join(lines[-200:]) + summary,
            "ok": proc.returncode == 0,
            "mode": mode,
            "exitCode": proc.returncode,
            "updatedFiles": updated,
        }
    except Exception as exc:
        _regen_state = {
            "running": False,
            "log": f"Regenerate failed: {exc}",
            "ok": False,
            "mode": mode,
            "exitCode": -1,
            "updatedFiles": [],
        }


@app.post("/api/regenerate")
@require_edit
def api_regenerate():
    global _regen_state
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "incremental").strip()
    packet = (data.get("packet") or data.get("pdf") or "").strip()
    if packet:
        pkt = packet_by_pdf(packet)
        if not pkt:
            return jsonify({"ok": False, "error": "Unknown packet"}), 404
        mode = pkt.get("regenArg") or mode
    allowed = {"incremental", "full"} | {
        p.get("regenArg") for p in load_registry().get("packets", []) if p.get("regenArg")
    }
    if mode not in allowed:
        return jsonify({"ok": False, "error": f"Invalid mode/packet: {mode}"}), 400
    ok_tools, tool_msg = _pdf_toolchain_ok()
    if not ok_tools:
        return jsonify({"ok": False, "error": tool_msg}), 503
    with _regen_lock:
        if _regen_state.get("running"):
            return jsonify({"ok": False, "error": "Regenerate already running"}), 409
        _regen_state = {
            "running": True,
            "log": "Starting...\n",
            "ok": None,
            "mode": mode,
            "updatedFiles": [],
        }
    threading.Thread(target=_run_regen, args=(mode,), daemon=True).start()
    return jsonify({"ok": True, "started": True, "mode": mode})


@app.post("/api/promote")
@require_edit
def api_promote():
    data = request.get_json(silent=True) or {}
    pdf = (data.get("pdf") or "").strip().lstrip("/")
    pkt = packet_by_pdf(pdf)
    if not pkt:
        return jsonify({"ok": False, "error": "Unknown packet"}), 404
    rel = pkt["reviewRel"]
    src = REVIEW / rel
    if not src.is_file():
        return jsonify({"ok": False, "error": f"Preview PDF missing: {rel}"}), 404
    dest = OFFICIAL / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    status = load_status()
    st = status.get(pkt["name"]) or default_status_for(pkt["name"])
    st["officialAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    status[pkt["name"]] = st
    save_status(status)
    return jsonify({"ok": True, "official": str(dest), "at": st["officialAt"]})


@app.post("/api/admin/sync")
def api_admin_sync():
    """Receive publish bundle files onto persistent disk (Render) or local publish/."""
    token = request.headers.get("X-Admin-Token") or ""
    if not ADMIN_SYNC_TOKEN or token != ADMIN_SYNC_TOKEN:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    data_root = PUBLISH
    pdf_root = data_root / "pdfs"
    pdf_root.mkdir(parents=True, exist_ok=True)
    # Also mirror into OFFICIAL so viewer prefers live official copies
    OFFICIAL.mkdir(parents=True, exist_ok=True)
    saved = []
    for key in ("inventory.json", "status.json", "manifest.json"):
        f = request.files.get(key)
        if f:
            dest = data_root / key
            f.save(str(dest))
            saved.append(key)
    for key, f in request.files.items():
        if key in {"inventory.json", "status.json", "manifest.json"}:
            continue
        if not f.filename:
            continue
        rel = key  # client sends relative path as field name
        dest = pdf_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        f.save(str(dest))
        off = OFFICIAL / rel
        off.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, off)
        saved.append(rel)
    return jsonify({"ok": True, "saved": saved, "root": str(data_root)})


@app.get("/api/admin/view-password")
@require_admin
def api_view_password_get():
    return jsonify({"ok": True, "viewPasswordSet": view_password_configured()})


@app.put("/api/admin/view-password")
@require_admin
def api_view_password_put():
    data = request.get_json(silent=True) or {}
    pw = (data.get("password") or "").strip()
    if len(pw) < 4:
        return jsonify({"ok": False, "error": "Password must be at least 4 characters"}), 400
    auth = load_site_auth()
    # pbkdf2 avoids scrypt (missing on some macOS/OpenSSL builds)
    auth["viewPasswordHash"] = generate_password_hash(pw, method="pbkdf2:sha256")
    auth["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_site_auth(auth)
    return jsonify({"ok": True, "viewPasswordSet": True})


@app.delete("/api/admin/view-password")
@require_admin
def api_view_password_delete():
    auth = load_site_auth()
    auth.pop("viewPasswordHash", None)
    auth["clearedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_site_auth(auth)
    # Clearing hash falls back to VIEW_PASSWORD env if set
    return jsonify({"ok": True, "viewPasswordSet": view_password_configured()})


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> list[str]:
    """Extract zip into dest with zip-slip protection. Returns member names written."""
    dest = dest.resolve()
    written: list[str] = []
    for info in zf.infolist():
        name = info.filename
        if not name or name.endswith("/"):
            continue
        # Normalize and reject absolute / parent escapes
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe zip path: {name}")
        target = (dest / rel).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            raise ValueError(f"Zip slip blocked: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        written.append(rel.as_posix())
    return written


def _apply_content_pack_extract(stage: Path) -> dict:
    """Copy staged pack into CONTENT_ROOT / REVIEW / OFFICIAL / status files."""
    stats = {"sources": 0, "reviewPdfs": 0, "officialPdfs": 0, "meta": 0}
    CONTENT_ROOT.mkdir(parents=True, exist_ok=True)

    # Prefer nested workspace/ if pack used that layout
    src_root = stage / "workspace" if (stage / "workspace").is_dir() else stage

    for item in src_root.iterdir():
        name = item.name
        if name in {"content_pack_manifest.json", "__MACOSX"}:
            continue
        if name == "0_Drafts" and item.is_dir():
            # Split generated PDFs onto disk roots; keep other drafts under content
            for sub in item.iterdir():
                if sub.name == "_pdf_review" and sub.is_dir():
                    REVIEW.mkdir(parents=True, exist_ok=True)
                    OFFICIAL.mkdir(parents=True, exist_ok=True)
                    for pdf in sub.rglob("*.pdf"):
                        rel = pdf.relative_to(sub)
                        dest = REVIEW / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(pdf, dest)
                        stats["reviewPdfs"] += 1
                        # Fill gaps so viewers that prefer official still see packets
                        # that were rebuilt but not yet Save-as-official locally.
                        off = OFFICIAL / rel
                        if not off.is_file():
                            off.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(pdf, off)
                            stats["officialPdfs"] += 1
                elif sub.name == "_official" and sub.is_dir():
                    OFFICIAL.mkdir(parents=True, exist_ok=True)
                    for pdf in sub.rglob("*.pdf"):
                        rel = pdf.relative_to(sub)
                        dest = OFFICIAL / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(pdf, dest)
                        stats["officialPdfs"] += 1
                else:
                    dest = CONTENT_DRAFTS / sub.name
                    if sub.is_dir():
                        shutil.copytree(sub, dest, dirs_exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(sub, dest)
                    stats["sources"] += 1
            continue
        if name == "metrics_status.json" and item.is_file():
            STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, STATUS_FILE)
            stats["meta"] += 1
            continue
        dest = CONTENT_ROOT / name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
            stats["sources"] += sum(1 for _ in dest.rglob("*") if _.is_file())
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            stats["sources"] += 1
    return stats


@app.post("/api/build-content-pack")
@require_edit
def api_build_content_pack():
    """Local edit only: build a Render import zip and return it for download."""
    script = BASE / "scripts" / "build_content_pack.sh"
    if not script.is_file():
        return jsonify({"ok": False, "error": "build_content_pack.sh missing"}), 500
    out_dir = BASE / "publish"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"content-pack-{stamp}.zip"
    try:
        proc = subprocess.run(
            ["bash", str(script), str(out_path)],
            cwd=str(BASE),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60 * 20,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Content pack build timed out"}), 504
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Content pack build failed: {exc}"}), 500
    if proc.returncode != 0 or not out_path.is_file():
        detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-1500:]
        return jsonify(
            {"ok": False, "error": "Content pack build failed", "log": detail}
        ), 500
    return send_file(
        out_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=out_path.name,
    )


def _clear_directory_contents(path: Path) -> int:
    """Delete everything inside path (not the directory itself). Returns entry count."""
    if not path.is_dir():
        return 0
    removed = 0
    for child in list(path.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
        removed += 1
    return removed


def wipe_hosted_content() -> dict:
    """Remove imported content on the persistent data disk (Render).

    Keeps site_auth.json and the /data mount itself. Refuses to run against the
    local authoring tree (no PUBLISH_DATA_ROOT) so a mistaken click cannot delete
    the Mac working copy.
    """
    if not USE_DATA_LAYOUT:
        raise RuntimeError(
            "Wipe is only available when PUBLISH_DATA_ROOT is set (Render /data). "
            "It will not wipe a local authoring install."
        )
    disk = ensure_data_dirs()
    if not disk.get("writable"):
        raise RuntimeError(
            f"Persistent disk not writable at {PUBLISH}: {disk.get('error')}"
        )

    removed = {
        "workspaceEntries": _clear_directory_contents(CONTENT_ROOT),
        "reviewEntries": _clear_directory_contents(REVIEW),
        "officialEntries": _clear_directory_contents(OFFICIAL),
        "publishPdfEntries": _clear_directory_contents(PUBLISH / "pdfs"),
        "metaFiles": 0,
    }
    for name in (
        "metrics_status.json",
        "inventory.json",
        "manifest.json",
        "status.json",
    ):
        meta = PUBLISH / name
        if meta.is_file():
            meta.unlink()
            removed["metaFiles"] += 1

    ensure_data_dirs()
    return removed


@app.get("/api/admin/storage")
@require_admin
def api_admin_storage():
    status = ensure_data_dirs()
    status["officialPdfCount"] = (
        sum(1 for _ in OFFICIAL.rglob("*.pdf")) if OFFICIAL.is_dir() else 0
    )
    status["reviewPdfCount"] = (
        sum(1 for _ in REVIEW.rglob("*.pdf")) if REVIEW.is_dir() else 0
    )
    status["hasContent"] = content_has_sources()
    status["hasPdfs"] = content_has_pdfs()
    status["canWipeContent"] = bool(USE_DATA_LAYOUT)
    return jsonify({"ok": True, **status})


@app.post("/api/admin/wipe-content")
@require_admin
def api_wipe_content():
    """Delete all imported content on the Render disk. Keeps view-password auth."""
    global _import_state
    data = request.get_json(silent=True) or {}
    confirm = (data.get("confirm") or "").strip()
    if confirm != "WIPE":
        return jsonify(
            {
                "ok": False,
                "error": 'Confirmation required: send {"confirm": "WIPE"}',
            }
        ), 400
    with _import_lock:
        if _import_state.get("running"):
            return jsonify({"ok": False, "error": "Import already running"}), 409
        try:
            removed = wipe_hosted_content()
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        log = (
            "Wiped hosted content.\n"
            f"Workspace entries removed: {removed['workspaceEntries']}\n"
            f"Review PDF tree entries: {removed['reviewEntries']}\n"
            f"Official PDF tree entries: {removed['officialEntries']}\n"
            f"publish/pdfs entries: {removed['publishPdfEntries']}\n"
            f"Meta files removed: {removed['metaFiles']}\n"
            "Site access password (if set) was kept. Upload a new content pack next."
        )
        _import_state = {"running": False, "log": log, "ok": True, "wiped": removed}
    return jsonify({"ok": True, "removed": removed, "log": log})


@app.post("/api/admin/import-content")
@require_admin
def api_import_content():
    """Upload a content-pack zip (markdown + attachments [+ optional PDFs])."""
    global _import_state
    f = request.files.get("file") or request.files.get("pack")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Missing file"}), 400
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"ok": False, "error": "Upload a .zip content pack"}), 400
    wipe_first = (request.form.get("wipe") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    disk = ensure_data_dirs()
    if USE_DATA_LAYOUT and not disk.get("writable"):
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Persistent disk not writable at "
                    f"{PUBLISH}. In Render: add a Disk mounted at /data, set "
                    "PUBLISH_DATA_ROOT=/data, then Manual Deploy, then retry import. "
                    f"Detail: {disk.get('error')}"
                ),
            }
        ), 500
    with _import_lock:
        if _import_state.get("running"):
            return jsonify({"ok": False, "error": "Import already running"}), 409
        _import_state = {"running": True, "log": "Receiving upload…\n", "ok": None}

    try:
        wipe_log = ""
        if wipe_first:
            removed = wipe_hosted_content()
            wipe_log = (
                "Wiped existing content first.\n"
                f"  workspace={removed['workspaceEntries']} "
                f"review={removed['reviewEntries']} "
                f"official={removed['officialEntries']} "
                f"pdfs={removed['publishPdfEntries']} "
                f"meta={removed['metaFiles']}\n"
            )
        with tempfile.TemporaryDirectory(prefix="pop-pack-") as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "pack.zip"
            f.save(str(zip_path))
            stage = tmp_path / "stage"
            stage.mkdir()
            with zipfile.ZipFile(zip_path, "r") as zf:
                written = _safe_extract_zip(zf, stage)
            stats = _apply_content_pack_extract(stage)
            # Copy order/bookmark json if staged under 0_Drafts
            for name in (
                "attachment_order.json",
                "attachment_bookmarks.json",
                "packet_order.json",
            ):
                src = stage / "0_Drafts" / name
                if src.is_file():
                    ATTACHMENT_ORDER_FILE.parent.mkdir(parents=True, exist_ok=True)
                    dest = {
                        "attachment_order.json": ATTACHMENT_ORDER_FILE,
                        "attachment_bookmarks.json": ATTACHMENT_BOOKMARKS_FILE,
                        "packet_order.json": PACKET_ORDER_FILE,
                    }[name]
                    shutil.copy2(src, dest)
                    stats["meta"] += 1
            log = (
                wipe_log
                + f"Extracted {len(written)} zip members.\n"
                f"Sources/files: {stats['sources']}\n"
                f"Review PDFs: {stats['reviewPdfs']}\n"
                f"Official PDFs: {stats['officialPdfs']}\n"
                f"Meta files: {stats['meta']}\n"
                "Import complete."
            )
            _import_state = {"running": False, "log": log, "ok": True, "stats": stats}
            return jsonify({"ok": True, "stats": stats, "log": log})
    except Exception as exc:
        _import_state = {"running": False, "log": f"Import failed: {exc}", "ok": False}
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/admin/import-status")
@require_admin
def api_import_status():
    return jsonify(_import_state)


@app.get("/api/download/pdf")
@require_viewer_auth
def api_download_pdf():
    rel = (request.args.get("file") or "").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return jsonify({"ok": False, "error": "Invalid file"}), 400
    path = resolve_viewable_pdf(rel)
    if not path:
        return jsonify({"ok": False, "error": "PDF not found"}), 404
    return send_file(
        path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=path.name,
    )


@app.get("/api/download/all")
@require_viewer_auth
def api_download_all():
    """Zip all viewable PDFs using section-folder paths (reviewRel layout)."""
    viewable = iter_viewable_pdfs()
    if not viewable:
        return jsonify({"ok": False, "error": "No PDFs available"}), 404
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, pdf in sorted(viewable.items(), key=lambda kv: kv[0].lower()):
            parts = rel.split("/", 1)
            if len(parts) == 2:
                section, rest = parts
                section_label = section.replace("_", " ")
                arc = f"{section_label}/{rest}"
            else:
                arc = rel
            zf.write(pdf, arcname=arc)
            count += 1
    if count == 0:
        return jsonify({"ok": False, "error": "No PDFs available"}), 404
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"PoP_Renewal_PDFs_{stamp}.zip",
    )


def main() -> None:
    if USE_DATA_LAYOUT:
        CONTENT_ROOT.mkdir(parents=True, exist_ok=True)
        REVIEW.mkdir(parents=True, exist_ok=True)
        OFFICIAL.mkdir(parents=True, exist_ok=True)
    if not STATUS_FILE.is_file():
        inv = build_inventory()
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(
            json.dumps(
                {r["name"]: {"needContent": r["needContent"], "level": r["level"]} for r in inv["pdfs"]},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    OFFICIAL.mkdir(parents=True, exist_ok=True)
    print(f"PoP dashboard [{POP_MODE}] -> http://127.0.0.1:{PORT}/")
    print(f"App root: {BASE}")
    print(f"Content root: {CONTENT_ROOT}")
    print(f"PDF review: {REVIEW}")
    if POP_MODE == "edit":
        print("Local shells: /edit  /admin  |  Render test: /preview  /render-admin")
    if is_production():
        print("Production shells: / = portal.html, /admin = hosted-admin.html")
        if ADMIN_PASSWORD:
            print("Admin password protection: ON")
        else:
            print("WARNING: ADMIN_PASSWORD not set")
    if view_password_configured():
        print("View password protection: ON")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
