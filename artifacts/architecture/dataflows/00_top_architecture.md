# Dataflow: Top Architecture

Status: [DONE]

This diagram shows the end-to-end flow across all modules.

```mermaid
flowchart TD
    User[Operator] --> UI[frontend / Streamlit]
        UI -->|JSON over HTTP| HY[embedded React/Vite overlay]
        HY --> UI
        UI --> API[api / FastAPI]
    API -->|SQLAlchemy| DB[(PostgreSQL)]
    API -->|upload / signed URLs / parquet lookup| Store[(Storage backend)]
    API -->|enqueue analysis| WorkerTrigger[worker launcher]
    WorkerTrigger --> Worker[worker / YOLOv8 + ByteTrack]
    Worker -->|tracks.parquet, frame.jpg, trajectories.png| Store
    Worker -->|status + metadata writes| DB
    UI -->|reads summaries, videos, counts, export| API
```

## Invariants

- The UI never reads raw database state directly.
- The worker never serves operator requests.
- The API is the only module allowed to decide whether a video can transition from uploaded to queued, analyzing, analyzed, or error.
- Storage artifacts are immutable after generation unless the source video changes.
- The embedded overlay never bypasses the API when persisting counting-line changes.
