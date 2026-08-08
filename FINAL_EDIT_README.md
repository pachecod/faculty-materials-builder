# Renewal 3 Final Edit (2026)

Active final-edit tree. Do **not** edit the frozen snapshot at `../Renewal 3 (2026)/`.

## Snapshot

| Role | Path |
|---|---|
| Frozen reference | `../Renewal 3 (2026)/` |
| Active work | this folder |

## Dashboard (edit mode)

```bash
cd "Renewal 3 Final Edit (2026)"
./start_metrics.sh
# -> http://127.0.0.1:8765/
```

Loop for each packet: **Preview -> Edit -> Rebuild preview -> Save as official**.

- Edit saves markdown only; Rebuild refreshes the `_pdf_review` PDF (and re-appends exhibits).
- **Save as official** copies the current preview into `0_Drafts/_official/` (FPS-ready).
- Attachments: add/remove files under packet attachment folders, then Rebuild.

## Publish viewer (read-only, local)

1. Promote packets you want public (`Save as official`).
2. Build the offline bundle:

```bash
./scripts/build_publish_bundle.sh
```

3. Preview exactly what reviewers would see:

```bash
./start_viewer.sh
# or: POP_MODE=viewer python3 serve_metrics.py
```

Set `VIEW_PASSWORD` (or `SITE_PASSWORD`) and `SECRET_KEY` in `.env` (copy from `.env.example`). Viewer mode hides Edit / Rebuild / Upload / Promote.

## GitHub + Render (content-free deploy)

See **[DEPLOY.md](DEPLOY.md)** for the full private-repo → Render → content-pack flow.

Short version:

1. App-only code lives in the private GitHub repo (content and `.env` stay local / ignored).
2. Deploy from that repo with `POP_MODE=production` and `PUBLISH_DATA_ROOT=/data`.
3. Build a content pack locally: `./scripts/build_content_pack.sh`
4. Sign in at `/admin`, upload the pack, rebuild/promote as needed, optional view password.
5. Public portal is `/` (preview + downloads).

Local edit mode remains the primary authoring environment.
