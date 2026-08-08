#!/usr/bin/env python3
"""Append evidence into Option-1 packet PDFs so each upload is self-contained.

Usage:
  ./build_attachments.py                 Courses + all Evidence-of-Impact categories
  ./build_attachments.py --only 4 5      Only those course keys (always re-append)
  ./build_attachments.py --evidence      Only Other Evidence of Impact packets
  ./build_attachments.py --creative-work Append exhibits into Creative Work PDF

Option 1 (instructions.pdf): one PDF per FPS bullet. Course Information Packets
include Overview + Syllabus + Student Work / teaching examples (+ Other Materials
when present). Full OIRA assessment PDFs are not re-appended (FPS Course Feedback).
Evidence is discovered from folders — add a PDF to the right folder and rebuild.

Course packet append order (per course folder under 2_Teaching/2_Courses/):
  1_Syllabi/*.pdf
  teachingexamples/teaching_examples.md (+ *_redacted.pdf)
  2_Student_Work/*.pdf
  4_Other_Course_Materials/*.pdf

OIRA / course-feedback section PDFs in 3_Assessments/ are NOT appended —
Course Feedback is embedded in FPS; do not re-upload full OIRA docs.

teachingexamples/_originals/ is never read.

Other Evidence categories: cover PDF in _pdf_review/5_Other_Evidence_of_Impact/
plus every *.pdf in the matching 5_Other Evidence of Impact/N_* folder.

Creative Work: every *.pdf in 3_Connections to the Profession/3_Creative Work/
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

SCRIPT_DIR = Path(__file__).resolve().parent
APP_ROOT = SCRIPT_DIR.parent
BASE = Path(os.environ.get("POP_CONTENT_ROOT", str(APP_ROOT))).resolve()
REVIEW_ROOT = Path(os.environ.get("POP_REVIEW_DIR", str(SCRIPT_DIR / "_pdf_review"))).resolve()
COURSES_DIR = BASE / "2_Teaching" / "2_Courses"
PACKET_DIR = REVIEW_ROOT / "2_Teaching"
EVIDENCE_PACKET_DIR = REVIEW_ROOT / "5_Other_Evidence_of_Impact"
EVIDENCE_ROOT = BASE / "5_Other Evidence of Impact"
CREATIVE_WORK_DIR = BASE / "3_Connections to the Profession" / "3_Creative Work"
CREATIVE_WORK_PACKET = (
    REVIEW_ROOT
    / "4_Connections_to_the_Profession"
    / "Pacheco_Daniel_Creative_Work.pdf"
)
_content_drafts = BASE / "0_Drafts"
ORDER_FILE = (
    _content_drafts / "attachment_order.json"
    if (_content_drafts / "attachment_order.json").is_file()
    else SCRIPT_DIR / "attachment_order.json"
)
BOOKMARKS_FILE = (
    _content_drafts / "attachment_bookmarks.json"
    if (_content_drafts / "attachment_bookmarks.json").is_file()
    else SCRIPT_DIR / "attachment_bookmarks.json"
)

# folder name under 5_Other Evidence of Impact/ -> review PDF name
# (only categories used for this renewal; empty unused categories were removed)
EVIDENCE_CATEGORIES = (
    ("3_Student_Correspondence", "Student_Correspondence.pdf"),
    ("4_Academic_Correspondence", "Academic_Correspondence.pdf"),
    ("5_Profession_and_Industry_Correspondence", "Profession_and_Industry_Correspondence.pdf"),
    ("7_Other_Evidence_of_Impact", "Other_Evidence_of_Impact.pdf"),
)
OTHER_EVIDENCE_ORDER = (
    "Experimenting_with_Emerging_Media_Platforms.pdf",
)

MARKER = "Appendix:"
EXAMPLES_DOC = "teaching_examples.md"
EXAMPLES_DIR = "teachingexamples"
EXAMPLES_LABEL = "Appendix: Teaching Examples"

PANDOC_OPTS = [
    "-f", "markdown+autolink_bare_uris",
    "--pdf-engine=xelatex",
    "-V", "geometry:margin=1in",
    "-V", "fontsize=11pt",
    "-V", "mainfont=Helvetica Neue",
    "-V", "colorlinks=true",
    "-V", "linkcolor=NavyBlue",
    "-V", "urlcolor=NavyBlue",
    "-V", "citecolor=NavyBlue",
]
DIVIDER_OPTS = PANDOC_OPTS + [
    "-V", "pagestyle=empty",
]


def packet_key(folder_name):
    m = re.match(r"\d+[a-z]?_Course_([0-9]+[a-z]?)_", folder_name)
    return m.group(1) if m else None


def find_packet(key):
    matches = sorted(PACKET_DIR.glob(f"Course {key} - *.pdf"))
    return matches[0] if matches else None


def document_title(pdf_path, fallback):
    try:
        outline = PdfReader(str(pdf_path)).outline
        while isinstance(outline, list) and outline:
            if isinstance(outline[0], list):
                outline = outline[0]
                continue
            return str(outline[0].title)
    except Exception:
        pass
    return fallback


def already_appended(pdf_path):
    reader = PdfReader(str(pdf_path))
    return any(MARKER in (page.extract_text() or "") for page in reader.pages)


def label_for(source):
    parent = source.parent.name
    if parent == "1_Syllabi":
        return "Appendix: Syllabus"
    if parent == "2_Student_Work":
        stem = source.stem.replace("_", " ").strip()
        return f"Appendix: Student Work — {stem}"
    if parent == "3_Assessments":
        # OIRA filenames are long; keep a readable slice
        stem = source.stem
        m = re.search(r"(FALL|SPRING|SUMMER)\d{2}", stem, re.I)
        term = m.group(0) if m else stem[:40]
        return f"Appendix: Assessment — {term}"
    if parent == "4_Other_Course_Materials":
        stem = source.stem.replace("_", " ").strip()
        return f"Appendix: Other Course Materials — {stem}"
    if source.name == "_teaching_examples.pdf" or source.stem == "teaching_examples":
        return EXAMPLES_LABEL
    if parent == "3_Creative Work" or parent == CREATIVE_WORK_DIR.name:
        stem = source.stem.replace("_", " ").strip()
        return f"Appendix: Exhibit — {stem}"
    stem = source.stem.replace("_redacted", "").replace("_", " ").strip()
    return f"Appendix: {stem.title()}"


def load_attachment_bookmarks():
    if not BOOKMARKS_FILE.is_file():
        return {}
    try:
        return json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def custom_bookmark_title(source, display_name, bookmarks):
    """Resolve dashboard-saved outline title for a source file."""
    if not bookmarks:
        return None
    try:
        rel = str(source.relative_to(BASE)).replace("\\", "/")
        if rel in bookmarks:
            return bookmarks[rel]
    except ValueError:
        pass
    # Temp teaching-examples PDF: match by teaching_examples.md path/name
    name = Path(display_name).name
    if name in bookmarks:
        return bookmarks[name]
    for key, title in bookmarks.items():
        if key.endswith("/" + name) or key.endswith(name):
            return title
    return None


def make_divider(tmpdir, index, label, title, source_name):
    md = tmpdir / f"divider_{index}.md"
    pdf = tmpdir / f"divider_{index}.pdf"
    md.write_text(
        "\\vspace*{2.5in}\n\n"
        f"# {label}\n\n"
        f"**{title}**\n\n"
        f"Source document: `{source_name}`\n",
        encoding="utf-8",
    )
    subprocess.run(["pandoc", str(md), "-o", str(pdf)] + DIVIDER_OPTS, check=True)
    return pdf


def append(packet_pdf, sources, tmpdir):
    title = document_title(packet_pdf, packet_pdf.stem)
    writer = PdfWriter()
    writer.append(str(packet_pdf))
    narrative_pages = len(writer.pages)
    bookmarks = load_attachment_bookmarks()

    syllabus_count = sum(1 for src, _ in sources if src.parent.name == "1_Syllabi")
    assess_count = sum(1 for src, _ in sources if src.parent.name == "3_Assessments")
    for i, (source, display_name) in enumerate(sources):
        custom = custom_bookmark_title(source, display_name, bookmarks)
        if custom:
            label = custom
        else:
            label = label_for(source)
            if syllabus_count > 1 and label == "Appendix: Syllabus":
                label = f"{label} — {source.stem}"
            if assess_count > 1 and label.startswith("Appendix: Assessment"):
                # keep term-based label from label_for
                pass
        divider = make_divider(tmpdir, f"{packet_pdf.stem}_{i}", label, title, display_name)
        bookmark_page = len(writer.pages)
        writer.append(str(divider), import_outline=False)
        writer.append(str(source), import_outline=False)
        writer.add_outline_item(label, bookmark_page)

    out = tmpdir / packet_pdf.name
    with open(out, "wb") as fh:
        writer.write(fh)
    shutil.move(str(out), str(packet_pdf))
    return narrative_pages, len(writer.pages)


def build_examples_pdf(doc, tmpdir):
    out = tmpdir / "_teaching_examples.pdf"
    subprocess.run(["pandoc", str(doc), "-o", str(out)] + PANDOC_OPTS, check=True)
    return out


def redacted_pdfs(folder):
    examples = folder / EXAMPLES_DIR
    if not examples.is_dir():
        return []
    approved, unapproved = [], []
    for pdf in sorted(examples.glob("*.pdf")):
        (approved if pdf.stem.endswith("_redacted") else unapproved).append(pdf)
    for pdf in unapproved:
        print(f"  WARNING  {pdf.relative_to(BASE)} is not named *_redacted.pdf; "
              "not appended. Redact it with redact_pdf.py or move it to _originals/.")
    return approved


def ordered_pdfs(folder, preferred=()):
    if not folder.is_dir():
        return []
    found = {p.name: p for p in folder.glob("*.pdf")}
    ordered = []
    for name in preferred:
        if name in found:
            ordered.append(found.pop(name))
    ordered.extend(found[name] for name in sorted(found))
    return ordered


def load_attachment_orders():
    if not ORDER_FILE.is_file():
        return {}
    try:
        return json.loads(ORDER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def review_rel_for(packet_pdf: Path) -> str:
    try:
        return str(packet_pdf.relative_to(REVIEW_ROOT)).replace("\\", "/")
    except ValueError:
        return packet_pdf.name


def source_match_keys(src: Path, display_name: str):
    keys = {display_name, Path(display_name).name}
    try:
        keys.add(str(src.relative_to(BASE)).replace("\\", "/"))
    except ValueError:
        pass
    return keys


def reorder_sources(sources, preferred_keys):
    """Reorder (path, display_name) pairs using dashboard-saved rel paths."""
    if not preferred_keys or not sources:
        return sources
    remaining = list(sources)
    ordered = []
    for pref in preferred_keys:
        pref = str(pref).replace("\\", "/")
        for i, (src, name) in enumerate(remaining):
            keys = source_match_keys(src, name)
            if (
                pref in keys
                or any(k.endswith("/" + Path(pref).name) for k in keys)
                or pref.endswith("/" + name)
                or pref.endswith(name)
            ):
                ordered.append(remaining.pop(i))
                break
    ordered.extend(remaining)
    return ordered


def apply_saved_order(packet_pdf: Path, sources):
    orders = load_attachment_orders()
    preferred = orders.get(review_rel_for(packet_pdf)) or orders.get(packet_pdf.name)
    return reorder_sources(sources, preferred)


def course_sources(folder, tmpdir, key):
    """Build ordered (path, display_name) list for one course packet."""
    sources = []

    syllabi = sorted((folder / "1_Syllabi").glob("*.pdf"))
    sources.extend((s, s.name) for s in syllabi)

    examples_doc = folder / EXAMPLES_DIR / EXAMPLES_DOC
    if examples_doc.exists() and examples_doc.stat().st_size > 0:
        ex_dir = tmpdir / f"ex_{key}"
        ex_dir.mkdir(exist_ok=True)
        examples_pdf = build_examples_pdf(examples_doc, ex_dir)
        sources.append((examples_pdf, EXAMPLES_DOC))
        for pdf in redacted_pdfs(folder):
            sources.append((pdf, pdf.name))

    for pdf in sorted((folder / "2_Student_Work").glob("*.pdf")):
        sources.append((pdf, pdf.name))

    # Intentionally skip 3_Assessments/*.pdf (full OIRA section reports).
    # Course Feedback / OIRA summary live in FPS; do not re-append here.

    for pdf in sorted((folder / "4_Other_Course_Materials").glob("*.pdf")):
        sources.append((pdf, pdf.name))

    return sources, bool(syllabi), examples_doc.exists() and examples_doc.stat().st_size > 0


def append_evidence_categories(tmpdir, force=False):
    """Append category folder PDFs into cover PDFs.

    When force=True (explicit --evidence after a fresh export), skip the
    already_appended guard so exhibit list changes are applied.
    """
    for folder_name, pdf_name in EVIDENCE_CATEGORIES:
        packet = EVIDENCE_PACKET_DIR / pdf_name
        folder = EVIDENCE_ROOT / folder_name
        if not packet.is_file():
            print(f"  skip   {pdf_name}: no cover PDF in _pdf_review (export first)")
            continue
        preferred = OTHER_EVIDENCE_ORDER if folder_name.startswith("7_") else ()
        sources = [(p, p.name) for p in ordered_pdfs(folder, preferred)]
        sources = apply_saved_order(packet, sources)
        if not sources:
            print(f"  skip   {pdf_name}: no PDFs in {folder_name}/")
            continue
        if not force and already_appended(packet):
            print(f"  skip   {pdf_name}: attachment already appended")
            continue
        before, after = append(packet, sources, tmpdir)
        joined = ", ".join(name for _, name in sources)
        print(f"   ok    {pdf_name}: {before} -> {after} pages (+{joined})")


def append_creative_work(tmpdir, force=False):
    """Append exhibit PDFs from the Creative Work folder into that packet.

    When force=True (explicit --creative-work after a fresh export), skip the
    already_appended guard so removing/adding exhibits is reflected.
    """
    packet = CREATIVE_WORK_PACKET
    if not packet.is_file():
        print("  skip   Creative Work: no packet PDF in _pdf_review")
        return
    sources = [(p, p.name) for p in ordered_pdfs(CREATIVE_WORK_DIR)]
    sources = apply_saved_order(packet, sources)
    if not sources:
        print("  skip   Creative Work: no exhibit PDFs found")
        return
    if not force and already_appended(packet):
        print(f"  skip   {packet.name}: attachment already appended")
        return
    before, after = append(packet, sources, tmpdir)
    joined = ", ".join(name for _, name in sources)
    print(f"   ok    {packet.name}: {before} -> {after} pages (+{joined})")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--only",
        nargs="+",
        metavar="KEY",
        help="Only these course keys (e.g. 4 2a). Always re-appends.",
    )
    ap.add_argument(
        "--evidence",
        "--other-evidence",
        action="store_true",
        dest="evidence",
        help="Append PDFs into Other Evidence of Impact category packets.",
    )
    ap.add_argument(
        "--creative-work",
        action="store_true",
        help="Append exhibit PDFs into Creative Work packet.",
    )
    args = ap.parse_args()
    only = set(args.only) if args.only else None
    exclusive = args.evidence or args.creative_work
    do_courses = only is not None or not exclusive
    do_evidence = args.evidence or (only is None and not args.creative_work)
    do_creative = args.creative_work or (only is None and not args.evidence)

    if do_courses and not PACKET_DIR.is_dir():
        sys.exit(f"No packet folder at {PACKET_DIR}. Run ./export_pdfs.sh first.")

    if do_courses:
        for stale in PACKET_DIR.glob("* Teaching Examples.pdf"):
            stale.unlink()
            print(f"  removed stale separate file: {stale.name}")

    jobs = {}
    missing_syllabus, missing_examples = [], []

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        if do_courses:
            for folder in sorted(COURSES_DIR.iterdir()):
                if not folder.is_dir():
                    continue
                key = packet_key(folder.name)
                if not key or key == "7":
                    continue
                if only is not None and key not in only:
                    continue
                packet = find_packet(key)
                if not packet:
                    print(f"  skip   Course {key}: no packet PDF in _pdf_review")
                    continue

                sources, has_syllabus, has_examples = course_sources(folder, tmpdir, key)
                sources = apply_saved_order(packet, sources)
                if not has_syllabus:
                    missing_syllabus.append(f"Course {key}")
                if not has_examples:
                    missing_examples.append(f"Course {key}")
                if sources:
                    jobs[packet] = sources

            for packet in sorted(jobs):
                if only is None and already_appended(packet):
                    print(f"  skip   {packet.name}: attachment already appended")
                    continue
                before, after = append(packet, jobs[packet], tmpdir)
                joined = ", ".join(name for _, name in jobs[packet])
                print(f"   ok    {packet.name}: {before} -> {after} pages (+{joined})")

        if do_evidence:
            # force only when explicitly requested (fresh cover export); otherwise skip if already appended
            append_evidence_categories(tmpdir, force=args.evidence)

        if do_creative:
            append_creative_work(tmpdir, force=args.creative_work)

    if missing_syllabus:
        print("\nNo syllabus on file: " + ", ".join(missing_syllabus))
    if missing_examples:
        print("No teaching examples yet: " + ", ".join(missing_examples))


if __name__ == "__main__":
    main()
