# 0_Drafts — 2026 PoP Renewal (FPS upload structure)

**Start here:** [`PUNCH_LIST.md`](PUNCH_LIST.md) — master checklist for draft status, evidence gaps, and export order.  
**This week:** [`WEEK_PLAN.md`](WEEK_PLAN.md) — Jul 26–31 daily 2-hour sprint (syllabi first, Sunday).

Markdown working files. Export each to PDF for upload in the Faculty Portfolio System.

Raw evidence (syllabi, letters, eval PDFs) lives in the numbered folders under `Renewal 3 (2026)/` and in `Student Evaluations/`.

## PDF export (review and upload prep)

**Always use the project script** — do not export with bare `pandoc` commands:

```bash
cd "Renewal 3 (2026)/0_Drafts"

./export_pdfs.sh            # incremental: only rebuild what changed (default)
./export_pdfs.sh full       # wipe _pdf_review and rebuild every PDF
./export_pdfs.sh 4          # rebuild only Course 4 (also: course4, 2a, course2a, …)
```

Output: `_pdf_review/` (mirrors FPS sections).

| Mode | When to use |
|---|---|
| `incremental` (default) | Day-to-day edits — skips PDFs whose sources are unchanged |
| `full` | Final packet sync, or after script/style changes that affect every PDF |
| `4` / `course4` / `2a` | You changed one course's narrative, syllabus, or teaching examples |

Single-course and incremental course rebuilds sync `2_Teaching/2_Courses/.../Course_*.md` into the supplemental copy before pandoc runs, then fold in syllabi and teaching examples.

The script uses Pandoc + XeLaTeX with:

- `markdown+autolink_bare_uris` — bare `https://...` URLs in markdown become clickable PDF links
- `colorlinks` / NavyBlue — links visible to reviewers
- 11pt Helvetica Neue, 1-inch margins

Markdown keeps bare URLs (not `[text](url)`) per project style; autolink handles PDF linking.

## Upload method: Option 1 (separate PDF per item)

Use one PDF per FPS outline bullet, consistently across **all** sections (see `../instructions.pdf`).

Each PDF should cover its outline bullet. **Gina Luttrell confirmed (course packets):** include the **latest syllabus**; other supporting content may be **embedded in the PDF or linked from it** (both OK). The current export embeds syllabi and teaching examples. Full OIRA section PDFs in `3_Assessments/` are **not** appended (Course Feedback is in FPS). Put Evidence-of-Impact files in the matching `5_Other Evidence of Impact/N_*/` folder; put Creative Work exhibits in `3_Creative Work/`.

---

## 1. Candidate Information

| Markdown | Export as PDF | Status |
|---|---|---|
| `1_Candidate_Information/CV.md` | CV PDF | Scaffold |
| `1_Candidate_Information/Personal_Statement.md` | Personal Statement PDF | Scaffold (outline only — not yet fully drafted in this repo) |
| `1_Candidate_Information/Teaching_Schedule.md` | Teaching Schedule PDF | Scaffold |

---

## 2. Supplemental Materials — Teaching

One Course Information Packet per class. Export names use em dash: `Course 1 — JNL 221.pdf`

Emerging Media Platforms is split into two packets — 2a for the residential cross-listed MND 413/613 class and 2b for the online graduate MND 613 class — because they serve different audiences on different calendars and carry separate OIRA evaluation records.

| Markdown | Export as PDF | Status |
|---|---|---|
| `2_Supplemental_Materials_Teaching/Course_1_-_JNL_221.md` | `Course 1 — JNL 221.pdf` | Scaffold |
| `2_Supplemental_Materials_Teaching/Course_2a_-_MND_413_613.md` | `Course 2a — MND 413-613 Residential.pdf` | Scaffold |
| `2_Supplemental_Materials_Teaching/Course_2b_-_MND_613_Online.md` | `Course 2b — MND 613 Online.pdf` | Scaffold |
| `2_Supplemental_Materials_Teaching/Course_3_-_MMI_680.md` | `Course 3 — MMI 680.pdf` | Scaffold |
| `2_Supplemental_Materials_Teaching/Course_4_-_MND_545.md` | `Course 4 — MND 545.pdf` | Scaffold |
| `2_Supplemental_Materials_Teaching/Course_5_-_MND_505.md` | `Course 5 — MND 505.pdf` | Scaffold |
| `2_Supplemental_Materials_Teaching/Course_6_-_MND_600.md` | `Course 6 — MND 600.pdf` | Scaffold |

COM 100 is excluded from this renewal (not in OIRA; zero-credit FYS) and is not exported.

### Teaching Examples (appended into each course's single PDF)

One PDF per course. Teaching examples are not a separate file — `build_attachments.py` folds them into that course's packet after the syllabus. Adding one needs no change to any script.

Per course folder under `2_Teaching/2_Courses/`:

| Path | Role |
|---|---|
| `teachingexamples/teaching_examples.md` | Narrative. Appended behind an "Appendix: Teaching Examples" divider. Filename must match exactly. |
| `teachingexamples/*_redacted.pdf` | Appended after that narrative, in filename order, each behind its own divider and bookmark. The filename becomes the appendix title, so name it descriptively. |
| `teachingexamples/_originals/` | Unredacted student documents. Never read by the build, never exported. |

Start from `_templates/teaching_examples_TEMPLATE.md`.

**Student privacy.** Anonymize students as Student A, Student B, and so on unless you have written consent to use a name. Put the original in `_originals/`, then redact it:

```bash
0_Drafts/redact_pdf.py teachingexamples/_originals/FILE.pdf \
  -o teachingexamples/FILE_redacted.pdf \
  --lines 3 --title "COURSE Assignment - Student A" \
  --forbid "Student Name" --forbid "handle-or-url-fragment"
```

`redact_pdf.py` rasterizes the affected page so the identifying text is genuinely removed rather than merely covered, strips link annotations, and refuses to write the file if any `--forbid` term survives. Use `--dry-run` first to check the band. A PDF in `teachingexamples/` not named `*_redacted.pdf` is reported as a warning and left out of the packet.

Current status:

| Course | Teaching examples |
|---|---|
| Course 1 — JNL 221 | Done: midterm pitch feedback plus two redacted final project grading sheets |
| Course 2a — MND 413/613 Residential | Done: Field Test assignment plus redacted graduate Field Test Report |
| Course 2b | Not provided — note in packet points to Course 1 and Course 2a |
| Course 3 — MMI 680 | Done: BasketBot final project plus instructor feedback |
| Course 4 — MND 545 | Done: two group 360 final projects (YouTube links) |
| Course 5 — MND 505 | Done: Orange Pulse section, splash screenshot, and published Election Day story |
| Course 6 — MND 600 | Not provided — note in packet points to Course 1 (JNL 221) |

Each packet includes: Course Overview, Syllabus, Student Work, Assessments, Other Course Materials.  
Evidence subfolders: `2_Teaching/2_Courses/0X_Course_#_.../`

**Embedded in FPS (do not re-upload):** Course Feedback refresh report, OIRA Numerical Ratings Summary (uploaded on your behalf).

---

## 3. Supplemental Materials — Service

| Markdown | Export as PDF | Status |
|---|---|---|
| `3_Supplemental_Materials_Service/Introduction_to_Service.md` | Introduction to Service PDF | Scaffold |
| `3_Supplemental_Materials_Service/Newhouse_School_Service.md` | Newhouse School Service PDF | Scaffold |
| `3_Supplemental_Materials_Service/Syracuse_University_Service.md` | Syracuse University Service PDF | Scaffold |
| `3_Supplemental_Materials_Service/Profession_and_Industry_Service.md` | Profession and Industry Service PDF | Scaffold |
| `3_Supplemental_Materials_Service/Community_Service.md` | Community Service PDF | Scaffold |
| `3_Supplemental_Materials_Service/Other_Service.md` | Other Service PDF | Scaffold |

You may alternatively use Rapid Reports from FPS Activities for the four activity-based service sections.

---

## 4. Supplemental — Other Evidence of Impact or Success

| Markdown | Export as PDF | Evidence folder |
|---|---|---|
| `4_Supplemental_Evidence_of_Impact/Student_Correspondence.md` | Student Correspondence PDF | `5_Other Evidence of Impact/3_Student_Correspondence/` |
| `4_Supplemental_Evidence_of_Impact/Academic_Correspondence.md` | Academic Correspondence PDF | `5_Other Evidence of Impact/4_Academic_Correspondence/` |
| `4_Supplemental_Evidence_of_Impact/Profession_and_Industry_Correspondence.md` | Profession & Industry Correspondence PDF | `5_Other Evidence of Impact/5_Profession_and_Industry_Correspondence/` |
| `4_Supplemental_Evidence_of_Impact/Other_Evidence_of_Impact.md` | Other Evidence of Impact PDF | `5_Other Evidence of Impact/7_Other_Evidence_of_Impact/` |

Unused empty categories (Citations, Media Appearances, Other Correspondence) were removed. Rule: no empty folder → no report.

---

## `_extras_still_in_instructions_pdf/`

Legacy / superseded drafts only. Advising, Internships, Thesis/Portfolio Reviews, and Other Teaching were **removed** (not used for this renewal). Canonical narratives live under `2_Teaching/`, `3_Connections to the Profession/`, and `4_Service/`.
