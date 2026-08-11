# Faculty Materials Builder

A Flask app for authoring, previewing, and publishing faculty review packets (markdown narratives + PDF exhibits). Local edit mode is the primary authoring environment. Hosted production serves a public portal and a separate admin import surface.

**Repository:** https://github.com/pachecod/faculty-materials-builder

This git tree is **app code only**. Narrative folders, generated PDFs, content packs, and `.env` stay on your machine (or on a host disk) and are gitignored.

## What it does

- Edit packet markdown in the browser and rebuild review PDFs
- Append exhibit PDFs into packet merges
- Promote packets to an official set for reviewers
- Build offline content packs for hosted import
- Run a read-only local viewer, or deploy a public portal + admin on Render

## Requirements

- Python 3.12+ (see `runtime.txt`)
- pip
- For PDF rebuild from markdown: Pandoc and a XeLaTeX engine (TeX distribution with `xelatex`)

Optional for hosted deploy:

- Render (or similar) with a persistent disk
- Optional Postgres if you use the catalog sync scripts

## Install (local)

```bash
git clone https://github.com/pachecod/faculty-materials-builder.git
cd faculty-materials-builder

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env: set SECRET_KEY (and passwords if you will use viewer/production)
```

System tools for PDF export (macOS example with Homebrew):

```bash
brew install pandoc
# Install a TeX distribution that provides xelatex (e.g. MacTeX or BasicTeX + needed packages)
```

Confirm Pandoc can find XeLaTeX:

```bash
pandoc --version
which xelatex
```

## Run locally

**Edit mode** (default authoring UI):

```bash
./start_metrics.sh
# or: POP_MODE=edit python3 serve_metrics.py
# -> http://127.0.0.1:8765/
```

Useful local routes in edit mode:

| URL | Role |
|-----|------|
| `/edit` | Edit & append |
| `/admin` | Local admin (build pack, import, site access) |
| `/preview` | Preview of the public portal |
| `/render-admin` | Preview of hosted `/admin` |

**Read-only viewer** (what reviewers would see after you build a publish bundle):

```bash
./scripts/build_publish_bundle.sh
./start_viewer.sh
# or: POP_MODE=viewer python3 serve_metrics.py
```

Set `VIEW_PASSWORD` (or `SITE_PASSWORD`) and `SECRET_KEY` in `.env` before sharing a viewer.

## Content layout (not in git)

Keep packet sources beside the app (same patterns as a full local tree):

- `1_Candidate Information/`
- `2_Teaching/`
- `3_Connections to the Profession/`
- `4_Service/`
- `5_Other Evidence of Impact/`
- Draft helpers under `0_Drafts/` (scripts and `packet_registry.json` ship in git; narrative copies and PDF output do not)

Typical authoring loop: **Preview -> Edit -> Rebuild preview -> Save as official**.

## Content packs and deploy

Build a zip of markdown + PDFs for import (do not commit the zip):

```bash
./scripts/build_content_pack.sh
```

Before any push, audit that content and secrets are not tracked:

```bash
./scripts/check_no_content_in_git.sh
```

Hosted deploy (Render, `POP_MODE=production`, disk at `/data`, admin import) is documented in **DEPLOY.md**. Day-to-day local notes are also in **FINAL_EDIT_README.md**.

## Security notes before making the repo public

- Confirm `.env` is never committed
- Run `./scripts/check_no_content_in_git.sh` and review `git ls-files` for PDFs, narrative folders, or personal docs
- Rotate any passwords or sync tokens that may have been used while the repo was private if they were ever exposed in git history
- Content packs (`content-pack*.zip`) stay offline

## License

MIT License. See **LICENSE.md**.

Copyright 2026 Dan Pacheco.
