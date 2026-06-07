# timestamp_correction

Companion module for the **traffic-counter** monorepo. Detects burned-in CCTV
timestamps, maps their presence across stitched videos, finds footage gaps, and
produces **gap-corrected vehicle counts**.

```
analyzed video ──▶ locate OSD (CNN) ──▶ OCR timeline ──▶ gap map ──▶ corrected counts
```

## Stack

- **timestamp-correction-api** (`:8200`) — gap map + corrected-count endpoints
- **timestamp-correction-worker** — polls analyzed videos, writes storage artifacts

Reads the counter API and shared `/data` storage only; never touches the counter DB schema.

## Quick start

```bash
# from repo root — counter stack must be running
docker compose up --build timestamp-correction-api timestamp-correction-worker

# API docs
open http://localhost:8200/docs

# trigger a scan for one video (after counter analysis is done)
curl -X POST http://localhost:8200/videos/<video-id>/timestamp-scan

# corrected counts (same line_ids body as counter /counts)
curl -X POST http://localhost:8200/videos/<video-id>/corrected-counts \
  -H 'Content-Type: application/json' \
  -d '{"line_ids": ["..."]}'
```

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `TRAFFIC_COUNTER_API_URL` | `http://api:8000` | Counter REST base |
| `LOCAL_STORAGE_ROOT` | `/data` | Shared artifact root |
| `SAMPLE_INTERVAL_S` | `5` | Seconds between OCR samples |
| `MIN_GAP_SAMPLES` | `3` | Consecutive failed reads → gap |
| `JUMP_THRESHOLD_S` | `15` | Wall-clock jump tolerance |
| `DEVICE` | `cpu` | `RegionLocatorNet` inference device |

See [`SPEC.md`](./SPEC.md) for the full design.