# Deploy: private GitHub ? Render ? content pack

Local authoring stays in this tree (`POP_MODE=edit`). GitHub holds **app code only**. Content never goes in git.

## 1. Harden and audit (before first push)

```bash
# From this app root
./scripts/check_no_content_in_git.sh   # after git init / first add
```

Confirm `.env` and folders like `1_Candidate Information/`, `2_Teaching/`, PDFs under `0_Drafts/_pdf_review` / `_official` are **ignored**, not tracked.

## 2. Private GitHub repo first

```bash
git init
git add -A
git status   # review: no .env, no narrative folders, no PDFs
./scripts/check_no_content_in_git.sh
git commit -m "Initial app-only PoP Final Edit dashboard"

# Create PRIVATE remote (do not use --public)
gh repo create faculty-materials-builder --private --source=. --remote=origin --push
```

While the repo is still private, open GitHub and confirm the file tree has no content or secrets. Optionally:

```bash
git clone <private-url> /tmp/pop-audit && ls /tmp/pop-audit
```

Only then connect Render.

## 3. Render

1. New Web Service from the **private** GitHub repo.
2. Use [`render.yaml`](render.yaml) or set manually:
   - `POP_MODE=production`
   - `PUBLISH_DATA_ROOT=/data`
   - Disk mounted at `/data`
   - `ADMIN_PASSWORD` (Dashboard secret)
   - `SECRET_KEY` (generate)
   - Optional `VIEW_PASSWORD` bootstrap
3. Keep `autoDeploy: false` until you are ready.
4. Deploy empty — no PoP content yet.

## 4. Populate content (efficient)

On your Mac (local tree with content):

```bash
./scripts/build_content_pack.sh
# ? content-pack-YYYYMMDD-HHMMSS.zip (keep offline; do not commit)
```

On Render (viewer only — no content editing):

1. Sign in at `/admin` with `ADMIN_PASSWORD`
2. **Import content** — upload the zip from local **Build Render import pack** (or `scripts/build_content_pack.sh`)
3. To replace everything cleanly (remove orphans from older packs): check **Wipe all existing content… before importing**, or use **Wipe all content…** then import
4. Optional: **Site access** — set a view password for `/`

Edit markdown and rebuild PDFs only on a local `POP_MODE=edit` install. Re-upload a new pack anytime after local edits. Default import merges by path; use wipe when you need a full replace. App code updates = `git push` then Manual Deploy (disk content persists).

## 5. Surfaces

| URL | Role |
|-----|------|
| `/` | Public view + downloads (optional view password) |
| `/admin` | Import content pack + site access only |
| `/api/download/pdf?file=…` | Single PDF download |
| `/api/download/all` | Zip of all PDFs by section folder |

## Local day-to-day

```bash
# Authoring
POP_MODE=edit python3 serve_metrics.py          # :8765

# Local view preview
./start_viewer.sh                               # or POP_MODE=viewer
```

Content and `.env` stay on disk; git only moves app changes.
