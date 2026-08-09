#!/usr/bin/env bash
# Export submission markdown drafts to review PDFs.
#
# Usage:
#   ./export_pdfs.sh              Incremental: rebuild only what changed (default)
#   ./export_pdfs.sh full         Wipe _pdf_review and rebuild everything
#   ./export_pdfs.sh 4            Rebuild only Course 4 (also: course4, 2a, course2a, …)
#                                 Course keys: 1, 2a, 2b, 3, 4, 5, 6 (COM 100 excluded)
#   ./export_pdfs.sh incremental  Same as default
#
# Output: 0_Drafts/_pdf_review/ (mirrors FPS upload sections)
#
# Required Pandoc settings (do not omit when exporting):
#   -f markdown+autolink_bare_uris   bare https:// URLs -> clickable links
#   --pdf-engine=xelatex
#   -V colorlinks=true + NavyBlue url/link colors
# See 0_Drafts/README.md and .cursor/rules/pdf-export.mdc

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# On Render / data-layout hosts: content and review PDFs live on persistent disk
BASE="${POP_CONTENT_ROOT:-$APP_ROOT}"
OUT="${POP_REVIEW_DIR:-$SCRIPT_DIR/_pdf_review}"
COURSES_DIR="$BASE/2_Teaching/2_Courses"
# Supplemental drafts: prefer content-root copy when present (imported pack)
if [[ -d "$BASE/0_Drafts/2_Supplemental_Materials_Teaching" ]]; then
  SUPP_TEACHING="$BASE/0_Drafts/2_Supplemental_Materials_Teaching"
else
  SUPP_TEACHING="$SCRIPT_DIR/2_Supplemental_Materials_Teaching"
fi

# Canonical narrative sources (Final Edit: Edit UI + export share these paths)
PS_MD="$BASE/1_Candidate Information/1_Personal Statement/Personal_Statement.md"
SCHEDULE_MD="$BASE/1_Candidate Information/4_Teaching Schedule/Pacheco_Daniel_Teaching_Schedule.md"
INTRO_TEACHING_MD="$BASE/2_Teaching/1_Introduction to Teaching/Introduction_to_Teaching.md"
SERVICE_DIR="$BASE/4_Service"
CONN_DIR="$BASE/3_Connections to the Profession"
if [[ -d "$BASE/0_Drafts/4_Supplemental_Evidence_of_Impact" ]]; then
  EVIDENCE_MD_DIR="$BASE/0_Drafts/4_Supplemental_Evidence_of_Impact"
else
  EVIDENCE_MD_DIR="$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact"
fi

PANDOC_OPTS=(
  -f markdown+autolink_bare_uris
  --pdf-engine=xelatex
  -V geometry:margin=1in
  -V fontsize=11pt
  -V mainfont="Helvetica Neue"
  -V colorlinks=true
  -V linkcolor=NavyBlue
  -V urlcolor=NavyBlue
  -V citecolor=NavyBlue
)

MODE="incremental"
COURSE_KEY=""
PACKET_KEY=""

usage() {
  cat <<'EOF'
Usage: ./export_pdfs.sh [full|incremental|COURSE|PACKET]

  (no args) / incremental   Rebuild only PDFs whose sources are newer than the output
  full                      Wipe _pdf_review and rebuild every PDF
  4 / course4 / 2a / …       Rebuild only that course packet (narrative + syllabus + teaching examples)
  ps / cv / schedule / …    Rebuild a single non-course packet (see packet_registry.json regenArg)

Examples:
  ./export_pdfs.sh
  ./export_pdfs.sh full
  ./export_pdfs.sh 4
  ./export_pdfs.sh ps
  ./export_pdfs.sh student-corr
EOF
}

# --- parse args ---
if [[ $# -gt 1 ]]; then
  usage; exit 1
fi
if [[ $# -eq 1 ]]; then
  case "$1" in
    -h|--help|help) usage; exit 0 ;;
    full|--all|-a) MODE="full" ;;
    incremental|--incremental|-i) MODE="incremental" ;;
    cv|ps|schedule|intro-teaching|service-intro|service-newhouse|service-su|service-profession|service-community|service-other|contrib|exec|creative|connections|plan|student-corr|academic-corr|profession-corr|other-evidence)
      MODE="packet"
      PACKET_KEY="$1"
      ;;
    course[0-9]*|[0-9]*[a-z]|[0-9]*)
      MODE="course"
      COURSE_KEY="${1#course}"
      COURSE_KEY="${COURSE_KEY#Course}"
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
fi

export_pdf() {
  local src="$1"
  local dest_dir="$2"
  local pdf_name="$3"
  mkdir -p "$dest_dir"
  echo "Exporting $pdf_name ..."
  pandoc "$src" -o "$dest_dir/$pdf_name" "${PANDOC_OPTS[@]}"
}

# Styled CV (altcv pipeline): markdown -> HTML/CSS -> Chrome PDF -> stamped header/footer
CV_DIR="$BASE/1_Candidate Information/2_Curriculum Vitae"
CV_MD="$CV_DIR/Pacheco_Daniel_CV.md"
CV_PDF_NAME="Pacheco_Daniel_CV.pdf"

export_cv_pdf() {
  local dest_dir="$1"
  mkdir -p "$dest_dir"
  echo "Exporting $CV_PDF_NAME (styled CV pipeline) ..."
  (
    cd "$CV_DIR"
    ./build_cv_pdf.sh
  )
  # build_cv_pdf.sh already writes to _pdf_review/1_Candidate_Information/
  if [[ ! -f "$dest_dir/$CV_PDF_NAME" ]]; then
    echo "ERROR: styled CV build did not produce $dest_dir/$CV_PDF_NAME" >&2
    exit 1
  fi
}

# True if any source is newer than dest, or dest is missing.
needs_rebuild() {
  local dest="$1"
  shift
  if [[ ! -f "$dest" ]]; then
    return 0
  fi
  local src
  for src in "$@"; do
    [[ -e "$src" ]] || continue
    if [[ "$src" -nt "$dest" ]]; then
      return 0
    fi
  done
  return 1
}

# Sync course-folder Course_*.md -> supplemental copy used by pandoc.
sync_course_md() {
  local key="$1"
  local folder
  folder="$(find_course_folder "$key")" || return 1
  local src
  src="$(ls "$folder"/Course_*.md 2>/dev/null | head -1)"
  if [[ -z "$src" ]]; then
    echo "  WARNING  Course $key: no Course_*.md in $folder" >&2
    return 1
  fi
  local dest_name
  dest_name="$(basename "$src")"
  local dest="$SUPP_TEACHING/$dest_name"
  mkdir -p "$SUPP_TEACHING"
  if [[ ! -f "$dest" ]] || [[ "$src" -nt "$dest" ]]; then
    cp "$src" "$dest"
    echo "  synced  $dest_name <- course folder" >&2
  fi
  # Path only on stdout (captured by callers)
  printf '%s\n' "$dest"
}

find_course_folder() {
  local key="$1"
  local d
  # Prefer exact Course_<key>_ segment: 01_Course_1_..., 02a_Course_2a_...
  for d in "$COURSES_DIR"/*; do
    [[ -d "$d" ]] || continue
    if [[ "$(basename "$d")" =~ ^[0-9]+[a-z]?_Course_${key}_ ]]; then
      echo "$d"
      return 0
    fi
  done
  return 1
}

course_pdf_name() {
  case "$1" in
    1)  echo "Course 1 - JNL 221.pdf" ;;
    2a) echo "Course 2a - MND 413-613 Residential.pdf" ;;
    2b) echo "Course 2b - MND 613 Online.pdf" ;;
    3)  echo "Course 3 - MMI 680.pdf" ;;
    4)  echo "Course 4 - MND 545.pdf" ;;
    5)  echo "Course 5 - MND 505.pdf" ;;
    6)  echo "Course 6 - MND 600.pdf" ;;
    *)  return 1 ;;
  esac
}

# Collect dependency paths for a course key (stdout, one per line).
course_sources() {
  local key="$1"
  local folder md
  folder="$(find_course_folder "$key")" || return 1
  md="$(ls "$folder"/Course_*.md 2>/dev/null | head -1)"
  [[ -n "$md" ]] && echo "$md"
  local supp="$SUPP_TEACHING/$(basename "$md")"
  [[ -f "$supp" ]] && echo "$supp"
  # syllabi + teaching examples (exclude _originals)
  find "$folder/1_Syllabi" -name "*.pdf" 2>/dev/null || true
  [[ -f "$folder/teachingexamples/teaching_examples.md" ]] && echo "$folder/teachingexamples/teaching_examples.md"
  find "$folder/teachingexamples" -maxdepth 1 -name "*_redacted.pdf" 2>/dev/null || true
  find "$folder/2_Student_Work" -name "*.pdf" 2>/dev/null || true
  # 3_Assessments (full OIRA section PDFs) intentionally not watched/appended —
  # Course Feedback is embedded in FPS.
  find "$folder/4_Other_Course_Materials" -name "*.pdf" 2>/dev/null || true
}

# --- encoding preflight (scoped when possible) ---
echo "Checking source encoding ..."
python3 - "$SCRIPT_DIR" "$BASE" "$MODE" "$COURSE_KEY" "$PACKET_KEY" <<'PYEOF'
import json, pathlib, sys
fatal, warn = [], []
drafts, base = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
mode, course_key, packet_key = sys.argv[3], sys.argv[4], sys.argv[5]
SKIP_PARTS = {"FINAL_EDIT_README.md", "viewer", "publish", "Examples_other_profs", "node_modules"}

def skip(path: pathlib.Path) -> bool:
    parts = set(path.parts) | {path.name}
    return bool(parts & SKIP_PARTS)

paths = []
if mode == "course" and course_key:
    courses = base / "2_Teaching" / "2_Courses"
    for d in courses.glob(f"*Course_{course_key}_*"):
        paths.extend(d.rglob("*.md"))
    supp = drafts / "2_Supplemental_Materials_Teaching"
    paths.extend(supp.glob(f"Course_{course_key}_*.md"))
    paths.extend(supp.glob(f"Course_*_{course_key}_*.md"))
elif mode == "packet" and packet_key:
    reg = drafts / "packet_registry.json"
    if reg.is_file():
        for p in json.loads(reg.read_text(encoding="utf-8")).get("packets", []):
            if p.get("regenArg") == packet_key and p.get("editMd"):
                paths.append(base / p["editMd"])
    # course-style packet keys (1, 2a, ...) may not appear as regenArg-only; registry covers them
else:
    # Content trees only — not every .md under the Final Edit root
    for root in (
        drafts,
        base / "1_Candidate Information",
        base / "2_Teaching",
        base / "3_Connections to the Profession",
        base / "4_Service",
        base / "5_Other Evidence of Impact",
    ):
        if root.is_dir():
            paths.extend(root.rglob("*.md"))

seen = set()
for md in paths:
    md = pathlib.Path(md)
    if md in seen or not md.is_file() or skip(md):
        continue
    seen.add(md)
    raw = md.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fatal.append(md)
        continue
    if "\ufffd" in text:
        warn.append(md)
for path in sorted(set(warn)):
    print(f"  warning: replacement characters in {path}")
if fatal:
    print("Cannot export (see .cursor/rules/no-emoji-markdown.mdc):")
    for path in sorted(set(fatal)):
        print(f"  not UTF-8: {path}")
    sys.exit(1)
PYEOF

REBUILT_COURSES=()
REBUILT_EVIDENCE_PDFS=()
REBUILT_CREATIVE_WORK=0

rebuild_course() {
  local key="$1"
  local pdf_name dest folder md_path
  pdf_name="$(course_pdf_name "$key")" || {
    echo "Unknown course key: $key (try 1, 2a, 2b, 3, 4, 5, 6)" >&2
    exit 1
  }
  dest="$OUT/2_Teaching/$pdf_name"
  mkdir -p "$OUT/2_Teaching"

  md_path="$(sync_course_md "$key")" || {
    echo "Cannot find course folder for key $key" >&2
    exit 1
  }

  # Fresh narrative PDF (overwrites any prior appended packet)
  export_pdf "$md_path" "$OUT/2_Teaching" "$pdf_name"
  REBUILT_COURSES+=("$key")
}

# --- mode dispatch ---
case "$MODE" in
  full)
    echo "Mode: full rebuild"
    rm -rf "$OUT"
    mkdir -p "$OUT"

    CI="$OUT/1_Candidate_Information"
    export_cv_pdf "$CI"
    export_pdf "$PS_MD" "$CI" "Pacheco_Daniel_Personal_Statement.pdf"
    export_pdf "$SCHEDULE_MD" "$CI" "Pacheco_Daniel_Teaching_Schedule.pdf"

    TE="$OUT/2_Teaching"
    for key in 1 2a 2b 3 4 5 6; do
      rebuild_course "$key"
    done
    export_pdf "$BASE/2_Teaching/1_Introduction to Teaching/Introduction_to_Teaching.md" "$TE" "Pacheco_Daniel_Introduction_to_Teaching.pdf"

    SV="$OUT/3_Service"
    export_pdf "$SERVICE_DIR/1_Introduction to Service/Introduction_to_Service.md" "$SV" "Pacheco_Daniel_Introduction_to_Service.pdf"
    export_pdf "$SERVICE_DIR/2_Newhouse School/Newhouse_School_Service.md" "$SV" "Pacheco_Daniel_Newhouse_School_Service.pdf"
    export_pdf "$SERVICE_DIR/3_Syracuse University/Syracuse_University_Service.md" "$SV" "Pacheco_Daniel_Syracuse_University_Service.pdf"
    export_pdf "$SERVICE_DIR/4_Profession and Industry/Profession_and_Industry_Service.md" "$SV" "Pacheco_Daniel_Profession_and_Industry_Service.pdf"
    export_pdf "$SERVICE_DIR/5_Community/Community_Service.md" "$SV" "Pacheco_Daniel_Community_Service.pdf"
    export_pdf "$SERVICE_DIR/6_Other Service/Other_Service.md" "$SV" "Pacheco_Daniel_Other_Service.pdf"

    CN="$OUT/4_Connections_to_the_Profession"
    export_pdf "$BASE/3_Connections to the Profession/1_Professional Contributions Document/Professional_Contributions_Document.md" "$CN" "Pacheco_Daniel_Professional_Contributions_Document.pdf"
    export_pdf "$BASE/3_Connections to the Profession/2_Executive Summary of Engagement/Executive_Summary_of_Engagement.md" "$CN" "Pacheco_Daniel_Executive_Summary_of_Engagement.pdf"
    export_pdf "$BASE/3_Connections to the Profession/3_Creative Work/Creative_Work.md" "$CN" "Pacheco_Daniel_Creative_Work.pdf"
    REBUILT_CREATIVE_WORK=1
    export_pdf "$BASE/3_Connections to the Profession/4_Professional Connections/Professional_Connections.md" "$CN" "Pacheco_Daniel_Professional_Connections.pdf"
    export_pdf "$BASE/3_Connections to the Profession/5_Professional Plan of Action/Professional_Plan_of_Action.md" "$CN" "Pacheco_Daniel_Plan_of_Action.pdf"

    EV="$OUT/5_Other_Evidence_of_Impact"
    export_pdf "$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact/Student_Correspondence.md" "$EV" "Student_Correspondence.pdf"
    export_pdf "$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact/Academic_Correspondence.md" "$EV" "Academic_Correspondence.pdf"
    export_pdf "$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact/Profession_and_Industry_Correspondence.md" "$EV" "Profession_and_Industry_Correspondence.pdf"
    export_pdf "$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact/Other_Evidence_of_Impact.md" "$EV" "Other_Evidence_of_Impact.pdf"
    REBUILT_EVIDENCE_PDFS+=(
      "Student_Correspondence.pdf"
      "Academic_Correspondence.pdf"
      "Profession_and_Industry_Correspondence.pdf"
      "Other_Evidence_of_Impact.pdf"
    )
    ;;

  course)
    echo "Mode: single course ($COURSE_KEY)"
    mkdir -p "$OUT"
    rebuild_course "$COURSE_KEY"
    ;;

  packet)
    echo "Mode: single packet ($PACKET_KEY)"
    mkdir -p "$OUT"
    case "$PACKET_KEY" in
      cv)
        export_cv_pdf "$OUT/1_Candidate_Information"
        ;;
      ps)
        export_pdf "$PS_MD" "$OUT/1_Candidate_Information" "Pacheco_Daniel_Personal_Statement.pdf"
        ;;
      schedule)
        export_pdf "$SCHEDULE_MD" "$OUT/1_Candidate_Information" "Pacheco_Daniel_Teaching_Schedule.pdf"
        ;;
      intro-teaching)
        export_pdf "$INTRO_TEACHING_MD" "$OUT/2_Teaching" "Pacheco_Daniel_Introduction_to_Teaching.pdf"
        ;;
      service-intro)
        export_pdf "$SERVICE_DIR/1_Introduction to Service/Introduction_to_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Introduction_to_Service.pdf"
        ;;
      service-newhouse)
        export_pdf "$SERVICE_DIR/2_Newhouse School/Newhouse_School_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Newhouse_School_Service.pdf"
        ;;
      service-su)
        export_pdf "$SERVICE_DIR/3_Syracuse University/Syracuse_University_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Syracuse_University_Service.pdf"
        ;;
      service-profession)
        export_pdf "$SERVICE_DIR/4_Profession and Industry/Profession_and_Industry_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Profession_and_Industry_Service.pdf"
        ;;
      service-community)
        export_pdf "$SERVICE_DIR/5_Community/Community_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Community_Service.pdf"
        ;;
      service-other)
        export_pdf "$SERVICE_DIR/6_Other Service/Other_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Other_Service.pdf"
        ;;
      contrib)
        export_pdf "$CONN_DIR/1_Professional Contributions Document/Professional_Contributions_Document.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Professional_Contributions_Document.pdf"
        ;;
      exec)
        export_pdf "$CONN_DIR/2_Executive Summary of Engagement/Executive_Summary_of_Engagement.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Executive_Summary_of_Engagement.pdf"
        ;;
      creative)
        export_pdf "$CONN_DIR/3_Creative Work/Creative_Work.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Creative_Work.pdf"
        REBUILT_CREATIVE_WORK=1
        ;;
      connections)
        export_pdf "$CONN_DIR/4_Professional Connections/Professional_Connections.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Professional_Connections.pdf"
        ;;
      plan)
        export_pdf "$CONN_DIR/5_Professional Plan of Action/Professional_Plan_of_Action.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Plan_of_Action.pdf"
        ;;
      student-corr)
        export_pdf "$EVIDENCE_MD_DIR/Student_Correspondence.md" "$OUT/5_Other_Evidence_of_Impact" "Student_Correspondence.pdf"
        REBUILT_EVIDENCE_PDFS+=("Student_Correspondence.pdf")
        ;;
      academic-corr)
        export_pdf "$EVIDENCE_MD_DIR/Academic_Correspondence.md" "$OUT/5_Other_Evidence_of_Impact" "Academic_Correspondence.pdf"
        REBUILT_EVIDENCE_PDFS+=("Academic_Correspondence.pdf")
        ;;
      profession-corr)
        export_pdf "$EVIDENCE_MD_DIR/Profession_and_Industry_Correspondence.md" "$OUT/5_Other_Evidence_of_Impact" "Profession_and_Industry_Correspondence.pdf"
        REBUILT_EVIDENCE_PDFS+=("Profession_and_Industry_Correspondence.pdf")
        ;;
      other-evidence)
        export_pdf "$EVIDENCE_MD_DIR/Other_Evidence_of_Impact.md" "$OUT/5_Other_Evidence_of_Impact" "Other_Evidence_of_Impact.pdf"
        REBUILT_EVIDENCE_PDFS+=("Other_Evidence_of_Impact.pdf")
        ;;
      *)
        echo "Unknown packet key: $PACKET_KEY" >&2
        exit 1
        ;;
    esac
    ;;

  incremental)
    echo "Mode: incremental (only changed sources)"
    mkdir -p "$OUT"

    # Non-course jobs: src -> dest_dir/pdf_name
    maybe_export() {
      local src="$1" dest_dir="$2" pdf_name="$3"
      local dest="$dest_dir/$pdf_name"
      if needs_rebuild "$dest" "$src"; then
        export_pdf "$src" "$dest_dir" "$pdf_name"
      else
        echo "  skip   $pdf_name (up to date)"
      fi
    }

    CV_DEST="$OUT/1_Candidate_Information/$CV_PDF_NAME"
    if needs_rebuild "$CV_DEST" "$CV_MD" \
        "$CV_DIR/altcv/cvsource.css" \
        "$CV_DIR/altcv/cvsource.html" \
        "$CV_DIR/altcv/build_altcv.py" \
        "$CV_DIR/build_cv_pdf.sh"; then
      export_cv_pdf "$OUT/1_Candidate_Information"
    else
      echo "  skip   $CV_PDF_NAME (up to date)"
    fi
    maybe_export "$PS_MD" "$OUT/1_Candidate_Information" "Pacheco_Daniel_Personal_Statement.pdf"
    maybe_export "$SCHEDULE_MD" "$OUT/1_Candidate_Information" "Pacheco_Daniel_Teaching_Schedule.pdf"

    for key in 1 2a 2b 3 4 5 6; do
      pdf_name="$(course_pdf_name "$key")"
      dest="$OUT/2_Teaching/$pdf_name"
      # shellcheck disable=SC2207
      sources=()
      while IFS= read -r line; do
        [[ -n "$line" ]] && sources+=("$line")
      done < <(course_sources "$key" || true)
      if [[ ${#sources[@]} -eq 0 ]]; then
        echo "  skip   Course $key (no sources found)"
        continue
      fi
      if needs_rebuild "$dest" "${sources[@]}"; then
        rebuild_course "$key"
      else
        echo "  skip   $pdf_name (up to date)"
      fi
    done

    maybe_export "$BASE/2_Teaching/1_Introduction to Teaching/Introduction_to_Teaching.md" "$OUT/2_Teaching" "Pacheco_Daniel_Introduction_to_Teaching.pdf"

    maybe_export "$SERVICE_DIR/1_Introduction to Service/Introduction_to_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Introduction_to_Service.pdf"
    maybe_export "$SERVICE_DIR/2_Newhouse School/Newhouse_School_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Newhouse_School_Service.pdf"
    maybe_export "$SERVICE_DIR/3_Syracuse University/Syracuse_University_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Syracuse_University_Service.pdf"
    maybe_export "$SERVICE_DIR/4_Profession and Industry/Profession_and_Industry_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Profession_and_Industry_Service.pdf"
    maybe_export "$SERVICE_DIR/5_Community/Community_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Community_Service.pdf"
    maybe_export "$SERVICE_DIR/6_Other Service/Other_Service.md" "$OUT/3_Service" "Pacheco_Daniel_Other_Service.pdf"

    maybe_export "$BASE/3_Connections to the Profession/1_Professional Contributions Document/Professional_Contributions_Document.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Professional_Contributions_Document.pdf"
    maybe_export "$BASE/3_Connections to the Profession/2_Executive Summary of Engagement/Executive_Summary_of_Engagement.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Executive_Summary_of_Engagement.pdf"

    # Creative Work: narrative + exhibit PDFs in that folder (and handbook one level up)
    cw_src="$BASE/3_Connections to the Profession/3_Creative Work/Creative_Work.md"
    cw_dest="$OUT/4_Connections_to_the_Profession/Pacheco_Daniel_Creative_Work.pdf"
    cw_sources=("$cw_src")
    while IFS= read -r line; do
      [[ -n "$line" ]] && cw_sources+=("$line")
    done < <(find "$BASE/3_Connections to the Profession/3_Creative Work" -maxdepth 1 -name "*.pdf" 2>/dev/null || true)
    hb="$BASE/3_Connections to the Profession/AI in the Newsroom: Current Trends, Challenges and Innovation.pdf"
    [[ -f "$hb" ]] && cw_sources+=("$hb")
    if needs_rebuild "$cw_dest" "${cw_sources[@]}"; then
      export_pdf "$cw_src" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Creative_Work.pdf"
      REBUILT_CREATIVE_WORK=1
    else
      echo "  skip   Pacheco_Daniel_Creative_Work.pdf (up to date)"
    fi

    maybe_export "$BASE/3_Connections to the Profession/4_Professional Connections/Professional_Connections.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Professional_Connections.pdf"
    maybe_export "$BASE/3_Connections to the Profession/5_Professional Plan of Action/Professional_Plan_of_Action.md" "$OUT/4_Connections_to_the_Profession" "Pacheco_Daniel_Plan_of_Action.pdf"

    # Evidence-of-impact categories: cover md + any PDFs in matching evidence folder
    # (only categories that have content for this renewal)
    maybe_export_evidence() {
      local src="$1" dest_dir="$2" pdf_name="$3" evid_subdir="$4"
      local dest="$dest_dir/$pdf_name"
      local sources=("$src")
      local evid_dir="$BASE/5_Other Evidence of Impact/$evid_subdir"
      if [[ -d "$evid_dir" ]]; then
        while IFS= read -r line; do
          [[ -n "$line" ]] && sources+=("$line")
        done < <(find "$evid_dir" -maxdepth 1 -name "*.pdf" 2>/dev/null || true)
      fi
      if needs_rebuild "$dest" "${sources[@]}"; then
        export_pdf "$src" "$dest_dir" "$pdf_name"
        REBUILT_EVIDENCE_PDFS+=("$pdf_name")
      else
        echo "  skip   $pdf_name (up to date)"
      fi
    }
    maybe_export_evidence "$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact/Student_Correspondence.md" "$OUT/5_Other_Evidence_of_Impact" "Student_Correspondence.pdf" "3_Student_Correspondence"
    maybe_export_evidence "$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact/Academic_Correspondence.md" "$OUT/5_Other_Evidence_of_Impact" "Academic_Correspondence.pdf" "4_Academic_Correspondence"
    maybe_export_evidence "$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact/Profession_and_Industry_Correspondence.md" "$OUT/5_Other_Evidence_of_Impact" "Profession_and_Industry_Correspondence.pdf" "5_Profession_and_Industry_Correspondence"
    maybe_export_evidence "$SCRIPT_DIR/4_Supplemental_Evidence_of_Impact/Other_Evidence_of_Impact.md" "$OUT/5_Other_Evidence_of_Impact" "Other_Evidence_of_Impact.pdf" "7_Other_Evidence_of_Impact"
    ;;
esac

# Append exhibits so each Option-1 PDF is self-contained.
echo ""
if [[ "$MODE" == "full" ]]; then
  echo "Appending course exhibits, Evidence-of-Impact files, and Creative Work exhibits ..."
  "$SCRIPT_DIR/build_attachments.py"
elif [[ ${#REBUILT_COURSES[@]} -gt 0 || ${#REBUILT_EVIDENCE_PDFS[@]} -gt 0 || "$REBUILT_CREATIVE_WORK" -eq 1 ]]; then
  if [[ ${#REBUILT_COURSES[@]} -gt 0 ]]; then
    echo "Appending exhibits for courses: ${REBUILT_COURSES[*]} ..."
    "$SCRIPT_DIR/build_attachments.py" --only "${REBUILT_COURSES[@]}"
  fi
  if [[ ${#REBUILT_EVIDENCE_PDFS[@]} -gt 0 ]]; then
    echo "Appending Evidence-of-Impact exhibits for: ${REBUILT_EVIDENCE_PDFS[*]} ..."
    # Only the rebuilt cover PDF(s) — do not force-reappend siblings (that
    # doubled appendices and flipped them to Official outdated).
    "$SCRIPT_DIR/build_attachments.py" --evidence "${REBUILT_EVIDENCE_PDFS[@]}"
  fi
  if [[ "$REBUILT_CREATIVE_WORK" -eq 1 ]]; then
    echo "Appending Creative Work exhibits ..."
    "$SCRIPT_DIR/build_attachments.py" --creative-work
  fi
else
  echo "No packets needing attachments; skipping attachment step."
fi

count="$(find "$OUT" -name "*.pdf" 2>/dev/null | wc -l | tr -d ' ')"
echo ""
echo "Done: $count PDFs in $OUT (mode=$MODE${COURSE_KEY:+ key=$COURSE_KEY}${PACKET_KEY:+ packet=$PACKET_KEY})"
