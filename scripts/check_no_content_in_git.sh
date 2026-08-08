#!/usr/bin/env bash
# Fail if tracked files look like PoP content or secrets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git repository ù skip content audit."
  exit 0
fi

FAIL=0

forbidden_patterns=(
  '^\.env$'
  '^\.env\.[^e]'
  '^site_auth\.json$'
  '^1_Candidate Information/'
  '^2_Teaching/'
  '^3_Connections to the Profession/'
  '^4_Service/'
  '^5_Other Evidence of Impact/'
  '^Examples_other_profs/'
  '^0_Drafts/1_Candidate_Information/'
  '^0_Drafts/2_Supplemental_Materials_Teaching/'
  '^0_Drafts/3_Supplemental_Materials_Service/'
  '^0_Drafts/4_Supplemental_Evidence_of_Impact/'
  '^0_Drafts/_extras_still_in_instructions_pdf/'
  '^0_Drafts/_pdf_review/'
  '^0_Drafts/_official/'
  '^publish/pdfs/'
  '^publish/workspace/'
  '^metrics_status\.json$'
  'content-pack.*\.zip$'
  '\.pem$'
  'credentials'
  '\.pdf$'
)

while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  for pat in "${forbidden_patterns[@]}"; do
    if [[ "$path" =~ $pat ]]; then
      echo "ERROR: tracked path looks like content/secret: $path"
      FAIL=1
    fi
  done
done < <(git ls-files)

if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "ERROR: .env is tracked"
  FAIL=1
fi

if [[ "$FAIL" -ne 0 ]]; then
  echo "Content/secret audit FAILED."
  exit 1
fi

echo "Content/secret audit OK ($(git ls-files | wc -l | tr -d ' ') tracked files)."
exit 0
