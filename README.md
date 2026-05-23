# traffic-counter

Project-organized vehicle tracking and counting from pre-recorded video.
Designed for Cloud Run on GCP with NVIDIA L4 GPU workers (scale to zero).

## How it works

```
┌──────────────────────────────────────────────────────────────────┐
│  Streamlit frontend  ◀──── HTTPS ────▶  FastAPI service          │
│  (line drawing UI)                       (CPU Cloud Run service) │
└──────────────────────────────────────────────────────────────────┘
                                            │
        ┌───────────────────┬───────────────┼────────────────────┐
        ▼                   ▼               ▼                    ▼
  ┌──────────┐        ┌──────────┐    ┌───────────┐      ┌──────────────┐
  │ Cloud    │◀──────▶│ Cloud SQL│    │ Cloud Run │      │ Cloud Storage│
  │ Storage  │        │ Postgres │    │ Job (L4)  │─────▶│ tracks.parquet│
  └──────────┘        └──────────┘    │ worker    │      └──────────────┘
                                       └───────────┘
```

**Detect once, count instantly.** The worker runs YOLOv8m + ByteTrack once per
video and persists a single `tracks.parquet` (one row per detection: frame,
track_id, class, cx, cy, ...). After that, drawing a counting line is pure 2D
geometry over the dataframe — milliseconds, no GPU.

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

## Local development

```bash
# 1. start the stack
docker compose up --build

# 2. open the UI
open http://localhost:8501

# 3. workflow
#    Sidebar → create a project
#    Watched Folder page → drop an mp4 in the mounted folder; the
#      watcher registers it and (with AUTO_ANALYZE=true) the worker
#      picks it up automatically
#    Count & export page → draw lines, view live counts, export xlsx
```

The `worker` container in compose runs in `WORKER_MODE=poll`, claiming queued
videos atomically (`SELECT ... FOR UPDATE SKIP LOCKED`). For local dev without
a GPU, set `DEVICE=cpu` (already the default in `docker-compose.yml`); analysis
runs in CPU mode (slow but works).

## Using the Count & Export page

![Counting line editor — annotated](docs/usage-guide.png)

1. **Drawing** — click and drag on empty video to start a new line; drag the
   white endpoint handles to fine-tune; press <kbd>Delete</kbd> to remove the
   selected line.
2. **Handles** — the two white dots are draggable endpoints; the line is
   live-counted as you move them.
3. **Frame / scene indicator** — when scene cuts are detected, the slider
   below the viewport scrubs between cameras; otherwise this just reads
   "Single camera angle".
4. **Total readings** — live count across every selected counting line.
5. **Layers** — toggle the trajectory trails, the speed heatmap, and the
   line overlay independently.
6. **Drawing color** — the color the next line you draw will use.
7. **Counting lines** — rename, recolor, see per-class & per-direction
   breakdown, or delete a saved line.
8. **Auto-suggest** — pick N, click *Suggest*, and the API proposes lines
   from trajectory clusters; accept any to commit.
9. **Import / Export** — save the line configuration as JSON, reload it on
   another video or project.
10. **Direction rose** — inbound vs outbound split for the selected line(s).
11. **Splits by direction** — per-class crossings across the selection.

> Re-render the annotated image after a UI change:
> `python docs/build_usage_guide.py` (reads `docs/usage-guide.source.png`).

### Large file uploads (>20GB)

To support uploads larger than 20GB, create `frontend/.streamlit/config.toml`:

```toml
[client]
maxUploadSize = 100000

[server]
maxUploadSize = 100000
```

The API and frontend automatically stream files >1GB to avoid memory exhaustion.
See [docs/LARGE_FILE_UPLOADS.md](docs/LARGE_FILE_UPLOADS.md) for details and troubleshooting.

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

- L4 on Cloud Run Jobs ≈ $0.71/GPU-hour (no-zonal-redundancy).
- YOLOv8m + ByteTrack on 1080p frontal traffic at ~50–80 fps detection on L4
  with `half=True`. A 10-minute video costs roughly $0.02–0.04 of GPU time.
- Cloud SQL `db-f1-micro` ≈ $9/month; bump it later.
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

## Scaling workers on a single GPU

The worker container is stateless and claims jobs via
`SELECT … FOR UPDATE SKIP LOCKED`, so multiple replicas can share one
GPU safely — each loads its own copy of YOLOv8m onto cuda:0 and they
race for queued videos through Postgres row locks. Two scripts pick a
safe replica count and apply it:

```bash
# 1. Measure GPU + per-worker VRAM, print a recommended N.
#    Requires no workers running (they'd hide free VRAM).
docker compose stop worker
./scripts/gpu_probe.sh

# 2. Apply the recommendation (or any other N).
./scripts/scale_workers.sh 6
```

`gpu_probe.sh` reads total/free VRAM via `nvidia-smi`, spawns one
ephemeral worker container that loads the actual model and runs a
warm-up inference to capture `torch.cuda.max_memory_allocated()`,
pads the result by 15 %, subtracts a 512 MiB safety margin from free
VRAM, and divides. `--json` for machine output, `--safety-margin
<MiB>` to override, `--no-measure` to skip the docker probe and use
an 800 MiB estimate.

`scale_workers.sh N` validates N, persists `WORKER_REPLICAS=N` into
`.env` for future reference, and runs
`docker compose up -d --scale worker=N worker`. Shrink back with
`./scripts/scale_workers.sh 1`.

In-flight jobs on replicas that are killed or scaled away are reaped
to `status=error` after `stale_claim_threshold_seconds` (default
900 s) + one reaper tick (~16 min total) — same path as a crashed
worker. Click **Analyze** on the row to re-queue.

## Project layout

```
api/                FastAPI service (CPU)
  app/main.py
  app/models.py        projects, videos, counting_lines
  app/schemas.py
  app/routers/         projects, videos, lines, analysis
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
  pages/0_Watched_Folder.py        auto-register videos from a mounted folder
  pages/2_Count_and_export.py      draw lines, compute, export

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
- **Line-drawing UI**: Streamlit's `streamlit-drawable-canvas` is functional
  but rough. For a polished product, swap the page for a small React app
  served from the same Cloud Run service (or its own).
- **Track interpolation**: when `FRAME_STRIDE > 1`, tracks are at sub-frame
  rate. The counting math is unaffected (segments still intersect lines), but
  visualization can look choppy. Add linear interpolation in the parquet
  read path if it matters.
