# traffic-counter

Project-organized vehicle tracking and counting from pre-recorded video.
Designed for Cloud Run on GCP with NVIDIA L4 GPU workers (scale to zero).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser  →  Streamlit (8501)  →  FastAPI (8000)  →  PostgreSQL │
│                                         │                        │
│                                   shared /data volume            │
│                                         │                        │
│                             Worker(s) ──┘                        │
│                          (YOLO + ByteTrack, GPU)                 │
└─────────────────────────────────────────────────────────────────┘
```

Four Docker services:

| Service | Role |
|---|---|
| `db` | PostgreSQL 16 — source of truth for projects, videos, counting lines |
| `api` | FastAPI — REST layer; triggers analysis jobs, serves counts |
| `frontend` | Streamlit — multi-page UI; canvas line editor, export |
| `worker` | Python process(es) — GPU inference, writes Parquet + PNGs |

## Data flow

```
Upload video (frontend)
  → API stores file in /data + inserts DB row (status=queued)
    → Worker polls DB (SELECT FOR UPDATE SKIP LOCKED)
      → Claims row (status=analyzing)
        → Downloads video to temp dir
          → YOLOv8m + ByteTrack (GPU, FP16, stride=2)
            → tracks.parquet   → /data
            → frame.jpg        → /data  (busiest frame, captured inline)
            → trajectories.png → /data  (transparent RGBA overlay)
          → DB row → status=analyzed, fps/width/height/num_tracks written
```

**Detect once, count instantly.** The worker runs YOLOv8m + ByteTrack once per
video and persists a single `tracks.parquet` (one row per detection: frame,
track_id, class, cx, cy, w, h). After that, drawing a counting line is pure 2D
geometry over the dataframe — milliseconds, no GPU.

Parquet schema: `frame_idx int32, t_seconds float64, track_id int32, class_id int16, conf float32, cx float32, cy float32, w float32, h float32`

Vehicle class IDs (COCO): 1=bicycle, 2=car, 3=motorcycle, 5=bus, 7=truck

### Counting math

For each track polyline `P_0, ..., P_{n-1}` and counting line segment `(A, B)`,
a crossing exists at segment k iff:

    sign((B-A) × (P_k - A)) ≠ sign((B-A) × (P_{k+1} - A))   AND
    sign((P_{k+1}-P_k) × (A - P_k)) ≠ sign((P_{k+1}-P_k) × (B - P_k))

Direction is `sign((B-A) × (P_{k+1} - P_k))` at the first crossing segment.

Two percentages are reported per line `i`:

    share_video_total[i] = C_i / T            T = unique tracks in selection
    share_drawn[i]       = C_i / Σ_j C_j

See `api/app/services/counting.py`.

### Trajectory overlay colours

| Colour | Class |
|---|---|
| Blue | Car |
| Coral/red | Bus |
| Teal | Bicycle |
| Amber | Motorcycle |
| Red | Truck |

---

## Local development

```bash
# 1. start the stack
docker compose up --build

# 2. open the UI
open http://localhost:8501

# 3. workflow
#    Sidebar → create a project
#    Videos page → upload an mp4 → click "Analyze"
#    (worker picks it up, processes it, marks 'analyzed')
#    Count & export page → draw lines → export xlsx
```

The `worker` container runs in `WORKER_MODE=poll`, claiming queued videos
atomically (`SELECT FOR UPDATE SKIP LOCKED`). Multiple workers (or containers)
can run simultaneously — the queue is contention-safe.

For local dev without a GPU, set `DEVICE=cpu` in `docker-compose.yml`; analysis
runs on CPU (slow but correct). FP16 is automatically disabled on CPU.

---

## Yandex.Disk integration

Videos stored in a Yandex.Disk sync folder on the host machine can be browsed
and imported directly — no browser upload required.

### How it works

```
Yandex.Disk folder (VM)
    └─ bind-mounted read-only → /yadisk inside the API container

GET  /disk/browse?path=…       → directory listing (JSON)
POST /projects/{id}/videos/from-disk  { disk_path }  → Video record
```

The API hard-links the source file into the storage volume (instant, no extra
disk space) and falls back to `shutil.copy2` if source and destination are on
different filesystems. The file is then treated identically to a browser upload
— click **Analyze** to start inference.

If `YADISK_ROOT` is not set or the path does not exist the endpoints return
`503` and the frontend shows a graceful info message.

### Setup

**1. Find the Yandex.Disk sync path on your VM:**
```bash
yandex-disk status
# e.g.  Path to Yandex.Disk directory: '/home/alexey/Yandex.Disk'
```

**2. Set the host path** (in `.env` or as a shell export):
```bash
# .env  (copy from .env.example)
YADISK_HOST_PATH=/home/alexey/Yandex.Disk
```

**3. Restart the stack:**
```bash
docker compose up -d
```

The folder is mounted read-only at `/yadisk` inside the `api` container and
exposed to Python via `settings.yadisk_root` (`YADISK_ROOT=/yadisk` is already
set in `docker-compose.yml`).

### Using the browser in the UI

Open **Videos → 📂 Import from Yandex.Disk**.

- Click a 📁 folder to navigate into it; use the breadcrumb row to go back up.
- 🎬 video files show an **Import** button. Clicking it registers the file as a
  new video in the current workspace (status `uploaded`).
- Non-video files are shown for context but cannot be imported.

### API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/disk/browse?path=` | List entries inside `YADISK_ROOT`. `path` is relative (default: root). |
| `POST` | `/projects/{id}/videos/from-disk` | Import `{"disk_path": "relative/path.mp4"}` as a new video. |

**Browse response:**
```json
{
  "path": "Traffic/2026-05",
  "entries": [
    { "name": "cam1.mp4", "is_dir": false, "is_video": true,
      "size": 412893184, "modified": "2026-05-10T14:32:00" },
    { "name": "archive",  "is_dir": true,  "is_video": false,
      "size": null,      "modified": "2026-05-01T09:00:00" }
  ]
}
```

### Supported video formats

`.mp4`, `.mov`, `.mkv`, `.avi`, `.webm`, `.ts`, `.m4v`

---

## Worker configuration

All settings are env vars. Defaults are production-ready; override in
`docker-compose.yml` or as container env on Cloud Run.

### GPU / device

| Var | Default | Notes |
|---|---|---|
| `DEVICE` | `cuda:0` if CUDA detected, else `cpu` | Set to `cpu` for dev without a GPU |
| `HALF` | `true` | FP16 inference; auto-disabled on CPU |
| `CUDA_MEM_FRACTION` | `0.9` | Fraction of GPU VRAM this process may use (0–1); lower when running `WORKER_CONCURRENCY > 1` |
| `MODEL_NAME` | `yolov8m.pt` | Path or ultralytics model name (`yolov8n.pt` for speed, `yolov8l.pt` for accuracy) |

`torch.backends.cudnn.benchmark = True` is set automatically, giving ~10-15%
speedup on fixed-resolution inputs (all traffic videos use `IMGSZ=640`).

### Inference quality

| Var | Default | Notes |
|---|---|---|
| `IMGSZ` | `640` | YOLO input resolution. `1280` for distant/small vehicles at ~4× cost |
| `CONF` | `0.40` | Detection confidence threshold. Lower → catches more distant vehicles |
| `IOU` | `0.45` | NMS IoU threshold |
| `MAX_DET` | `300` | Max detections per frame |
| `FRAME_STRIDE` | `2` | Process every Nth frame. `2` = 15 fps from 30 fps source; ByteTrack handles gaps, <2% ID-loss increase |
| `TRACKER` | `bytetrack.yaml` | Tracker config file (relative to ultralytics package) |

### Concurrency

| Var | Default | Notes |
|---|---|---|
| `WORKER_CONCURRENCY` | `1` | Parallel worker processes inside one container |
| `WORKER_MODE` | `poll` | `poll` = long-running loop; `single` = process one video (set `VIDEO_ID` too) |
| `STUCK_JOB_TIMEOUT_MINUTES` | `30` | Videos stuck in `analyzing` for longer than this are reset to `queued` on next startup |

### Common recipes

**Two workers sharing one GPU (≥8 GB VRAM recommended):**
```yaml
# docker-compose.yml  worker.environment
WORKER_CONCURRENCY: "2"
CUDA_MEM_FRACTION: "0.45"
```

**Two separate containers (two GPUs, or Docker Swarm):**
```bash
docker compose up --scale worker=2
# No WORKER_CONCURRENCY needed — DB queue handles it
```

**Dev on CPU (laptop, no GPU):**
```yaml
DEVICE: cpu
# HALF is auto-disabled
```

**Best detection of distant / small vehicles:**
```yaml
IMGSZ: "1280"
CONF: "0.30"
```

**Maximum throughput (batch processing archive footage):**
```yaml
FRAME_STRIDE: "4"   # 30fps → 7.5fps effective
CONF: "0.45"
```

---

## Deploy to GCP

Prerequisites: gcloud authenticated, billing enabled, project picked.

```bash
cp .env.example .env
# edit .env: GCP_PROJECT, GCS_BUCKET, DB_PASSWORD, region

./infra/deploy.sh
```

The script:

1. Enables required APIs.
2. Creates Artifact Registry repo, GCS bucket, Cloud SQL Postgres instance.
3. Builds three images with Cloud Build (api, worker, frontend).
4. Deploys:
   - **API** — Cloud Run service, CPU 2 / 2 GiB, public, connects to Cloud SQL.
   - **Frontend** — Cloud Run service, CPU 1 / 1 GiB, public.
   - **Worker** — Cloud Run **Job** with `--gpu 1 --gpu-type nvidia-l4`. Scales
     to zero. Each analysis is one execution.

When the API serves `POST /videos/{id}/analyze`, it directly triggers a
job execution via `google-cloud-run` SDK with `VIDEO_ID` set as a per-execution
env override. This is the right primitive for "one GPU instance per analysis
job, scale to zero between jobs."

### Cost notes

- L4 on Cloud Run Jobs ≈ $0.71/GPU-hour.
- YOLOv8m + ByteTrack on 1080p frontal traffic at ~50–80 fps on L4 with
  `HALF=true`. A 10-minute video costs roughly $0.02–0.04 of GPU time.
- Cloud SQL `db-f1-micro` ≈ $9/month.
- GCS storage is pennies.


### Scaling knobs

- `FRAME_STRIDE` env on the worker — process every Nth frame for big speedups
  on long clips. Track interpolation is implicit (tracker carries IDs forward).
- `HALF=true` — FP16 on L4 is free (~2× over FP32).
- Switch the model to `yolov8n.pt`/`yolov8s.pt` for throughput, or export it to
  TensorRT (`yolo export format=engine half=True`) for another ~2× — bake the
  resulting `.engine` into the worker image.
- For warm workers, swap the Cloud Run Job for a Cloud Run **Worker Pool**
  with the same image (`gcloud run worker-pools …`).

## Project layout

```
api/                FastAPI service (CPU)
  app/main.py
  app/models.py        projects, videos, counting_lines
  app/schemas.py
  app/routers/         projects, videos, lines, analysis, disk (Yandex.Disk)
  app/services/
    storage.py         local FS + GCS
    counting.py        ← THE MATH
    tracks.py          parquet I/O + per-video namespacing
    jobs.py            local poll vs Cloud Run Job trigger
    xlsx_export.py     openpyxl workbook builder

worker/             GPU job container
  main.py              poll mode (local) and single-video mode (Cloud Run Job)
  pipeline.py          YOLO + ByteTrack + parquet write + trajectory PNG
  storage.py

frontend/           Streamlit UI
  streamlit_app.py     project picker
  pages/1_🎥_Videos.py     upload + analyze + Yandex.Disk browser
  pages/2_📏_Count_and_export.py    draw lines, compute, export
  canvas_editor/index.html  Fabric.js Streamlit component (line overlay UI)

infra/deploy.sh     gcloud one-shot deploy
```

## Known limitations & next steps

- **Auth**: deploy script makes services public (`--allow-unauthenticated`).
  Production should put IAP or Firebase Auth in front, and require an IAM
  identity for the API → Worker Job invocation (currently the API service's
  default service account must have `roles/run.invoker` on the worker job).
- **Migrations**: API uses `SQLAlchemy.create_all()` for simplicity. Swap for
  Alembic before evolving the schema.
- **Direct uploads**: large videos currently stream through the API. For
  production, add a `POST /videos/upload-url` endpoint that issues a GCS V4
  signed PUT URL and have the browser upload directly.
- **Track interpolation**: when `FRAME_STRIDE > 1`, tracks are at sub-frame
  rate. The counting math is unaffected (segments still intersect lines), but
  visualization can look choppy. Add linear interpolation in the parquet
  read path if it matters.
