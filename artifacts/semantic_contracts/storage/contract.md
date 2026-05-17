# Storage Semantic Contract

Status: [DONE]

## Scope

Storage is the shared artifact layer for both the API and the worker.

## Responsibilities

- Store source uploads, generated parquet files, representative frames, and trajectory overlays.
- Provide existence checks and signed or absolute URLs where supported.
- Preserve deterministic key generation across modules.

## Key Contract

- `projects/{project_id}/videos/{video_id}/source{ext}` for the source video.
- `projects/{project_id}/videos/{video_id}/tracks.parquet` for tracks.
- `projects/{project_id}/videos/{video_id}/frame.jpg` for the representative frame.
- `projects/{project_id}/videos/{video_id}/trajectories.png` for the overlay.

## Invariants

- API and worker key generation must remain byte-for-byte identical.
- Storage backend choice is an environment concern, not a key-format concern.
