# Viewer notes

The read-only viewer is the same Flask app (`serve_metrics.py`) with `POP_MODE=viewer`.

- Local: `./start_viewer.sh` after `./scripts/build_publish_bundle.sh`
- Production (later): Render web service from `render.yaml`, password via `SITE_PASSWORD`

No separate frontend lives here; this folder is reserved for viewer-only assets if needed later.
