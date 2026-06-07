# Timestamp Correction — Design Spec

## Problem

CCTV videos in this project are often **stitched** from shorter, sometimes disjoint clips.
The traffic-counter worker derives time as `t_seconds = frame_idx / fps`, which assumes
continuous footage. When clips are missing, duplicated, or out of order, counts aligned
to wall-clock time are wrong.

Burned-in on-screen timestamps (OSD) are the ground truth for real time. This module:

1. **Locates** the timestamp overlay region per video (neural network + OCR validation).
2. **Reads** timestamps across the video on a sampled timeline.
3. **Builds** a presence/absence map and detects **gaps** (missing footage, frozen OSD, jumps).
4. **Syncs** vehicle counts by excluding crossings that fall inside gap intervals.

## Architecture

```
traffic-counter API (analyzed videos, tracks.parquet)
        │
        ▼
timestamp-correction-worker  ──▶  shared storage artifacts
        │                              │
        │                              ├─ timestamp_region.json
        │                              ├─ timestamp_timeline.parquet
        │                              └─ timestamp_gaps.json
        ▼
timestamp-correction-api  ──▶  GET map, POST corrected-counts
```

Coupling to the counter app is **read-only** over REST + shared storage keys (same pattern
as `macromodel/`).

## Storage contract

| Key | Format | Contents |
|-----|--------|----------|
| `projects/{pid}/videos/{vid}/timestamp_region.json` | JSON | `{x, y, w, h, confidence, method}` in source pixels |
| `projects/{pid}/videos/{vid}/timestamp_timeline.parquet` | Parquet | `frame_idx, t_seconds, wall_clock_epoch, ocr_text, present` |
| `projects/{pid}/videos/{vid}/timestamp_gaps.json` | JSON | `{gaps: [{start_frame, end_frame, start_t_s, end_t_s, reason}], stats}` |
| `projects/{pid}/videos/{vid}/timestamp_status.json` | JSON | Worker job status: `pending \| processing \| done \| error` |

## Pipeline stages

### 1. Region locator (`RegionLocatorNet`)

- Sample `N` frames evenly across the video.
- Generate candidate ROIs (four corners + top/bottom strips — typical CCTV OSD placement).
- Score each patch with a small CNN (`RegionLocatorNet`, 64×128 grayscale input).
- When no trained weights exist, bootstrap by OCR success rate on candidates.
- Persist the winning bbox; reuse across re-runs unless `force=true`.

### 2. Timestamp reader

- Sample frames every `SAMPLE_INTERVAL_S` seconds (default 5 s).
- Crop the located ROI, preprocess (grayscale, contrast stretch), run EasyOCR.
- Parse common OSD formats: `YYYY-MM-DD HH:MM:SS`, `DD.MM.YYYY HH:MM:SS`, `HH:MM:SS`.
- Record `present=false` when OCR fails or parse fails.

### 3. Gap detector

Gap types:

| Reason | Detection |
|--------|-----------|
| `missing_osd` | `present=false` for ≥ `MIN_GAP_SAMPLES` consecutive samples |
| `time_jump` | Wall-clock delta differs from expected by > `JUMP_THRESHOLD_S` |
| `time_reverse` | Wall-clock decreases (stitch boundary) |
| `frozen_osd` | Same wall-clock while frame advances beyond `FROZEN_THRESHOLD_S` |

Gaps are expanded to frame ranges by interpolating between samples.

### 4. Count sync

- Load tracks parquet from shared storage (segment files merged with ID offsets).
- Drop track rows whose `frame_idx` falls inside any gap interval.
- Run the same line-crossing math as `api/app/services/counting.py`.
- Return raw counts, corrected counts, and gap statistics side by side.

## API surface (`:8200`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Liveness |
| GET | `/videos/{id}/timestamp-status` | Processing status + artifact presence |
| GET | `/videos/{id}/timestamp-map` | Region, timeline summary, gaps |
| POST | `/videos/{id}/timestamp-scan` | Queue (or run inline in dev) timestamp scan |
| POST | `/videos/{id}/corrected-counts` | `{line_ids}` → gap-filtered counts |

## Worker modes

- `WORKER_MODE=poll` — loop: find analyzed videos without `timestamp_status.json=done`
- `WORKER_MODE=single` — process `VIDEO_ID` then exit

## Future work

- Fine-tune `RegionLocatorNet` on labelled OSD crops per camera vendor.
- Project-level ROI cache (same camera model → same corner).
- Wall-clock hourly export buckets in XLSX.
- Hook into main worker finalisation (optional post-analyze step).