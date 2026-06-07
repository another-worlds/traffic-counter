# Timestamp Correction — Semantic Contract

Status: [DONE]

## Coupling

- **Reads** traffic-counter API (`GET /videos/{id}`, `GET /projects`, `POST /counts`).
- **Reads/writes** shared storage under `projects/{project_id}/videos/{video_id}/timestamp_*`.
- **Never writes** to the counter PostgreSQL schema.

## Artifact invariants

1. `timestamp_region.json` bbox is in **source-video pixel coordinates** (same space as counting lines).
2. `timestamp_timeline.parquet` `frame_idx` is the **absolute** frame index in the source file.
3. `timestamp_gaps.json` intervals are **half-open** `[start_frame, end_frame)` in frame indices.
4. Corrected counts **exclude** any track detection row whose `frame_idx ∈ [start, end)` for any gap.

## Count sync rule

```
corrected_tracks = tracks[~frame_idx.isin(gap_frames)]
corrected_counts = compute_counts_for_lines(corrected_tracks, lines)
```

Raw counts from the counter API are returned alongside corrected counts for comparison.

## Worker idempotency

Re-running a scan with existing `timestamp_status.json` status `done` is a no-op unless
`force=true` on `POST /timestamp-scan`.