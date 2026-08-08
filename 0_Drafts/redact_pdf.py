#!/usr/bin/env python3
"""Anonymize a student document before it goes into the renewal packet.

Drawing a white box over a name is NOT a redaction: the original text stays in
the PDF's text layer and remains selectable, searchable and extractable, and
URLs survive as clickable link annotations. Both modes below actually remove
the underlying content, and the result is verified before it is written.

Two modes, which can be combined in one run:

  --scrub "OLD" / --scrub "OLD=NEW"
      Removes every occurrence of a term anywhere in the document and
      optionally stamps a replacement in its place. The rest of the page keeps
      its searchable text, so this is the better choice for prose documents
      such as papers. Repeat the flag for each term.

  --lines N --title "..."
      For a header block of name and URLs sitting above the content, where the
      lines are easier to identify by position than by string. Rasterizes the
      page, which discards its text layer, paints over the top N text blocks
      and stamps a replacement title. Use when identifiers are dense and mixed,
      as on a grading sheet. The band is measured from the rendered page rather
      than hardcoded.

Always pass --forbid terms: the tool refuses to write the file if any of them
survive in the extracted text, the raw bytes, a decompressed stream or a link
annotation. Use --dry-run first to preview what would be removed.

Examples:
  ./redact_pdf.py paper.pdf --scrub "Jacob Spudich=Student A" \\
      --scrub "Spudich, Jacob=Student A" --forbid Spudich

  ./redact_pdf.py sheet.pdf --lines 3 --title "JNL 221 Final - Student A" \\
      --forbid Maddy --forbid madzyc
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

DPI = 200
PAD = 12
FONT = "helv"
DESCENDER = 0.21  # Helvetica descender as a fraction of font size
FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def rasterize(source, page_number, tmpdir):
    png = tmpdir / "page.png"
    subprocess.run(
        ["gs", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", f"-r{DPI}",
         f"-dFirstPage={page_number}", f"-dLastPage={page_number}",
         f"-sOutputFile={png}", str(source)],
        check=True, capture_output=True,
    )
    return png


def ink_blocks(image):
    """Vertical runs of rows containing ink, as (top, bottom, left, right)."""
    arr = np.array(image.convert("L"))
    mask = arr < 200
    has_ink = mask.any(axis=1)
    blocks, start = [], None
    for y, inked in enumerate(has_ink):
        if inked and start is None:
            start = y
        elif not inked and start is not None:
            cols = np.where(mask[start:y].any(axis=0))[0]
            blocks.append((start, y - 1, int(cols.min()), int(cols.max())))
            start = None
    if start is not None:
        cols = np.where(mask[start:].any(axis=0))[0]
        blocks.append((start, len(has_ink) - 1, int(cols.min()), int(cols.max())))
    return blocks


def fit_font(text, target_height):
    for path in FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        size = max(target_height, 10)
        while size > 8:
            try:
                font = ImageFont.truetype(path, size)
            except OSError:
                break
            box = font.getbbox(text)
            if box[3] - box[1] <= target_height:
                return font
            size -= 1
    return ImageFont.load_default()


def redact_page(png, lines, title):
    im = Image.open(png).convert("RGB")
    blocks = ink_blocks(im)
    if len(blocks) <= lines:
        sys.exit(
            f"Page has only {len(blocks)} text blocks; --lines {lines} would remove "
            "everything. Inspect the page and lower --lines."
        )

    first = blocks[0]
    band_top = max(0, first[0] - PAD)
    band_bottom = blocks[lines - 1][1] + PAD
    keep_top = blocks[lines][0]
    if band_bottom >= keep_top:
        sys.exit("Redaction band would overlap content that must be kept.")

    draw = ImageDraw.Draw(im)
    draw.rectangle([0, band_top, im.width, band_bottom], fill="white")
    if title:
        font = fit_font(title, first[1] - first[0])
        draw.text((first[2], first[0]), title, fill="black", font=font)

    print(f"  redacting rows {band_top}-{band_bottom} of {im.height}; "
          f"first kept content at row {keep_top}")
    return im


def parse_scrub(spec):
    old, sep, new = spec.partition("=")
    return old.strip(), (new.strip() if sep else "")


def scrub_terms(source, dest, specs, dry_run):
    """Remove every occurrence of each term, optionally stamping a replacement."""
    doc = pymupdf.open(str(source))
    total, replacements = 0, []

    for spec in specs:
        old, new = parse_scrub(spec)
        found = 0
        for number, page in enumerate(doc):
            for rect in page.search_for(old):
                found += 1
                if dry_run:
                    continue
                # A redaction annotation physically deletes the covered content
                # when applied; a drawn rectangle would only hide it.
                page.add_redact_annot(rect, fill=(1, 1, 1))
                if new:
                    replacements.append((number, rect, new))
        if not found:
            sys.exit(f"Term not found in document, so nothing was removed: {old!r}")
        print(f"  scrub {old!r} -> {new or '(removed)'}: {found} occurrence(s)")
        total += found

    if dry_run:
        doc.close()
        return None

    for page in doc:
        page.apply_redactions()
    # Stamped separately: add_redact_annot's own text argument silently declines
    # to draw into a rect only as tall as one line.
    for number, rect, new in replacements:
        size = min(rect.height * 0.85,
                   rect.width / pymupdf.get_text_length(new, FONT, 1))
        doc[number].insert_text(
            (rect.x0, rect.y1 - size * DESCENDER), new,
            fontname=FONT, fontsize=size, color=(0, 0, 0),
        )

    doc.save(str(dest), garbage=4, deflate=True, clean=True)
    doc.close()
    return total


def strip_matching_links(path, forbidden):
    """Drop link annotations whose target contains a forbidden term."""
    if not forbidden:
        return
    doc = pymupdf.open(str(path))
    removed = 0
    for page in doc:
        for link in page.get_links():
            uri = link.get("uri") or ""
            if uri and any(t.lower() in uri.lower() for t in forbidden):
                page.delete_link(link)
                removed += 1
    if removed:
        doc.saveIncr()
        print(f"  removed {removed} link annotation(s) pointing at forbidden terms")
    doc.close()


def verify(path, forbidden):
    reader = PdfReader(str(path))
    text = "\n".join(p.extract_text() or "" for p in reader.pages).lower()
    raw = path.read_bytes().lower()
    streams = b""
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        try:
            streams += zlib.decompress(match.group(1))
        except zlib.error:
            continue  # not a deflate stream, or already covered by the raw scan
    streams = streams.lower()
    hits = sorted({t for t in forbidden
                   if t.lower() in text
                   or t.lower().encode() in raw
                   or t.lower().encode() in streams})
    if hits:
        path.unlink(missing_ok=True)
        sys.exit(f"FAILED: identifying text survived redaction: {', '.join(hits)}")
    annots = sum(1 for p in reader.pages if "/Annots" in p)
    print(f"  verified: no forbidden term in text, raw bytes or compressed "
          f"streams; {annots} page(s) with annotations")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--output", type=Path,
                    help="default: <source stem>_redacted.pdf alongside the source")
    ap.add_argument("--scrub", action="append", default=[], metavar="OLD[=NEW]",
                    help="remove a term everywhere it appears, optionally "
                         "stamping a replacement; repeatable")
    ap.add_argument("--page", type=int, default=1,
                    help="page holding the identifying header block (default 1)")
    ap.add_argument("--lines", type=int, default=0,
                    help="number of leading text blocks to remove by "
                         "rasterizing --page; omit to leave the page alone")
    ap.add_argument("--title", default="",
                    help="replacement title stamped where the first line was")
    ap.add_argument("--forbid", action="append", default=[],
                    help="term that must not survive; repeatable")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be removed and render the band to "
                         "PNG for review, without writing a PDF")
    args = ap.parse_args()

    if not args.source.exists():
        sys.exit(f"Source not found: {args.source}")
    if not args.scrub and not args.lines:
        sys.exit("Nothing to do: pass --scrub and/or --lines. See --help.")
    output = args.output or args.source.with_name(args.source.stem + "_redacted.pdf")

    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        working = args.source

        if args.scrub:
            scrubbed = tmpdir / "scrubbed.pdf"
            scrub_terms(working, scrubbed, args.scrub, args.dry_run)
            if not args.dry_run:
                working = scrubbed

        if args.lines:
            image = redact_page(rasterize(working, args.page, tmpdir),
                                args.lines, args.title)
            if args.dry_run:
                preview = output.with_suffix(".preview.png")
                image.save(preview)
                print(f"  dry run: wrote {preview}")
                return

            page_pdf = tmpdir / "page.pdf"
            image.save(page_pdf, "PDF", resolution=DPI)
            writer = PdfWriter()
            original = PdfReader(str(working))
            for index in range(len(original.pages)):
                if index == args.page - 1:
                    writer.append(str(page_pdf))
                else:
                    writer.add_page(original.pages[index])
            # The rasterized page's own annotations are meaningless now, and any
            # link elsewhere in a graded document is likely to carry the
            # student's URL, so drop them all in this mode.
            for page in writer.pages:
                if "/Annots" in page:
                    del page["/Annots"]
            with open(output, "wb") as fh:
                writer.write(fh)
        else:
            if args.dry_run:
                print("  dry run: no PDF written")
                return
            shutil.copy(working, output)

    if args.scrub and not args.lines:
        strip_matching_links(output, args.forbid)

    print(f"  wrote {output.name} ({len(PdfReader(str(output)).pages)} pages)")
    if args.forbid:
        verify(output, args.forbid)
    else:
        print("  note: no --forbid terms given, so nothing was verified")


if __name__ == "__main__":
    main()
