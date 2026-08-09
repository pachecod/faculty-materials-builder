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
   - `ADMIN_SYNC_TOKEN` (long random secret; same value in local `.env` for one-click pack upload)
3. `autoDeploy: true` so app code updates on git push (content still via import pack).
4. First deploy can be empty  no PoP content yet until you import a pack.

## 4. Populate content (efficient)

On your Mac (local tree with content), either:

**A. One-click upload (preferred)**

1. In local `.env` set:
   - `RENDER_SYNC_URL=https://pacheco-materials.onrender.com`
   - `ADMIN_SYNC_TOKEN=` *(same value as on Render)*
2. Local Admin ? **Build Render import pack** ? **Upload to Render**
   - Optional: check wipe-before-import
3. Pack includes markdown sources, attachment PDFs, and packet PDFs. Render imports in the background; visitors get PDF + Reader View without a manual `/admin` upload.

**B. Manual zip**

```bash
./scripts/build_content_pack.sh
# ? content-pack-YYYYMMDD-HHMMSS.zip (keep offline; do not commit)
```

Then on Render `/admin`: **Import content** (optionally wipe first).

Edit markdown and rebuild PDFs only on a local `POP_MODE=edit` install. Re-upload a new pack anytime after local edits. Default import merges by path; use wipe when you need a full replace. App code updates = `git push` (autodeploy); disk content persists across deploys.

### Reader View

Public portal (and local Preview):

- **Wide windows (>1100px):** packet opens as PDF; use **Reader View** for the markdown narrative + attachment PDF list.
- **Narrow windows (?1100px CSS width):** opens **Reader View** first; **View full PDF** for the merged packet. Resizing the browser and opening again is enough to test — choice is made at click time from `window.innerWidth`.

## 5. Surfaces (Render)

| URL | Page shell | Role |
|-----|------------|------|
| `/` | `app_pages/portal.html` | Public view + downloads |
| `/admin` | `app_pages/hosted-admin.html` | Import + site access only |
| `/api/download/pdf?file=…` | — | Single PDF download |
| `/api/download/all` | — | Zip of all PDFs |

Local authoring uses **different** pages under `POP_MODE=edit` so local chrome cannot leak onto Render.

## Local day-to-day

```bash
POP_MODE=edit python3 serve_metrics.py          # :8765
```

| Local URL | Page shell | Role |
|-----------|------------|------|
| `/edit` | `local.html` | Edit & Append |
| `/admin` | `local.html` | Local Admin (build pack, import, site access) |
| `/preview` | `portal.html` | **Test Render public `/`** before push |
| `/render-admin` | `hosted-admin.html` | **Test Render `/admin`** before push |

A sticky top toggle (**Local working copy** / **What Render users see**) appears only in local edit mode. It is not shown on Render.

Content and `.env` stay on disk; git only moves app changes.
