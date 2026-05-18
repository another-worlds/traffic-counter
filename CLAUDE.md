# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
# Start everything (builds images on first run)
docker compose up --build

# Rebuild a single service after code changes
docker compose up --build api
docker compose up --build worker
docker compose up --build frontend
docker compose up --build watcher

# Build the React/Vite hybrid viewport (must rebuild after any frontend/hybrid_viewport/src change)
cd frontend/hybrid_viewport && npm run build

# Run the API standalone (outside Docker)
cd api && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run the worker in poll mode (outside Docker)
cd worker && WORKER_MODE=poll DATABASE_URL=postgresql+psycopg://traffic:traffic@localhost:5432/traffic \
  STORAGE_BACKEND=local LOCAL_STORAGE_ROOT=/data python main.py

# Run the watcher (outside Docker)
cd watcher && API_URL=http://localhost:8000 WATCH_PATH=/mnt/yandex-videos python main.py
```

There are no automated test suites. Validation is via TypeScript types (frontend) and Pydantic schemas (API).

## Architecture

Five Docker services collaborate:

```
frontend (Streamlit :8501)
    ↕ REST (API_URL env)
api (FastAPI :8000)
    ↕ SQL
db (PostgreSQL :5432)

worker (GPU, poll or single-shot)
    ↕ SQL + storage read/write

watcher (inotify + periodic scan)
    → POST /local-folder/register → api
```

**Video lifecycle state machine:** `uploading → uploaded → queued → analyzing → analyzed | error`

## API Service (`api/`)

### Adding a route
1. Create or edit a router file in `api/app/routers/`.
2. Register it in `api/app/main.py` with `app.include_router(...)`.

### Database migrations
There is no Alembic. `db.py:_safe_add_columns()` runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements at startup. Add new columns there alongside the SQLAlchemy model change in `models.py` and the Pydantic schema change in `schemas.py`.

### Storage abstraction
`api/app/services/storage.py` provides `LocalStorage` (dev) and `GCSStorage` (prod), selected via `STORAGE_BACKEND` env var. Use the `key_*` helper functions to construct storage paths—never build paths by hand. The same `key_*` helpers exist in `worker/storage.py`; keep both in sync when adding new artifact types.

Storage layout:
```
projects/{project_id}/videos/{video_id}/source{.ext}
projects/{project_id}/videos/{video_id}/tracks.parquet
projects/{project_id}/videos/{video_id}/frame.jpg          ← legacy single frame
projects/{project_id}/videos/{video_id}/frames/{n}.jpg     ← scene keyframes
projects/{project_id}/videos/{video_id}/trajectories.png
projects/{project_id}/videos/{video_id}/heatmap.png
projects/{project_id}/exports/{export_id}.xlsx
```

`frame.jpg` (legacy) and `frames/0.jpg` both exist for analyzed videos so that old and new clients work. `key_frame()` must always return the `frame.jpg` path; `key_scene_frame(n)` returns `frames/{n}.jpg`.

### Job runner
`api/app/services/jobs.py` has two implementations selected by `JOB_RUNNER` env var:
- **`local`** — no-op; the long-running worker container polls for queued rows.
- **`cloudrun`** — creates a Cloud Run Job execution with `VIDEO_ID`/`PROJECT_ID` env overrides.

## Worker Service (`worker/`)

`WORKER_MODE=poll` loops forever; `WORKER_MODE=single` processes one video (set via `VIDEO_ID` env) then exits (used by Cloud Run Jobs).

Job claiming is atomic: `SELECT ... FOR UPDATE SKIP LOCKED` so concurrent workers never double-claim.

**Pipeline** (`worker/pipeline.py`):
1. Fetch video — if `local_source_path` is set, read directly from the mount path (no download). Otherwise, download from storage.
2. Run YOLO + ByteTrack (`model.track(..., stream=True)`) — only vehicle classes (COCO IDs 1, 2, 3, 5, 7).
3. Write `tracks.parquet` (columns: `frame_idx, t_seconds, track_id, class_id, conf, cx, cy, w, h`).
4. Detect scene cuts (PySceneDetect); extract keyframe JPEGs.
5. Upload all artifacts; write scene-frame metadata back to the `videos.scene_frames` JSON column.
6. Also write scene 0 to the legacy `frame.jpg` key for backward compatibility.

## Counting Math (`api/app/services/counting.py`)

Crossing detection uses 2-D cross products: for each consecutive track-point pair `(Pk, Pk+1)`, test strict segment–segment intersection with the counting line `(A, B)`. Direction is `sign((B−A) × (Pk+1−Pk))`. In multi-video mode, track IDs are namespaced as `hash("{video_id}:{track_id}")` to avoid collisions.

## Frontend (`frontend/`)

Streamlit pages live in `frontend/pages/`. `api_client.py` wraps all API calls; add new calls there.

### Hybrid viewport (React inside Streamlit)
`frontend/hybrid_viewport/` is a standalone Vite project. After any change in `src/`, run `npm run build` — the output lands in `dist/` which is bind-mounted into the frontend container.

There are **two separate Vite entry points** (both built by `npm run build`):
- `dist/index.html` — the main line-drawing viewport.
- `dist/uploader/index.html` — the tus resumable-upload widget.

**Bridge protocol:**
- Streamlit calls the component with a `bootstrap` JSON object (`HostViewportBootstrap` TypeScript type).
- React emits `overlay-snapshot` payloads back via `Streamlit.setComponentValue(...)` whenever the user edits lines.
- Snapshots include `lines` (current line state), `pendingActions` (one-shot requests like `request-suggestions`), and `activeLayers`.
- Python reconciles the `lines` array against the DB on each snapshot (creates/updates/deletes as needed).
- **Do not** dispatch `pendingActions` without pre-clearing them in `lastEmittedRef` — the double-emit pattern in `App.tsx` exists to prevent Streamlit from receiving two snapshots (one with actions, one without) and discarding the first.

**Line drawing flow:**
- `start-draw` is deferred until the first mousemove exceeds `MOVE_THRESHOLD` pixels (not on mousedown). This prevents zero-length draft lines from being silently discarded.

## Watcher Service (`watcher/`)

Monitors a folder for new video files and calls `POST /local-folder/register`. Uses watchdog's inotify observer (`on_closed` = `IN_CLOSE_WRITE`) plus a periodic full-directory rescan. File stability is verified by comparing `stat().st_size` twice with a `STABILITY_WAIT`-second gap before registering.

`POST /local-folder/register` is idempotent (dedup by `local_source_path`); it auto-creates a "Yandex Disk Inbox" project if one doesn't exist.

## Environment Variables Reference

| Var | Service | Notes |
|-----|---------|-------|
| `DATABASE_URL` | api, worker | `postgresql+psycopg://user:pass@host/db` |
| `STORAGE_BACKEND` | api, worker | `local` or `gcs` |
| `LOCAL_STORAGE_ROOT` | api, worker | Root path for `local` backend |
| `JOB_RUNNER` | api | `local` or `cloudrun` |
| `WORKER_MODE` | worker | `poll` or `single` |
| `VIDEO_ID` / `PROJECT_ID` | worker | Set by Cloud Run Job for `single` mode |
| `MODEL_NAME` | worker | Path to YOLO `.pt` weights |
| `DEVICE` | worker | `cuda:0` or `cpu` |
| `HALF` | worker | `true` for FP16 inference |
| `FRAME_STRIDE` | worker | Process every Nth frame (default 1) |
| `API_URL` | frontend, watcher | Internal Docker hostname |
| `PUBLIC_API_URL` | frontend | Browser-reachable URL for signed asset URLs |
| `WATCH_PATH` | watcher | Folder to monitor |
| `AUTO_ANALYZE` | watcher | `true` to auto-queue new videos |
| `SCAN_INTERVAL` | watcher | Full-rescan interval in seconds |
| `STABILITY_WAIT` | watcher | Seconds to confirm file write is complete |
