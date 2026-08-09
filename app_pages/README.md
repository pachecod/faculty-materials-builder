# App page shells

Separate HTML so local chrome cannot leak onto Render.

| File | Used by |
|------|---------|
| `local.html` | Local `POP_MODE=edit` only: `/edit`, `/admin` |
| `portal.html` | Render `/` and local `/preview` (public visitor UI) |
| `hosted-admin.html` | Render `/admin` and local `/render-admin` |
| `local-switcher.js` | Local-only top toggle (injected when `localSurfaces`) |

## Local testing of Render

With `POP_MODE=edit python3 serve_metrics.py`, a sticky toggle appears at the top:

1. **Local working copy** — `/edit` (Edit & Append) and `/admin` (build pack / local tools)
2. **What Render users see** — `/preview` (same as production `/`) and `/render-admin` (same as production `/admin`)

The switcher is not shown on Render production.
